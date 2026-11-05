"""scripts/nginx_perf_patch.py: правка живых nginx-конфигов идемпотентна и не трогает WebSocket и редиректы."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("nginx_perf_patch", ROOT / "scripts" / "nginx_perf_patch.py")
npp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(npp)

OLD = (
    subprocess.run(
        ["git", "show", "71e8d88:deploy/nginx-getorbita.me.conf"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    or (ROOT / "deploy" / "nginx-getorbita.me.conf").read_text(encoding="utf-8")
)


def test_patch_applies_once_and_keeps_ws_unbuffered():
    new, changes = npp.patch(OLD)
    again, more = npp.patch(new)
    assert again == new and more == []
    assert "text/javascript" in new and "client_max_body_size 64m;" in new
    assert "client_max_body_size 520m;" in new and "client_max_body_size 60m;" in new
    ws = new[new.index("location = /api/miniapp/ws") :]
    assert "proxy_buffering off;" in ws[: ws.index("}")]
    main = new[new.rindex("    location / {") :]
    assert "proxy_buffering on;" in main
    assert new.count("{") == new.count("}")
    www = new[: new.index("server_name getorbita.me;")]
    assert "leo-perf" not in www  # редирект www не тронут


def test_repo_configs_already_patched():
    for name in ("nginx-getorbita.me.conf", "nginx-assistent.networ.ru.conf"):
        text = (ROOT / "deploy" / name).read_text(encoding="utf-8")
        assert npp.patch(text)[1] == [], name
