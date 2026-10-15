"""Арт-дирекшен для картинок презентаций: к описанию (от модели или пользователя)
добавляем стиль оформления деки, чтобы картинки выглядели дорого и в одном ключе.

Сравнение моделей и промптов (2026-10-15) показало: «abstract teal shapes» даёт
дешёвый клипарт на любой модели, а описание + арт-дирекшен — студийный кадр даже
на базовой модели. Коды цветов (#0c1412) в промпт не кладём — модели печатают их на картинке.
"""

from __future__ import annotations

import re
from typing import Optional

_STYLE = {
    "analytic": (
        "Art direction: premium tech-company keynote visual. Deep dark-green environment, emerald accent light, "
        "glossy glass, frosted acrylic and brushed metal materials, soft volumetric rim light, subtle film grain, "
        "shallow depth of field, cinematic composition with generous negative space, wide 16:9 framing."
    ),
    "mono": (
        "Art direction: high-end investor-deck visual. Strict black-and-white palette with a pale ice-blue as the only accent, "
        "matte ceramic and paper materials, soft diffused studio light, large calm shapes, Swiss minimalism, "
        "lots of empty space, wide 16:9 framing."
    ),
}
_NO_TEXT = "No text, no letters, no numbers, no logos, no watermarks, no UI."
_MARK = "Art direction:"


def styled_prompt(prompt: str, theme: Optional[str] = None) -> str:
    """Описание + стиль деки + запрет текста. Повторно стиль не добавляем."""
    text = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if not text or _MARK in text:
        return text
    style = _STYLE.get(str(theme or "").strip().lower(), _STYLE["analytic"])
    return f"{text.rstrip('.')}. {style} {_NO_TEXT}"


def deck_theme_of_html(html: str) -> Optional[str]:
    m = re.search(r'data-leo-theme="([a-z0-9_-]+)"', html or "")
    return m.group(1) if m else None
