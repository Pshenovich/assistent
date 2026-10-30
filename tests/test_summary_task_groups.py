"""Группы задач встречи от Лео: модель предлагает, код проверяет, человек решает."""

from __future__ import annotations

from unittest import mock

from assistant.nlu import llm


def test_clean_groups_validates_model_answer():
    raw = [
        {"title": "Запустить лендинг", "parent": 1, "items": [1, 2, 3]},
        {"title": "Пересекается", "items": [3, 4]},  # 3 уже в группе → одна задача, не группа
        {"title": "Чужие номера", "items": [0, 99]},
        {"title": "", "parent": None, "items": [5, 6]},  # без названия и без основной
        {"title": "Договор с Альфой", "parent": None, "items": ["5", 6]},
        "мусор",
    ]
    got = llm.clean_task_groups(raw, 6)
    assert got == [
        {"title": "Запустить лендинг", "parent": 0, "items": [0, 1, 2]},
        {"title": "Договор с Альфой", "parent": None, "items": [4, 5]},
    ]


def test_clean_groups_caps_count():
    raw = [{"title": f"g{i}", "items": [2 * i + 1, 2 * i + 2]} for i in range(8)]
    assert len(llm.clean_task_groups(raw, 20)) == llm.MAX_TASK_GROUPS


def test_group_tasks_marks_items():
    tasks = [{"task": "Запустить лендинг"}, {"task": "Тексты"}, {"task": "Домен"}, {"task": "Договор"}]
    with mock.patch.object(llm, "_chat", return_value='{"groups":[{"title":"Запустить лендинг","parent":1,"items":[2,3]}]}'):
        groups = llm.group_tasks(tasks)
    meta = llm.mark_task_groups(tasks, groups)
    assert meta == [{"key": "g1", "title": "Запустить лендинг", "state": "suggested"}]
    assert [t.get("group_key") for t in tasks] == ["g1", "g1", "g1", None]
    assert tasks[0]["group_parent"] is True


def test_group_tasks_failure_means_no_groups():
    with mock.patch.object(llm, "_chat", return_value="не json"):
        assert llm.group_tasks([{"task": "a"}, {"task": "b"}, {"task": "c"}]) == []


def test_focus_stack_collapses_group():
    import usage_server

    items = [
        {"key": "k1", "status": "new", "text": "Тексты", "source": {"item": "i1", "id": 5}},
        {"key": "k2", "status": "new", "text": "Отдельное", "source": {"item": "i2", "id": 5}},
        {"key": "k3", "status": "new", "text": "Домен", "source": {"item": "i3", "id": 5}},
    ]
    groups = [{"id": "g1", "title": "Запустить лендинг", "parent": "", "items": ["i1", "i3"], "note_id": 5}]
    out = usage_server._focus_group_items(items, groups)
    assert [x["key"] for x in out] == ["grp:5:g1", "k2"]
    assert out[0]["text"] == "2 задачи → «Запустить лендинг»"
    assert [m["key"] for m in out[0]["group"]["members"]] == ["k1", "k3"]
