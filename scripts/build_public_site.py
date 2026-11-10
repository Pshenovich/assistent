#!/usr/bin/env python3
"""Публичные страницы Orbita для зарубежного хостинга (about.getorbita.me, GitHub Pages).

Из РФ-сервера страницы видны не всем за рубежом (ответы режут на границе), а проверяющие Google
и Zoom смотрят именно оттуда. Здесь — только то, что и так открыто на getorbita.me: английский
лендинг, политика, условия, помощь по Zoom. Никаких ключей и данных пользователей.

  python3 scripts/build_public_site.py OUT_DIR

OUT_DIR — пустая папка (или прежняя сборка: файлы перезаписываются).
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = "about.getorbita.me"
APP = "https://getorbita.me"

# Токены темы вместо webapp/styles.css (юридические страницы берут только цвета и шрифт).
LEGAL_CSS = """
:root { --bg: #f6f7f9; --text: #0b0d12; --text-secondary: #5b6573; --accent: #2688eb; --border: #e3e7ec; color-scheme: light; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0b0b14; --text: #f2f3f7; --text-secondary: #a3a8b8; --accent: #529ef4; --border: #262634; color-scheme: dark; }
}
html, body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif; }
code { font-size: 0.85em; }
"""

# Ссылки, общие для всех страниц: в приложение и на русскую главную — на основной домен.
COMMON = [
    ('href="/webapp/"', f'href="{APP}/webapp/"'),
    ('href="https://getorbita.me/en"', f'href="https://{DOMAIN}/"'),
]


def _strip_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->\s*", "", html, flags=re.S)


def _apply(html: str, pairs: list[tuple[str, str]]) -> str:
    for a, b in pairs:
        html = html.replace(a, b)
    return html


def build_landing() -> str:
    html = (ROOT / "docs" / "orbita-en.html").read_text(encoding="utf-8")
    html = _apply(html, COMMON + [
        ('href="/docs/fonts/unbounded-latin-wght.woff2"', 'href="/fonts/unbounded-latin-wght.woff2"'),
        ('url("/docs/fonts/unbounded-latin-wght.woff2")', 'url("/fonts/unbounded-latin-wght.woff2")'),
        ('href="/en"', 'href="/"'),
        ('href="/privacy#en"', 'href="/privacy/#en"'),
        ('href="/terms#en"', 'href="/terms/#en"'),
        ('href="/zoom"', 'href="/zoom/"'),
        ('href="/" hreflang="ru"', f'href="{APP}/" hreflang="ru"'),
        ('href="https://getorbita.me/" hreflang="ru"', f'href="{APP}/" hreflang="ru"'),
        ('<meta name="robots" content="noindex">\n', ''),
    ])
    html = html.replace('<link rel="alternate" hreflang="en" href="https://getorbita.me/en">',
                        f'<link rel="alternate" hreflang="en" href="https://{DOMAIN}/">')
    return _strip_comments(html)


def build_legal(name: str) -> str:
    html = (ROOT / "webapp" / "legal" / f"orbita-{name}.html").read_text(encoding="utf-8")
    html = _apply(html, COMMON + [
        ('<link rel="stylesheet" href="../styles.css" />', f"<style>{LEGAL_CSS}</style>"),
        ('href="../index.html"', 'href="/"'),
        ('href="orbita-privacy.html#en"', 'href="/privacy/#en"'),
        ('href="orbita-privacy.html"', 'href="/privacy/"'),
        ('href="/privacy"', 'href="/privacy/"'),
        ('href="/terms"', 'href="/terms/"'),
    ])
    return _strip_comments(html)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    pages = {
        "index.html": build_landing(),
        "privacy/index.html": build_legal("privacy"),
        "terms/index.html": build_legal("terms"),
        "zoom/index.html": build_legal("zoom"),
    }
    for rel, html in pages.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
    (out / "fonts").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "docs" / "fonts" / "unbounded-latin-wght.woff2", out / "fonts" / "unbounded-latin-wght.woff2")
    shutil.copyfile(ROOT / "docs" / "fonts" / "UNBOUNDED-LICENSE.txt", out / "fonts" / "UNBOUNDED-LICENSE.txt")
    (out / "CNAME").write_text(DOMAIN + "\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print("\n".join(sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and ".git" not in p.parts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
