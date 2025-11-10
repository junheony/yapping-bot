"""Hash utilities."""

import hashlib


def url_hash(url: str) -> str:
    """Return md5 hash of a URL string."""

    return hashlib.md5(url.encode("utf-8"), usedforsecurity=False).hexdigest()

