"""Тема веб-приложения: хранится в настройках пользователя, выбор пока только для UI_THEME_USERS."""

from __future__ import annotations

import asyncio

import pytest

import assistant.stores.user_prefs as up


def test_ui_theme_default_system_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))
    assert up.get_ui_theme(7) == "system"
    assert up.set_ui_theme(7, " Light ") == "light"
    assert up.get_ui_theme(7) == "light"
    with pytest.raises(ValueError):
        up.set_ui_theme(7, "sepia")
    assert up.get_ui_theme(7) == "light"


def test_ui_theme_enabled_by_id_username_or_star(monkeypatch):
    import usage_server as us

    p = us._MiniappPrincipal(42, {"username": "Vova"})
    monkeypatch.delenv("UI_THEME_USERS", raising=False)
    assert us._ui_theme_enabled(p) is False
    monkeypatch.setenv("UI_THEME_USERS", "1, 42")
    assert us._ui_theme_enabled(p) is True
    monkeypatch.setenv("UI_THEME_USERS", "@vova")
    assert us._ui_theme_enabled(p) is True
    monkeypatch.setenv("UI_THEME_USERS", "other")
    assert us._ui_theme_enabled(p) is False
    monkeypatch.setenv("UI_THEME_USERS", "*")
    assert us._ui_theme_enabled(p) is True


def test_ui_theme_state_and_put(tmp_path, monkeypatch):
    import usage_server as us
    from assistant.realtime import notify

    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(notify, "publish", lambda topic, ev: sent.append((topic, ev)))
    p = us._MiniappPrincipal(42, {"username": "vova"})

    monkeypatch.delenv("UI_THEME_USERS", raising=False)
    assert us._ui_theme_state(p) == {"enabled": False, "pref": "dark"}
    with pytest.raises(us.HTTPException) as e:
        asyncio.run(us.miniapp_profile_theme_put(us._MiniappThemeBody(theme="light"), p))
    assert e.value.status_code == 403

    monkeypatch.setenv("UI_THEME_USERS", "42")
    assert us._ui_theme_state(p) == {"enabled": True, "pref": "system"}
    r = asyncio.run(us.miniapp_profile_theme_put(us._MiniappThemeBody(theme="light"), p))
    assert r == {"ok": True, "pref": "light"}
    assert us._ui_theme_state(p) == {"enabled": True, "pref": "light"}
    assert sent and sent[-1][1] == {"type": "prefs.changed", "ui_theme": "light"}
    with pytest.raises(us.HTTPException) as e:
        asyncio.run(us.miniapp_profile_theme_put(us._MiniappThemeBody(theme="sepia"), p))
    assert e.value.status_code == 400


def _css_literals_outside_tokens(css: str) -> list[str]:
    import re

    css = re.sub(r'^(:root|html\[data-theme="light"\])\s*\{.*?^\}', "", css, flags=re.S | re.M)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    cols = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^()]*\)", css)
    black = re.compile(r"^(#000(000)?|rgba?\(\s*0\s*,\s*0\s*,\s*0\b.*)$", re.I)
    return [c for c in cols if not black.match(c)]


def test_styles_use_theme_tokens_not_literal_colors():
    """Цвета экранов — через токены темы (светлая/тёмная). Чёрные тени не считаем; остальное — осознанные исключения
    (белый лист письма, бегунки переключателей, цветные метки). Новый литерал — заведите токен в :root и в светлой теме."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "webapp" / "styles.css").read_text(encoding="utf-8")
    left = _css_literals_outside_tokens(css)
    assert len(left) <= 50, f"{len(left)} литеральных цветов вне токенов, например: {left[-10:]}"
