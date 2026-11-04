"""Лео рисует экраны на макете: когда показывать макет модели, раскладка компонентов в автолэйаут."""

from __future__ import annotations

import json

from assistant.lib import board_ai, board_doc, design_draw

LOGIN = {
    "name": "Вход", "device": "iphone", "padding": [56, 24, 32, 24], "gap": 16,
    "children": [
        {"type": "text", "text": "Вход", "size": 28, "weight": 700},
        {"type": "text", "text": "Введите e-mail и пароль, чтобы продолжить работу с заметками", "color": "muted"},
        {"type": "input", "label": "E-mail", "placeholder": "name@mail.ru"},
        {"type": "input", "label": "Пароль", "placeholder": "••••••••"},
        {"type": "link", "text": "Забыли пароль?", "align": "right"},
        {"type": "spacer"},
        {"type": "button", "text": "Войти"},
        {"type": "row", "justify": "center", "children": [
            {"type": "text", "text": "Нет аккаунта?", "color": "muted"}, {"type": "link", "text": "Регистрация"}]},
        {"type": "list", "items": [{"title": "Пункт", "subtitle": "Описание"}, {"title": "Второй"}]},
        {"type": "tabbar", "items": ["Главная", "Поиск", "Профиль"]},
    ],
}

EXISTING = {"v": 1, "mode": "design", "elements": {
    "s": {"type": "frame", "name": "Старый", "x": 0, "y": 0, "w": 393, "h": 852, "z": 1,
          "layout": "v 12 24 24 24 24 start start none"},
    "b": {"type": "rect", "frame": "s", "x": 24, "y": 24, "w": 345, "h": 48, "z": 2, "text": "Кнопка", "grow": "w",
          "style": {"fill": "#7048e8", "fontFamily": "Manrope"}},
    "t": {"type": "text", "frame": "s", "x": 24, "y": 84, "w": 345, "h": 20, "z": 3, "text": "Подпись", "grow": "w"},
}}


def test_intent_only_when_asked_about_design():
    assert design_draw.design_intent("Сделай саммари заметки") == ""
    assert design_draw.design_intent("Что упущено?") == ""
    assert design_draw.design_intent("Найди задачи и сроки") == ""
    assert design_draw.design_intent("Разбери экран по UX") == "work"
    assert design_draw.design_intent("Поменяй цвет кнопок на зелёный") == "work"
    assert design_draw.design_intent("что думаешь?", has_layers=True) == "work"
    assert design_draw.design_intent("Нарисуй экран регистрации") == "draw"
    assert design_draw.design_intent("Добавь кнопку «Забыли пароль?» под полем") == "draw"


def _contains(outer, inner, eps=0.6):
    return (inner["x"] >= outer["x"] - eps and inner["y"] >= outer["y"] - eps
            and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + eps and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + eps)


def test_screen_layout_is_valid_and_tidy():
    action = {"action": "update", "screens": [LOGIN]}
    body = board_ai.update_board(json.dumps(EXISTING), action)
    els = board_doc.parse_board(body)["elements"]
    ids = action["_relayout"]
    scr = els[ids[0]]
    assert scr["name"] == "Вход" and scr["layout"].startswith("v 16 56 24 32 24")  # раскладка прошла проверку board_doc
    assert scr["x"] >= 393 + 100 and (scr["w"], scr["h"]) == (393, 852)  # справа от старого экрана
    kids = sorted((e for e in els.values() if e.get("frame") == scr["id"]), key=lambda e: e["y"])
    assert len(kids) == len(LOGIN["children"])
    for a, b in zip(kids, kids[1:]):
        assert a["y"] + a["h"] <= b["y"] + 0.6  # колонка без наложений
    for e in els.values():  # всё нарисованное — внутри своего фрейма
        if e.get("frame") and e["frame"] in els and e["id"] not in EXISTING["elements"]:
            assert _contains(els[e["frame"]], e), (e.get("name"), e["id"])
    spacer = next(e for e in kids if e.get("name") == "Отступ")
    assert spacer["grow"] == "wh" and spacer["h"] > 50  # отступ забрал свободное место
    btn = next(e for e in kids if str(e.get("name", "")).startswith("Кнопка"))
    assert btn["style"]["fill"] == "#7048e8"  # акцент — из уже нарисованных экранов
    label = next(e for e in els.values() if e.get("frame") == btn["id"])
    assert label["style"]["fontFamily"] == "Manrope" and label["text"] == "Войти"
    assert json.loads(body)["mode"] == "design"


def test_add_into_existing_frame_after_layer():
    action = {"action": "update", "add": [{"into": "s", "after": "b", "children": [{"type": "link", "text": "Забыли пароль?"}]}]}
    body = board_ai.update_board(json.dumps(EXISTING), action)
    els = board_doc.parse_board(body)["elements"]
    new = [e for e in els.values() if e["id"] not in EXISTING["elements"]]
    assert len(new) == 1 and new[0]["frame"] == "s" and new[0]["text"] == "Забыли пароль?"
    b, t = els["b"], els["t"]
    mid = new[0]["y"] + new[0]["h"] / 2
    assert b["y"] + b["h"] / 2 < mid < t["y"] + t["h"] / 2  # в автолэйауте встанет сразу за кнопкой
    assert "s" in action["_relayout"]


def test_draw_addon_mentions_components():
    text = design_draw.draw_addon()
    for word in ("screens", "button", "input", "tabbar", "add"):
        assert word in text
