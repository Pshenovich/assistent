#!/usr/bin/env python3
"""Перенос usage_server (бэкенд миниаппа) на assistent.networ.ru.

Копирует /opt/assistant с origin (включая sqlite/токены), поднимает uvicorn :8080
и переключает nginx на localhost. Бот тоже переезжает — он пишет в те же БД.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = Path.home() / ".ssh" / "leo_server"
ORIGIN = "root@155.212.189.205"
EDGE_HOST = "91.229.90.4"
EDGE = f"root@{EDGE_HOST}"


def env_vals() -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in (ROOT / ".env").read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def run(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)


def wait_origin(deadline: float) -> bool:
    while time.time() < deadline:
        r = run(
            [
                "ssh",
                "-o",
                "IPQoS=none",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=12",
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                str(IDENTITY),
                ORIGIN,
                "echo OK",
            ],
            timeout=18,
        )
        if r.returncode == 0:
            print("origin ssh ok")
            return True
        print("origin ssh wait:", (r.stderr or r.stdout)[-120:].strip())
        time.sleep(8)
    return False


def wait_edge(pw: str, deadline: float) -> tuple[list[str], dict[str, str]] | None:
    env = os.environ.copy()
    env["SSHPASS"] = pw
    pass_cmd = [
        "sshpass",
        "-e",
        "ssh",
        "-o",
        "IPQoS=none",
        "-o",
        "PreferredAuthentications=password",
        "-o",
        "PubkeyAuthentication=no",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=12",
        EDGE,
    ]
    key_cmd = [
        "ssh",
        "-o",
        "IPQoS=none",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(IDENTITY),
        EDGE,
    ]
    while time.time() < deadline:
        for label, cmd, e in (("key", key_cmd, None), ("pass", pass_cmd, env)):
            r = run(cmd + ["echo OK"], env=e, timeout=18)
            if r.returncode == 0:
                print(f"edge ssh ok ({label})")
                return cmd, (e or os.environ.copy())
        print("edge ssh wait: closed/denied")
        time.sleep(8)
    return None


def ssh_run(prefix: list[str], env: dict[str, str] | None, script: str, timeout: int = 120) -> str:
    r = run(prefix + [script], env=env, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise SystemExit(f"ssh failed rc={r.returncode}\n{out[-4000:]}")
    return out


def main() -> int:
    vals = env_vals()
    pw = vals.get("DEPLOY_SSH_PASSWORD") or ""
    if not pw:
        print("Нет DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    print("==> ждём SSH edge, затем origin")
    edge = wait_edge(pw, time.time() + 180)
    if not edge:
        print("Нет SSH на 91.229.90.4 (часто fail2ban после прошлых попыток входа).", file=sys.stderr)
        return 2
    edge_ssh, edge_env = edge
    if not wait_origin(time.time() + 180):
        print("Нет SSH на origin 155.212.189.205 (VPN?). Нужен доступ, чтобы скопировать sqlite/токены.", file=sys.stderr)
        return 3

    origin_ssh = [
        "ssh",
        "-o",
        "IPQoS=none",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(IDENTITY),
        ORIGIN,
    ]
    origin_rsync = (
        f"ssh -o IPQoS=none -o BatchMode=yes -o ConnectTimeout=20 "
        f"-o IdentitiesOnly=yes -i {shlex.quote(str(IDENTITY))}"
    )

    print("==> подготовка edge")
    print(
        ssh_run(
            edge_ssh,
            edge_env,
            r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3.12-venv python3-pip ffmpeg fonts-dejavu-core rsync >/dev/null
mkdir -p /opt/assistant /var/www/certbot
command -v python3.12
ss -lntp | grep -E ':8080' || echo '8080 free'
""",
            timeout=180,
        )
    )

    rsync_excludes = [
        "--exclude", ".venv/",
        "--exclude", ".git/",
        "--exclude", "webapp/node_modules/",
        "--exclude", "webapp/.npm-cache/",
        "--exclude", "data/telegram-bot-api/",
        "--exclude", ".cursor/",
        "--exclude", "*.log",
        "--exclude", "__pycache__/",
        "--exclude", ".pytest_cache/",
    ]

    def rsync_origin_to_edge() -> None:
        print("==> rsync origin → edge")
        # origin dumps to stdout? use origin as rsync source via this Mac
        cmd = [
            "rsync",
            "-az",
            "--numeric-ids",
            "-e",
            origin_rsync,
            *rsync_excludes,
            "root@155.212.189.205:/opt/assistant/",
            str(ROOT / ".edge-sync/"),
        ]
        # Two-hop via local staging to avoid origin needing edge creds
        Path(ROOT / ".edge-sync").mkdir(exist_ok=True)
        r = run(cmd, timeout=600)
        if r.returncode != 0:
            raise SystemExit(f"rsync from origin failed\n{(r.stderr or r.stdout)[-3000:]}")
        print("origin copy ok", (r.stdout or "")[-200:])
        env = dict(edge_env)
        rsync_edge = [
            "sshpass",
            "-e",
            "rsync",
            "-az",
            "--numeric-ids",
            "-e",
            "ssh -o IPQoS=none -o StrictHostKeyChecking=accept-new -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=20",
            *rsync_excludes,
            str(ROOT / ".edge-sync/") + "/",
            f"root@{EDGE_HOST}:/opt/assistant/",
        ]
        # If edge ssh is key-based, don't use sshpass
        if edge_ssh[0] != "sshpass":
            rsync_edge = [
                "rsync",
                "-az",
                "--numeric-ids",
                "-e",
                f"ssh -o IPQoS=none -o BatchMode=yes -o IdentitiesOnly=yes -i {IDENTITY} -o ConnectTimeout=20",
                *rsync_excludes,
                str(ROOT / ".edge-sync/") + "/",
                f"root@{EDGE_HOST}:/opt/assistant/",
            ]
            env = None
        r = run(rsync_edge, env=env, timeout=600)
        if r.returncode != 0:
            raise SystemExit(f"rsync to edge failed\n{(r.stderr or r.stdout)[-3000:]}")
        print("edge copy ok")

    rsync_origin_to_edge()

    print("==> stop origin services")
    print(
        ssh_run(
            origin_ssh,
            None,
            "systemctl stop assistant-bot assistant-usage 2>/dev/null || true; systemctl is-active assistant-bot assistant-usage || true",
            timeout=40,
        )
    )
    rsync_origin_to_edge()

    print("==> venv + systemd on edge")
    print(
        ssh_run(
            edge_ssh,
            edge_env,
            r"""
set -euo pipefail
cd /opt/assistant
python3.12 -m venv .venv
./.venv/bin/pip install -q -U pip
./.venv/bin/pip install -q -r requirements.txt
mkdir -p assistant/assets/fonts
if [[ -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]]; then
  cp -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf assistant/assets/fonts/ 2>/dev/null || true
fi
if grep -q '^MINIAPP_DEV_MODE=1' .env 2>/dev/null; then
  sed -i 's/^MINIAPP_DEV_MODE=1/MINIAPP_DEV_MODE=0/' .env
fi
if grep -q '^TELEGRAM_PROXY_URL=socks5://127.0.0.1' .env 2>/dev/null; then
  sed -i 's/^TELEGRAM_PROXY_URL=socks5:\/\/127\.0\.0\.1/#TELEGRAM_PROXY_URL=/' .env
fi
grep -q '^WEBAPP_PUBLIC_URL=' .env && sed -i 's|^WEBAPP_PUBLIC_URL=.*|WEBAPP_PUBLIC_URL=https://assistent.networ.ru|' .env
grep -q '^APP_URL=' .env && sed -i 's|^APP_URL=.*|APP_URL=https://assistent.networ.ru|' .env || echo 'APP_URL=https://assistent.networ.ru' >> .env
cp -f deploy/assistant-usage.service /etc/systemd/system/assistant-usage.service
cp -f deploy/assistant-bot.service /etc/systemd/system/assistant-bot.service
# бот на новом хосте: без зависимости от локального telegram-bot-api, если его нет
if [[ ! -x /opt/assistant/deploy/run-telegram-bot-api.sh ]] || ! systemctl list-unit-files | grep -q telegram-bot-api; then
  sed -i 's/^After=.*/After=network.target/' /etc/systemd/system/assistant-bot.service
  sed -i '/^Wants=telegram-bot-api.service/d' /etc/systemd/system/assistant-bot.service
fi
systemctl daemon-reload
systemctl enable assistant-usage assistant-bot
systemctl restart assistant-usage
sleep 2
systemctl is-active assistant-usage
curl -sS -m 5 -o /dev/null -w "local_webapp:%{http_code}\n" http://127.0.0.1:8080/webapp/ || true
curl -sS -m 5 -o /dev/null -w "local_api:%{http_code}\n" http://127.0.0.1:8080/api/miniapp/auth/config || true
systemctl restart assistant-bot
sleep 2
systemctl is-active assistant-bot
""",
            timeout=300,
        )
    )

    print("==> nginx → localhost:8080")
    print(
        ssh_run(
            edge_ssh,
            edge_env,
            r"""
set -euo pipefail
f=/etc/nginx/sites-available/assistent.networ.ru
python3 - <<'PY'
from pathlib import Path
p = Path("/etc/nginx/sites-available/assistent.networ.ru")
t = p.read_text()
t = t.replace("proxy_pass https://assistant.obuchat.me;", "proxy_pass http://127.0.0.1:8080;")
t = t.replace("proxy_pass https://assistant.obuchat.me", "proxy_pass http://127.0.0.1:8080")
# Host must be the public name so cookies/session stay on assistent.networ.ru
t = t.replace("proxy_set_header Host assistant.obuchat.me;", "proxy_set_header Host $host;")
lines = []
skip_prefixes = ("proxy_ssl_server_name", "proxy_ssl_name")
for line in t.splitlines(True):
    if any(x in line for x in skip_prefixes):
        continue
    lines.append(line)
p.write_text("".join(lines))
print("nginx proxy now localhost")
PY
nginx -t
systemctl reload nginx
curl -sS -m 8 -o /dev/null -w "public_webapp:%{http_code}\n" https://assistent.networ.ru/webapp/
curl -sS -m 8 -o /dev/null -w "public_api:%{http_code}\n" https://assistent.networ.ru/api/miniapp/auth/config
""",
            timeout=40,
        )
    )

    print("==> origin nginx: assistant.obuchat.me → новый бэкенд (OAuth/webhook)")
    print(
        ssh_run(
            origin_ssh,
            None,
            r"""
set -euo pipefail
f=/etc/nginx/sites-available/assistant.obuchat.me
if [[ -f "$f" ]]; then
  python3 - <<'PY'
from pathlib import Path
p = Path("/etc/nginx/sites-available/assistant.obuchat.me")
t = p.read_text()
t = t.replace("proxy_pass http://127.0.0.1:8080;", "proxy_pass https://assistent.networ.ru;")
t = t.replace("proxy_pass http://127.0.0.1:8080", "proxy_pass https://assistent.networ.ru")
if "proxy_ssl_server_name" not in t and "proxy_pass https://assistent.networ.ru" in t:
    t = t.replace(
        "proxy_pass https://assistent.networ.ru;",
        "proxy_pass https://assistent.networ.ru;\n        proxy_ssl_server_name on;\n        proxy_ssl_name assistent.networ.ru;",
    )
p.write_text(t)
print("origin nginx retargeted")
PY
  nginx -t && systemctl reload nginx
fi
systemctl disable assistant-bot assistant-usage 2>/dev/null || true
echo origin_services_disabled
""",
            timeout=40,
        )
    )
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
