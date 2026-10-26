"""Разовая генерация 12 3D-объектов для карточек слайдов (content.cards, тема «Аналитика»).

python scripts/gen_card_objects.py [key ...]   — без аргументов генерирует все, с ключами — только их.
Картинки: webapp/canvas-kit/objects/<key>.jpg (512px) + <key>-thumb.jpg (160px).
Фон приводится к цвету карточки (--s-el #13201c) с мягким переходом к краям — без шва в карточке.
"""

from __future__ import annotations

import base64
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from PIL import Image, ImageChops, ImageDraw, ImageFilter  # noqa: E402

from assistant.integrations.openrouter_client import openrouter_generate_images  # noqa: E402

OUT = ROOT / "webapp" / "canvas-kit" / "objects"
CARD_BG = (0x13, 0x20, 0x1C)

OBJECTS = {
    "orb": "a glossy emerald glass sphere encircled by a thin tilted golden orbit ring",
    "rings": "three concentric frosted glass discs floating in a slight tilt, emerald glowing edges",
    "prism": "a clear triangular glass prism refracting a thin beam of emerald and blue light",
    "cube": "a frosted glass cube with one glowing emerald face and soft beveled edges",
    "torus": "a thick glossy emerald glass torus, tilted, with soft internal glow",
    "steps": "a row of ascending frosted glass bars like a rising chart, the tallest glowing emerald",
    "shield": "a smooth frosted glass shield with an emerald glowing rim",
    "funnel": "a sleek translucent glass funnel with small glowing emerald spheres flowing down into it",
    "wave": "a flowing ribbon of luminous emerald glass forming a smooth wave",
    "nodes": "a small cluster of glossy glass spheres connected by thin glowing emerald lines, like a network",
    "target": "a round glass target disc with concentric rings and a glowing emerald center",
    "cluster": "several floating glass cubes of different sizes, a few with emerald glowing faces",
    "hourglass": "a sleek glass hourglass with glowing emerald sand flowing through it",
    "spiral": "a smooth glossy glass spiral helix rising upward, with an emerald inner glow",
    "bubbles": "two overlapping frosted glass speech bubbles, one with a glowing emerald edge",
    "arch": "a smooth frosted glass arch standing upright like a portal, emerald light glowing from inside",
    "bolt": "a thick glossy glass lightning bolt with an emerald inner glow",
    "gear": "two interlocking frosted glass gears with emerald glowing edges",
    "pie": "a 3D pie chart made entirely of clear frosted glass slices, one slice lifted out and glowing emerald; only glass, grey metal and emerald colors, no blue, no orange",
    "coins": "a short stack of glossy glass coins, the top coin glowing emerald",
    "layers": "three small stacked floating frosted glass square plates, slightly separated, emerald glowing edges, compact object with lots of empty space on all sides",
    "puzzle": "two frosted glass puzzle pieces about to connect, one glowing emerald",
    "compass": "a small round glass compass with a plain brushed metal rim and a glowing emerald needle, the dial has no letters and no markings, the object is small with lots of empty space around it",
    "bulb": "a glass light bulb with a glowing emerald filament inside",
}

PROMPT = (
    "Studio 3D render of a single object: {desc}. The object is centered, fully visible, "
    "occupies about 60% of the frame, isolated on a plain flat very dark green background with no floor, "
    "no horizon and no other objects. Premium tech keynote style: glossy glass, frosted acrylic, brushed metal accents, "
    "emerald accent light, soft rim light, subtle reflections. Square 1:1 composition. "
    "No text, no letters, no numbers, no logos, no watermarks, no UI."
)


def _postprocess(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((512, 512), Image.LANCZOS)
    # фон картинки (по углам) → сдвигаем к цвету карточки
    px = [img.getpixel(p) for p in [(6, 6), (505, 6), (6, 505), (505, 505), (256, 6), (6, 256), (505, 256), (256, 505)]]
    bg = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
    shift = Image.new("RGB", img.size, tuple(max(0, CARD_BG[i] - bg[i]) for i in range(3)))
    neg = Image.new("RGB", img.size, tuple(max(0, bg[i] - CARD_BG[i]) for i in range(3)))
    img = ImageChops.subtract(ImageChops.add(img, shift), neg)
    # радиальная маска: центр — картинка, края — ровный цвет карточки
    mask = Image.new("L", (512, 512), 255)
    ImageDraw.Draw(mask).ellipse((40, 40, 472, 472), fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(40))
    return Image.composite(Image.new("RGB", img.size, CARD_BG), img, mask)


def gen(key: str) -> str:
    imgs = openrouter_generate_images(PROMPT.format(desc=OBJECTS[key]), n=1, timeout=240)
    if not imgs:
        return f"{key}: пусто"
    img = _postprocess(base64.b64decode(imgs[0]["b64"]))
    img.save(OUT / f"{key}.jpg", quality=84, optimize=True, progressive=True)
    img.resize((160, 160), Image.LANCZOS).save(OUT / f"{key}-thumb.jpg", quality=82, optimize=True)
    return f"{key}: ok"


def _safe(key: str) -> str:
    try:
        return gen(key)
    except Exception as e:  # noqa: BLE001
        return f"{key}: ошибка {e}"


if __name__ == "__main__":
    keys = sys.argv[1:] or list(OBJECTS)
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(_safe, keys):
            print(res, flush=True)
