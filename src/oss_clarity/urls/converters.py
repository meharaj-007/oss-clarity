from __future__ import annotations

from ..models import PUBLIC_KEY_PATTERN
from ..tracker import DIGEST_LENGTH


class PublicKeyConverter:
    regex = PUBLIC_KEY_PATTERN

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


class DigestConverter:
    regex = f"[0-9a-f]{{{DIGEST_LENGTH}}}"

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value
