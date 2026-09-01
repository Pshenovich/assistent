#!/usr/bin/env bash
# Патчи vexa-lite: безопасный authenticated-режим без сломанного MinIO (http://).
set -euo pipefail

if ! docker ps --format '{{.Names}}' | grep -qx vexa; then
  echo "Контейнер vexa не запущен" >&2
  exit 1
fi

docker exec -i vexa python3 <<'PY'
from pathlib import Path

meetings = Path("/app/meeting-api/meeting_api/meetings.py")
text = meetings.read_text(encoding="utf-8")
old = """    if req.authenticated:
        minio_endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
        minio_secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
        s3_endpoint_url = f"{'https' if minio_secure else 'http'}://{minio_endpoint}"
        s3_bucket = os.environ.get("MINIO_BUCKET", "vexa-recordings")
        bot_config["authenticated"] = True
        bot_config["userdataS3Path"] = f"users/{current_user.id}/browser-userdata"
        bot_config["s3Endpoint"] = s3_endpoint_url
        bot_config["s3Bucket"] = s3_bucket
        bot_config["s3AccessKey"] = os.environ.get("MINIO_ACCESS_KEY", "")
        bot_config["s3SecretKey"] = os.environ.get("MINIO_SECRET_KEY", "")"""
new = """    if req.authenticated:
        bot_config["authenticated"] = True
        minio_endpoint = (os.environ.get("MINIO_ENDPOINT") or "").strip()
        if minio_endpoint:
            minio_secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
            s3_endpoint_url = f"{'https' if minio_secure else 'http'}://{minio_endpoint}"
            s3_bucket = os.environ.get("MINIO_BUCKET", "vexa-recordings")
            bot_config["userdataS3Path"] = f"users/{current_user.id}/browser-userdata"
            bot_config["s3Endpoint"] = s3_endpoint_url
            bot_config["s3Bucket"] = s3_bucket
            bot_config["s3AccessKey"] = os.environ.get("MINIO_ACCESS_KEY", "")
            bot_config["s3SecretKey"] = os.environ.get("MINIO_SECRET_KEY", "")"""
if old not in text:
    if "minio_endpoint = (os.environ.get(\"MINIO_ENDPOINT\") or \"\").strip()" in text and "if req.authenticated:" in text:
        print("meetings.py already patched")
    else:
        raise SystemExit("meetings.py: блок authenticated не найден — проверьте версию vexa")
else:
    meetings.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("meetings.py patched")

s3 = Path("/app/vexa-bot/dist/s3-sync.js")
s3text = s3.read_text(encoding="utf-8")
needle = "function s3Sync(localDir, s3Path, config, direction, excludes = []) {"
if "function isValidS3Endpoint" not in s3text:
    insert = """function isValidS3Endpoint(endpoint) {
    if (!endpoint || typeof endpoint !== 'string')
        return false;
    try {
        const u = new URL(endpoint);
        return Boolean(u.hostname);
    }
    catch (_e) {
        return false;
    }
}
"""
    if needle not in s3text:
        raise SystemExit("s3-sync.js: s3Sync не найден")
    s3text = s3text.replace(needle, insert + needle, 1)
    s3text = s3text.replace(
        "if (!config.userdataS3Path || !config.s3Endpoint || !config.s3Bucket)",
        "if (!config.userdataS3Path || !isValidS3Endpoint(config.s3Endpoint) || !config.s3Bucket)",
        1,
    )
    s3.write_text(s3text, encoding="utf-8")
    print("s3-sync.js patched")
else:
    print("s3-sync.js already patched")

text = meetings.read_text(encoding="utf-8")
if 'bot_config["obfToken"]' in text:
    print("meetings.py obfToken already patched")
else:
    anchor = "    if req.authenticated:"
    if anchor in text:
        text = text.replace(
            anchor,
            """    zoom_obf = (getattr(req, "zoom_obf_token", None) or "").strip()
    if zoom_obf and req.platform.value == "zoom":
        bot_config["obfToken"] = zoom_obf

""" + anchor,
            1,
        )
        meetings.write_text(text, encoding="utf-8")
        print("meetings.py obfToken patched")
    else:
        print("meetings.py obfToken: пропуск (не найден anchor)")

text = meetings.read_text(encoding="utf-8")
zoom_env_old = """    if req.platform.value == "zoom":
        if os.getenv("ZOOM_WEB", "").strip() == "true":
            env_vars["ZOOM_WEB"] = "true"
        if os.getenv("ZOOM_SDK", "").strip() == "true":
            env_vars["ZOOM_SDK"] = "true"
            zoom_cid = os.getenv("ZOOM_CLIENT_ID")
            zoom_csec = os.getenv("ZOOM_CLIENT_SECRET")
            if zoom_cid and zoom_csec:
                env_vars["ZOOM_CLIENT_ID"] = zoom_cid
                env_vars["ZOOM_CLIENT_SECRET"] = zoom_csec"""
zoom_env_new = """    if req.platform.value == "zoom":
        if os.getenv("ZOOM_WEB", "").strip() == "true":
            env_vars["ZOOM_WEB"] = "true"
        use_zoom_sdk = (
            os.getenv("ZOOM_SDK", "").strip() == "true"
            or bool((getattr(req, "zoom_obf_token", None) or "").strip())
            or os.path.exists("/app/build/Release/zoom_sdk_wrapper.node")
        )
        if use_zoom_sdk:
            env_vars["ZOOM_SDK"] = "true"
            zoom_cid = os.getenv("ZOOM_CLIENT_ID")
            zoom_csec = os.getenv("ZOOM_CLIENT_SECRET")
            if zoom_cid and zoom_csec:
                env_vars["ZOOM_CLIENT_ID"] = zoom_cid
                env_vars["ZOOM_CLIENT_SECRET"] = zoom_csec
            libs_path = "/opt/zoom-sdk/libs"
            if os.path.isdir(libs_path):
                cur = os.environ.get("LD_LIBRARY_PATH", "")
                env_vars["LD_LIBRARY_PATH"] = f"{libs_path}:{cur}" if cur else libs_path"""
if zoom_env_new.strip() in text:
    print("meetings.py ZOOM_SDK env already patched")
elif zoom_env_old in text:
    meetings.write_text(text.replace(zoom_env_old, zoom_env_new, 1), encoding="utf-8")
    print("meetings.py ZOOM_SDK env patched")
else:
    print("meetings.py ZOOM_SDK env: пропуск")

sdk = Path("/app/vexa-bot/dist/platforms/zoom/sdk-manager.js")
sdktext = sdk.read_text(encoding="utf-8")
sdk_old = """        const now = Math.floor(Date.now() / 1000);
        const payload = Buffer.from(JSON.stringify({
            appKey: clientId,
            iat: now,
            exp: now + 86400, // 24 hours
            tokenExp: now + 86400
        })).toString('base64url');"""
sdk_new = """        const now = Math.floor(Date.now() / 1000) - 30;
        const exp = now + 7200;
        const payload = Buffer.from(JSON.stringify({
            appKey: clientId,
            sdkKey: clientId,
            iat: now,
            exp: exp,
            tokenExp: exp
        })).toString('base64url');"""
if "sdkKey: clientId" in sdktext:
    print("sdk-manager.js JWT already patched")
elif sdk_old in sdktext:
    sdk.write_text(sdktext.replace(sdk_old, sdk_new, 1), encoding="utf-8")
    print("sdk-manager.js JWT patched")
else:
    print("sdk-manager.js JWT: пропуск")
PY

echo "==> Vexa zoom join patches applied"
