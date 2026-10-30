"""Каталог онбординга сходится с интерфейсом: каждый шаг тура и каждая функция подсвечивают реальный элемент."""

from __future__ import annotations

import re
from pathlib import Path

from assistant import onboarding

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "webapp"


def _anchors() -> set[str]:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    found = set(re.findall(r'data-tour="([a-z0-9-]+)"', html))
    found |= set(re.findall(r'setAttribute\("data-tour", "([a-z0-9-]+)"\)', js))
    found |= set(re.findall(r'data-tour="([a-z0-9-]+)"', js))  # в html-строках app.js
    return found


def _tabs() -> set[str]:
    js = (WEB / "app.js").read_text(encoding="utf-8")
    m = re.search(r"TAB_ORDER = \[([^\]]+)\]", js)
    assert m
    return set(re.findall(r'"([a-z]+)"', m.group(1)))


def test_feature_fields_and_unique_ids():
    feats = onboarding.features()
    ids = [f["id"] for f in feats]
    assert len(ids) == len(set(ids))
    for f in feats:
        for key in ("title", "section", "summary", "where", "how", "route", "anchor"):
            assert f.get(key), (f["id"], key)
        assert "hint" in f, f["id"]


def test_every_anchor_exists_in_ui():
    anchors = _anchors()
    cat = onboarding.catalog()
    for step in cat["tour"]:
        assert step["anchor"] in anchors, step["id"]
        if step.get("open_menu"):
            assert step["open_menu"] in anchors, step["id"]
    for f in cat["features"]:
        assert f["anchor"] in anchors, f["id"]
        if f.get("menu_anchor"):
            assert f["menu_anchor"] in anchors, f["id"]


def test_screen_intros_have_text_and_real_anchors():
    anchors = _anchors()
    screens = onboarding.catalog()["screens"]
    assert {s["id"] for s in screens} >= {"saved", "note", "leo_chat", "digest", "knowledge", "profile"}
    for sc in screens:
        assert sc["title"] and sc["text"] and sc["anchors"], sc["id"]
        for a in sc["anchors"]:
            assert a in anchors, (sc["id"], a)


def test_routes_point_to_real_tabs():
    tabs = _tabs()
    for f in onboarding.features():
        assert f["route"]["tab"] in tabs, f["id"]


def test_tour_has_five_steps_and_intro_texts():
    cat = onboarding.catalog()
    assert len(cat["tour"]) == 5
    assert "Лео" in cat["intro"]["greeting"]
    assert cat["team"]["title"]
