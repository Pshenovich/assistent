#!/usr/bin/env python3
"""Mirror the public news digest from the legacy origin into this app.

This is a migration bridge for /news/: the new public host serves files from
./news, while the legacy generator is still only reachable at assistant.obuchat.me.
"""

from __future__ import annotations

import os
import re
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NEWS_DIR = ROOT / "news"
ARCHIVE_DIR = NEWS_DIR / "archive"
DEFAULT_ORIGIN_URL = "https://assistant.obuchat.me/news/"


def _fetch(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ObuchatNewsMigrator/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _digest_date(html: str) -> str:
    patterns = (
        r"дайджест за\s+(\d{2})\.(\d{2})\.(\d{4})",
        r"Обновлено:\s+(\d{2})\.(\d{2})\.(\d{4})",
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            day, month, year = match.groups()
            return f"{year}-{month}-{day}"
    raise RuntimeError("Could not determine digest date from HTML")


def _validate(html: str) -> None:
    required = ("Обучат", "отраслевой дайджест", "Watchlist")
    missing = [marker for marker in required if marker not in html]
    if missing:
        raise RuntimeError(f"Origin response does not look like news HTML: {missing}")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        delete=False,
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def main() -> None:
    url = (os.getenv("NEWS_ORIGIN_URL") or DEFAULT_ORIGIN_URL).strip()
    html = _fetch(url)
    _validate(html)
    digest_date = _digest_date(html)
    _write_atomic(NEWS_DIR / "index.html", html)
    _write_atomic(ARCHIVE_DIR / f"{digest_date}.html", html)
    print(f"synced {url} -> /news/ ({digest_date})")


if __name__ == "__main__":
    main()
