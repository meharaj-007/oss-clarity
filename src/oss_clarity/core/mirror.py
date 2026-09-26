"""A small copy of the recorded page's DOM, enough to name what a node id is.

rrweb refers to every element by a numeric id. `Mirror` rebuilds the tree from
the full snapshot and keeps it current from mutation adds and text changes, so
a click on a button that appeared after load can still be named. Removed nodes
are kept: nothing can be clicked after it is gone, and ids are not reused
within one document.

Children are kept in document order. Both the snapshot and the mutation walk
use a stack, and push children in reverse so they come off in order; every
"text just before this field" label depends on it.
"""

from __future__ import annotations

import re

from .events import NodeType

#: Characters kept in a marker's label.
LABEL_CHARS = 60

#: Clicking these is expected to change nothing visible (a caret, a native
#: picker), so a click on one is never dead. Buttons are deliberately absent:
#: a button that does nothing is exactly the dead click worth finding.
CONTROL_TAGS = frozenset({"input", "textarea", "select", "option", "label"})
CONTROL_ROLES = frozenset({"textbox", "combobox", "checkbox", "radio", "switch"})
PRESSABLE_TAGS = frozenset(
    {"a", "button", "input", "select", "textarea", "label", "summary", "option"}
)
PRESSABLE_ROLES = frozenset(
    {"button", "link", "tab", "menuitem", "checkbox", "radio", "switch", "option"}
)
FIELD_TAGS = frozenset({"input", "textarea", "select"})
NOT_FIELD_TYPES = frozenset({"hidden", "submit", "button", "reset", "image"})

#: Cookie and consent UI, by the names common banners and consent platforms use.
CONSENT_RE = re.compile(
    r"cookie|consent|gdpr|onetrust|cookiebot|didomi|usercentrics|termly|iubenda"
    r"|truste|quantcast|\bcmp\b|cmp-|cc-window|cc-banner|cky-",
    re.IGNORECASE,
)
#: How many ancestors up a consent container is looked for.
CONSENT_DEPTH = 25


