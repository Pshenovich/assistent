"""Публичная ссылка /share/ не отдаёт исполняемый HTML: очистка по белому списку (assistant/lib/rich_html.py)."""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.lib.rich_html import sanitize_rich_html
from assistant.stores import notes as notes_store
from assistant.stores import share_links

_ids = itertools.count(930_000, 10)


@pytest.mark.parametrize(
    "attack",
    [
        '<img src=x onerror="alert(1)">',
        '<a href="javascript:alert(1)">x</a>',
        '<a href=" jav&#x09;ascript:alert(1)">x</a>',
        '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        "<svg><animate onbegin=alert(1)>",
        '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
        "<script>alert(1)</script>",
        '<details open ontoggle="alert(1)">x</details>',
        '<input type="image" src=x onerror=alert(1)>',
        '<p style="background:url(javascript:alert(1))">x</p>',
    ],
)
def test_attacks_are_neutralized(attack):
    out = sanitize_rich_html(attack).lower()
    assert "onerror" not in out and "onbegin" not in out and "ontoggle" not in out
    assert "javascript:" not in out and "<script" not in out and "<svg" not in out and "<iframe" not in out
    assert "data:text" not in out and "url(" not in out
    assert 'type="image"' not in out


def test_editor_markup_survives():
    html = (
        '<h2>Итоги</h2><p><strong>Ж</strong> <a href="https://ya.ru">с</a></p>'
        '<ul data-type="taskList"><li data-checked="true" data-leo-item="a1">'
        '<label><input type="checkbox" checked></label><div><p>Задача</p></div></li></ul>'
        '<table><tbody><tr><th colspan="2" colwidth="120">A</th></tr>'
        '<tr><td><span style="color: red" data-leo-mark-label="x">2</span></td></tr></tbody></table>'
        '<p><img src="data:image/png;base64,iVBORw0KGgo=" alt="pic">'
        '<a href="mailto:a@b.ru">m</a><a href="tg://resolve?domain=x">tg</a></p>'
    )
    out = sanitize_rich_html(html)
    for keep in (
        "<h2>Итоги</h2>",
        'href="https://ya.ru"',
        'data-type="taskList"',
        'data-leo-item="a1"',
        'type="checkbox"',
        'colspan="2"',
        "color:red",
        'data-leo-mark-label="x"',
        'src="data:image/png;base64,iVBORw0KGgo="',
        'href="mailto:a@b.ru"',
        'href="tg://resolve?domain=x"',
    ):
        assert keep in out, keep


def test_markdown_and_plain_text_untouched():
    md = "# Заголовок\n\n> цитата & a < b\n\n- [ ] задача"
    assert sanitize_rich_html(md) == md


def test_public_share_json_returns_sanitized_body():
    owner = next(_ids)
    note = notes_store.create_note(owner, "Ловушка", '<p>текст</p><img src=x onerror="alert(1)">')
    link = share_links.create_or_get_share(str(owner), "local", str(note["id"]), "view", None)
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    resp = client.get(f"/api/public/share/{link['token']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()["body"]
    assert "<p>текст</p>" in body
    assert "onerror" not in body
