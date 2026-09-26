"""Which visits are recorded: a hash of the session id against a percentage.

Every page of a visit gets the same answer with no shared state, and the
collector can recompute the tracker's answer, so a chunk from a visit outside
the sample cannot vouch for itself. The tracker runs the same hash in
JavaScript; `tests/vectors/sampling.json` pins both to the same results.
"""

from __future__ import annotations

import re

#: Ids the tracker mints: ASCII letters, digits and dashes. Anything else is
#: refused before hashing, so hashing characters and hashing bytes agree.
ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def fnv1a32(value: str) -> int:
    """32-bit FNV-1a over the string's characters."""
    h = 0x811C9DC5
    for char in value:
        h ^= ord(char)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def session_is_sampled(session_id: str, sample_pct: int) -> bool:
    """Whether a visit with this session id is recorded at `sample_pct` %."""
    pct = max(0, min(int(sample_pct or 0), 100))
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    return fnv1a32(session_id) % 100 < pct