class Mirror:
    """Node tags, attributes, parents, children and text, by rrweb node id."""

    def __init__(self, *, short_text_chars: int = 60) -> None:
        self.short_text_chars = short_text_chars
        self.tags: dict[int, str] = {}
        self.attrs: dict[int, dict] = {}
        self.parents: dict[int, int] = {}
        self.children: dict[int, list[int]] = {}
        self.texts: dict[int, str] = {}
        self.document_id: int | None = None
        self._by_dom_id: dict[str, int] | None = None

    # -- building ------------------------------------------------------------

    def load_snapshot(self, node: dict) -> None:
        """Add a serialised subtree (a full snapshot's `data.node`)."""
        self._walk(node, None)

    def apply_mutation(self, data: dict) -> None:
        """Apply a mutation event's `adds` and `texts`."""
        for entry in data.get("adds") or []:
            if not isinstance(entry, dict):
                continue
            node = entry.get("node")
            if isinstance(node, dict):
                # An added node carries its whole subtree inside it.
                parent = entry.get("parentId")
                self._walk(node, parent if isinstance(parent, int) else None)
        for entry in data.get("texts") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), int):
                self.texts[entry["id"]] = str(entry.get("value") or "")

    def _walk(self, root: dict, parent: int | None) -> None:
        stack: list[tuple[dict, int | None]] = [(root, parent)]
        while stack:
            current, owner = stack.pop()
            if not isinstance(current, dict):
                continue
            self._add(current, owner)
            node_id = current.get("id")
            # Reversed, so children come off the stack in document order.
            for child in reversed(current.get("childNodes") or []):
                stack.append((child, node_id if isinstance(node_id, int) else None))

    def _add(self, node: dict, parent: int | None) -> None:
        node_id = node.get("id")
        if not isinstance(node_id, int):
            return
        kind = node.get("type")
        if kind == NodeType.DOCUMENT and self.document_id is None:
            self.document_id = node_id
        if kind == NodeType.TEXT:
            self.texts[node_id] = str(node.get("textContent") or "")
        self.tags[node_id] = str(node.get("tagName") or "").lower()
        self._by_dom_id = None
        attributes = node.get("attributes")
        self.attrs[node_id] = attributes if isinstance(attributes, dict) else {}
        if isinstance(parent, int):
            self.parents[node_id] = parent
            self.children.setdefault(parent, []).append(node_id)

    # -- what a node is --------------------------------------------------------

    def _attrs(self, node_id) -> dict:
        return self.attrs.get(node_id) or {}

    def is_control(self, node_id) -> bool:
        """A form control or editable region: clicking it needs no visible answer."""
        if self.tags.get(node_id, "") in CONTROL_TAGS:
            return True
        attrs = self._attrs(node_id)
        if attrs.get("contenteditable") not in (None, "false"):
            return True
        return attrs.get("role") in CONTROL_ROLES

    def is_pressable(self, node_id) -> bool:
        attrs = self._attrs(node_id)
        return (
            self.tags.get(node_id, "") in PRESSABLE_TAGS
            or attrs.get("role") in PRESSABLE_ROLES
            or "onclick" in attrs
        )

    def pressable_ancestor(self, node_id, depth: int = 6):
        """The thing a pointer on `node_id` is on, as a visitor would name it:
        the node itself or the nearest pressable element around it."""
        current = node_id
        for _ in range(depth):
            if current is None:
                return None
            if self.is_pressable(current):
                return current
            current = self.parents.get(current)
        return None

    def element_of(self, node_id):
        """A text node's parent element; an element itself."""
        if node_id in self.texts:
            return self.parents.get(node_id)
        return node_id

    def is_leaf(self, node_id) -> bool:
        """An element holding only text, such as a price or a label, rather
        than a container of other elements that happens to read short."""
        return all(child in self.texts for child in self.children.get(node_id, []))

    def is_field(self, node_id) -> bool:
        if self.tags.get(node_id, "") not in FIELD_TAGS:
            return False
        kind = str(self._attrs(node_id).get("type") or "").lower()
        return kind not in NOT_FIELD_TYPES

    def is_submit(self, node_id) -> bool:
        """A control that sends a form: an explicit submit anywhere, or a
        `<button>` with no type inside a form, which defaults to submit."""
        tag = self.tags.get(node_id, "")
        kind = str(self._attrs(node_id).get("type") or "").lower()
        if tag == "input":
            return kind in ("submit", "image")
        if tag != "button" or kind in ("button", "reset"):
            return False
        return kind == "submit" or self._inside(node_id, "form")

    def _inside(self, node_id, tag: str, depth: int = 30) -> bool:
        current = self.parents.get(node_id)
        for _ in range(depth):
            if current is None:
                return False
            if self.tags.get(current) == tag:
                return True
            current = self.parents.get(current)
        return False

    def in_consent(self, node_id) -> bool:
        """Is this node inside a cookie or consent banner?"""
        current = node_id
        for _ in range(CONSENT_DEPTH):
            if current is None:
                return False
            attrs = self._attrs(current)
            names = " ".join(str(attrs.get(key) or "") for key in ("id", "class", "aria-label"))
            if names.strip() and CONSENT_RE.search(names):
                return True
            current = self.parents.get(current)
        return False

    # -- naming ------------------------------------------------------------------

    def text_of(self, node_id, limit: int = LABEL_CHARS * 4) -> str:
        """The visible text under a node, whitespace collapsed, as the
        visitor's browser masked it (so a masked price reads `$▫▫`)."""
        if node_id in self.texts:
            return " ".join(self.texts[node_id].split())
        parts: list[str] = []
        stack = [node_id]
        seen = 0
        while stack and seen < 200:
            current = stack.pop()
            seen += 1
            if self.tags.get(current) in ("script", "style", "noscript"):
                continue
            if current in self.texts:
                parts.append(self.texts[current])
                if sum(len(p) for p in parts) > limit:
                    break
            stack.extend(reversed(self.children.get(current, [])))
        return " ".join(" ".join(parts).split())

    def label(self, node_id) -> str:
        """What a marker on this node should read."""
        if self.tags.get(node_id) in FIELD_TAGS:
            return self.field_label(node_id)[:LABEL_CHARS]
        text = self.text_of(node_id) or str(self._attrs(node_id).get("aria-label") or "")
        return text[:LABEL_CHARS]

    def field_label(self, node_id) -> str:
        """The name a visitor saw on a field, not the one a form plugin gave
        it. In the order assistive technology uses: `aria-label`,
        `aria-labelledby`, a `<label for>`, a wrapping `<label>`; then the
        placeholder; then short text just before the field; then `name`."""
        attrs = self._attrs(node_id)
        if attrs.get("aria-label"):
            return " ".join(str(attrs["aria-label"]).split())
        by_dom_id = self._dom_ids()
        labelled = " ".join(
            self.text_of(by_dom_id[ref])
            for ref in str(attrs.get("aria-labelledby") or "").split()
            if ref in by_dom_id
        ).strip()
        if labelled:
            return labelled
        dom_id = attrs.get("id")
        if dom_id:
            for other, tag in self.tags.items():
                if tag == "label" and self._attrs(other).get("for") == dom_id:
                    text = self.text_of(other)
                    if text:
                        return text
        current = self.parents.get(node_id)
        for _ in range(3):
            if current is None:
                break
            if self.tags.get(current) == "label":
                text = self.text_of(current)
                if text:
                    return text
                break
            current = self.parents.get(current)
        if attrs.get("placeholder"):
            return " ".join(str(attrs["placeholder"]).split())
        before = self._text_before(node_id)
        if before:
            return before
        return str(attrs.get("name") or "")

    def _dom_ids(self) -> dict[str, int]:
        if self._by_dom_id is None:
            self._by_dom_id = {
                str(attrs["id"]): node for node, attrs in self.attrs.items() if attrs.get("id")
            }
        return self._by_dom_id

    def _text_before(self, node_id) -> str:
        """Short text in the sibling just before the field, or before its
        wrapper: the `<p>Phone</p><input>` a page builder writes in place of
        a `<label>`."""
        current = node_id
        for _ in range(2):
            parent = self.parents.get(current)
            if parent is None:
                return ""
            siblings = self.children.get(parent, [])
            index = siblings.index(current) if current in siblings else 0
            for sibling in reversed(siblings[:index]):
                if self._holds_field(sibling):
                    break  # Another field's block: its text belongs to that field.
                text = self.text_of(sibling)
                if text:
                    return text if len(text) <= self.short_text_chars else ""
            # Climb only out of a wrapper around this one field: the text
            # before a whole form is no single field's label.
            if any(self._holds_field(other) for other in siblings if other != current):
                return ""
            current = parent
        return ""

    def _holds_field(self, node_id, limit: int = 200) -> bool:
        stack, seen = [node_id], 0
        while stack and seen < limit:
            current = stack.pop()
            seen += 1
            if self.tags.get(current) in FIELD_TAGS:
                return True
            stack.extend(self.children.get(current, []))
        return False

    def describe(self, node_id) -> str:
        """A short CSS-like selector: `tag#id`, `tag.class1.class2` or `tag`."""
        tag = self.tags.get(node_id, "")
        if not tag:
            return ""
        attrs = self._attrs(node_id)
        if attrs.get("id"):
            return f"{tag}#{attrs['id']}"[:120]
        classes = str(attrs.get("class") or "").split()
        if classes:
            return f"{tag}.{'.'.join(classes[:2])}"[:120]
        return tag
