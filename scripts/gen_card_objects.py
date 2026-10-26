"""Разовая генерация 3D-объектов для карточек слайдов (content.cards) и абстракций для обложек.

python scripts/gen_card_objects.py [key ...]                 — «Аналитика»: objects/<key>.jpg
python scripts/gen_card_objects.py --theme lime --bg light   — «Лайм», белый слайд: objects-lime/<key>-light.jpg
python scripts/gen_card_objects.py --theme lime --bg dark    — «Лайм», чёрный слайд: objects-lime/<key>-dark.jpg
python scripts/gen_card_objects.py --abstracts lime [n ...]  — абстракции abstracts/lime-<n>.jpg (1..10)
Без ключей — все. Объекты 512px + thumb 160px; фон приводится к цвету карточки с мягким переходом к краям.
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

KIT = ROOT / "webapp" / "canvas-kit"
OUT = KIT / "objects"
CARD_BG = (0x13, 0x20, 0x1C)
SUFFIX = ""

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


# «Лайм»: карточка на белом и на чёрном слайде (токены --s-el в themes.css, html[data-leo-theme="lime"]).
LIME_BG = {"light": (0xF3, 0xF4, 0xEF), "dark": (0x16, 0x17, 0x15)}
LIME_PROMPT = (
    "Studio 3D render of a single object: {desc}. The object is centered, fully visible, "
    "occupies about 60% of the frame, isolated on a plain flat seamless {bg} background with no floor, "
    "no horizon and no other objects. Premium fintech keynote style: glossy and frosted glass in acid lime and "
    "fresh chartreuse green fading to pale mint, crisp white and silver accents, soft studio light, subtle reflections. "
    "Square 1:1 composition. No text, no letters, no numbers, no logos, no watermarks, no UI."
)
# Отдельные описания для «Лайма» (после согласования 2026-10-26: без эмблем, лучи и доли — лаймовые).
LIME_OVERRIDES = {
    "shield": "a smooth plain frosted glass shield with an acid lime glowing rim, completely blank surface without any emblem or symbol",
    "prism": "a clear triangular glass prism refracting a thin beam of light into soft acid lime and pale mint rays, no blue, no rainbow",
    "pie": "a 3D pie chart made of frosted glass slices in acid lime, chartreuse and pale mint shades, one lime slice lifted out",
}
LIME_BG_WORDS = {"light": "very light warm off-white", "dark": "pure matte near-black"}

LIME_ABSTRACTS = [
    "a large flowing wave of glossy translucent lime-green glass rising from the bottom right corner",
    "a soft rolling landscape of smooth bright acid lime velvet dunes with luminous highlights",
    "a twisted ribbon of frosted chartreuse glass floating diagonally",
    "a stack of rounded lime glass slabs ascending like a growth chart",
    "a smooth glossy lime-green torus partly cropped by the frame edge",
    "layered undulating sheets of lime and mint glass like a topographic relief",
    "a single liquid drop of lime glass with soft caustics",
    "a minimal arch of frosted lime glass with a glowing inner edge",
    "a single calm smooth wave of glossy lime glass gently rising on the right side, the left half of the frame empty black",
    "a sweeping curved surface of brushed lime metal with soft highlights",
]
LIME_ABSTRACT_PROMPT = (
    "Abstract 3D render for a premium fintech pitch deck slide: {desc}. Pure black background, "
    "acid lime to deep green gradient materials, glossy glass and soft velvet, cinematic soft light, "
    "generous negative space on the left for a title, wide 16:9 framing. "
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


PROMPT_FN = lambda key: PROMPT.format(desc=OBJECTS[key])  # noqa: E731


def gen(key: str) -> str:
    imgs = openrouter_generate_images(PROMPT_FN(key), n=1, timeout=240)
    if not imgs:
        return f"{key}: пусто"
    img = _postprocess(base64.b64decode(imgs[0]["b64"]))
    img.save(OUT / f"{key}{SUFFIX}.jpg", quality=84, optimize=True, progressive=True)
    img.resize((160, 160), Image.LANCZOS).save(OUT / f"{key}{SUFFIX}-thumb.jpg", quality=82, optimize=True)
    return f"{key}{SUFFIX}: ok"


def gen_abstract(n: str) -> str:
    desc = LIME_ABSTRACTS[int(n) - 1]
    imgs = openrouter_generate_images(LIME_ABSTRACT_PROMPT.format(desc=desc), n=1, timeout=240)
    if not imgs:
        return f"lime-{n}: пусто"
    img = Image.open(io.BytesIO(base64.b64decode(imgs[0]["b64"]))).convert("RGB")
    w, h = img.size
    th = int(w * 768 / 1376)  # как analytic-N: 1376×768
    if th <= h:
        img = img.crop((0, (h - th) // 2, w, (h - th) // 2 + th))
    else:
        tw = int(h * 1376 / 768)
        img = img.crop(((w - tw) // 2, 0, (w - tw) // 2 + tw, h))
    img = img.resize((1376, 768), Image.LANCZOS)
    img.save(KIT / "abstracts" / f"lime-{n}.jpg", quality=84, optimize=True, progressive=True)
    img.resize((360, 201), Image.LANCZOS).save(KIT / "abstracts" / f"lime-{n}-thumb.jpg", quality=82, optimize=True)
    return f"lime-{n}: ok"


def _safe(key: str) -> str:
    try:
        return gen_abstract(key) if key.isdigit() else gen(key)
    except Exception as e:  # noqa: BLE001
        return f"{key}: ошибка {e}"


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--abstracts"]:
        keys = args[2:] or [str(i) for i in range(1, len(LIME_ABSTRACTS) + 1)]
    elif args[:1] == ["--theme"]:
        bg = args[3]
        OUT, SUFFIX, CARD_BG = KIT / "objects-lime", f"-{bg}", LIME_BG[bg]
        PROMPT_FN = lambda key: LIME_PROMPT.format(  # noqa: E731
            desc=LIME_OVERRIDES.get(key) or OBJECTS[key].replace("emerald", "acid lime"), bg=LIME_BG_WORDS[bg])
        keys = args[4:] or list(OBJECTS)
    else:
        keys = args or list(OBJECTS)
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(_safe, keys):
            print(res, flush=True)
