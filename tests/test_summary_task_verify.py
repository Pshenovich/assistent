import json

from assistant.nlu import llm

TASKS = [
    {"assignee": "Дима", "task": "Подготовить квартальный отчёт", "deadline": "пятница"},
    {"assignee": "Дима", "task": "Сделать отчёт за квартал", "deadline": ""},
    {"assignee": "", "task": "Обсудили проблемы ботов", "deadline": ""},
    {"assignee": "", "task": "Стартовать пилот с минимальным бюджетом", "deadline": ""},
    {"assignee": "", "task": "Кто согласует лицензии?", "deadline": ""},
]


def _fake(reply):
    calls = []

    def chat(system, user, **kw):
        calls.append(json.loads(user))
        return reply

    return chat, calls


def test_verify_sorts_candidates(monkeypatch):
    chat, calls = _fake(json.dumps({"items": [
        {"id": 1, "kind": "task"},
        {"id": 2, "kind": "duplicate", "duplicate_of": 1},
        {"id": 3, "kind": "not_task", "reason": "пересказ обсуждения"},
        {"id": 4, "kind": "decision"},
        {"id": 5, "kind": "question"},
    ]}))
    monkeypatch.setattr(llm, "_chat", chat)
    out = llm._verify_v1_tasks(TASKS, "транскрипт")
    assert [t["task"] for t in out["tasks"]] == ["Подготовить квартальный отчёт"]
    assert {d["kind"] for d in out["dropped"]} == {"duplicate", "not_task", "decision", "question"}
    assert calls[0]["candidates"][0] == {"id": 1, "task": "Подготовить квартальный отчёт", "assignee": "Дима", "deadline": "пятница"}


def test_verify_keeps_all_on_bad_reply(monkeypatch):
    monkeypatch.setattr(llm, "_chat", _fake("не json")[0])
    assert llm._verify_v1_tasks(TASKS, "т")["tasks"] == TASKS


def test_verify_never_empties_tasks(monkeypatch):
    reply = json.dumps({"items": [{"id": i, "kind": "not_task"} for i in range(1, 6)]})
    monkeypatch.setattr(llm, "_chat", _fake(reply)[0])
    assert llm._verify_v1_tasks(TASKS, "т")["tasks"] == TASKS


def test_duplicate_without_target_is_kept(monkeypatch):
    reply = json.dumps({"items": [{"id": i, "kind": "task"} for i in range(1, 5)] + [{"id": 5, "kind": "duplicate", "duplicate_of": 5}]})
    monkeypatch.setattr(llm, "_chat", _fake(reply)[0])
    assert len(llm._verify_v1_tasks(TASKS, "т")["tasks"]) == 5
