from datetime import datetime, timedelta, timezone

import pytest

from assistant.services import mail_inbox, mail_source, mail_sync
from assistant.stores import mail_accounts, mail_index, mail_senders

ME = 100
MINE = "vova@gmail.com"
NOW = datetime.now(timezone.utc)


class FakeImap:
    """Минимальный IMAP: запоминает команды, папка на запись — по флагу writable."""

    def __init__(self, writable=True, caps=("IMAP4REV1", "MOVE", "UIDPLUS")):
        self.writable = writable
        self.capabilities = caps
        self.calls = []
        self.selected = None

    def select(self, folder, readonly=False):
        import imaplib

        if not readonly and not self.writable:
            raise imaplib.IMAP4.readonly(f"{folder} is not writable")
        self.selected = folder
        return "OK", [b"1"]

    def response(self, name):
        return name, [b"1"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
                      b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"']

    def uid(self, cmd, *args):
        self.calls.append((cmd, self.selected) + args)
        if cmd == "SEARCH":
            return "OK", [b"77"]
        if cmd == "FETCH":
            return "OK", [b"1 (UID 11 FLAGS (\\Seen))", b"2 (UID 12 FLAGS ())"]
        return "OK", [b""]

    def logout(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.services import focus_items, mail_memory, proactive_ai
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    for mod in (notes_store, focus_items, proactive_ai, mail_index, mail_accounts, mail_senders, mail_inbox, mail_memory):
        mod._CONN = None
    mail_accounts._FERNET = None
    acc = mail_accounts.upsert(ME, provider="gmail", email=MINE, auth="password", secret="x")
    fake = {"imap": FakeImap()}
    monkeypatch.setattr(mail_source, "open_imap", lambda uid, a: fake["imap"])
    for i, (key, uidn) in enumerate([("a", 11), ("b", 12)]):
        mail_index.upsert(ME, {"mail_key": key, "account_id": acc["account_id"], "folder": "INBOX", "uid": uidn,
                               "direction": "in", "from_addr": "ivan@firm.ru", "to": [MINE], "subject": "Договор",
                               "thread_key": "t1", "message_id": f"<{key}>", "snippet": "x",
                               "date_utc": (NOW - timedelta(hours=5 - i)).isoformat()})
    return {"acc": acc, "fake": fake}


def test_mark_seen_writes_flags(env):
    assert mail_sync.mark_seen(ME, ["a", "b"], True) == 2
    cmd = env["fake"]["imap"].calls[-1]
    assert cmd[0] == "STORE" and cmd[1] == "INBOX" and cmd[2] == "11,12" and cmd[3] == "+FLAGS.SILENT"
    assert mail_accounts.get(ME, env["acc"]["account_id"])["can_write"] is True


def test_no_write_rights_remembered(env):
    env["fake"]["imap"] = FakeImap(writable=False)
    assert mail_sync.mark_seen(ME, ["a"], True) == 0
    assert mail_accounts.get(ME, env["acc"]["account_id"])["can_write"] is False
    assert mail_sync.mark_seen(ME, ["a"], True) == 0 and env["fake"]["imap"].calls == []  # больше не пытаемся


def test_pull_seen_marks_read_in_orbita(env):
    rows = [mail_index.get(ME, "a"), mail_index.get(ME, "b")]
    assert mail_sync.pull_seen(ME, env["acc"], env["fake"]["imap"], "INBOX", rows) == 1
    assert mail_inbox.read_keys(ME) == {"a"}


def test_spam_moves_and_restore_returns(env):
    res = mail_sync.spam_thread(ME, "t1", "ivan@firm.ru")
    assert res["moved"] == 2 and res["pattern"] == "ivan@firm.ru"
    move = [c for c in env["fake"]["imap"].calls if c[0] == "MOVE"][0]
    assert move[2] == "11,12" and move[3] == '"[Gmail]/Spam"'
    rule = mail_senders.list_rules(ME)[0]
    assert rule["rule"] == "block" and len(rule["moved"]) == 2
    assert mail_sync.restore_moved(ME, rule["moved"]) == 1  # FakeImap находит один UID по Message-ID
    back = [c for c in env["fake"]["imap"].calls if c[0] == "MOVE"][-1]
    assert back[1] == '"[Gmail]/Spam"' and back[3] == '"INBOX"'


def test_move_without_move_capability_copies_and_flags(env):
    env["fake"]["imap"] = FakeImap(caps=("IMAP4REV1",))
    mail_sync.spam_thread(ME, "t1", "ivan@firm.ru")
    cmds = [c[0] for c in env["fake"]["imap"].calls]
    assert "COPY" in cmds and "STORE" in cmds and "EXPUNGE" not in cmds  # без UIDPLUS — не стираем чужое
