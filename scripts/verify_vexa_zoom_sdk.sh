#!/usr/bin/env bash
# Проверка Zoom SDK в контейнере vexa: файлы, загрузка addon, JWT auth.
set -euo pipefail

if ! docker ps --format '{{.Names}}' | grep -qx vexa; then
  echo "Контейнер vexa не запущен" >&2
  exit 1
fi

echo "==> Файлы SDK"
docker exec vexa ls -lh /app/build/Release/zoom_sdk_wrapper.node
docker exec vexa test -d /opt/zoom-sdk/libs && docker exec vexa ls /opt/zoom-sdk/libs/libQt5Core.so.5

echo "==> Env"
docker exec vexa printenv ZOOM_SDK | sed 's/^/ZOOM_SDK=/'
docker exec vexa printenv ZOOM_CLIENT_ID | sed 's/^\(.\{4\}\).*/ZOOM_CLIENT_ID=\1***/'

echo "==> Require addon (путь sdk-manager.js)"
docker exec vexa bash -c 'LD_LIBRARY_PATH=/opt/zoom-sdk/libs node -e "
const p=require(\"path\").resolve(\"/app/vexa-bot/dist/platforms/zoom\",\"../../../../build/Release/zoom_sdk_wrapper\");
require(p);
console.log(\"addon ok\", p);
"'

echo "==> SDK authenticate (код 11 = неверный JWT / Meeting SDK не включён в Marketplace)"
docker exec vexa bash -c 'LD_LIBRARY_PATH=/opt/zoom-sdk/libs node -e "
const crypto=require(\"crypto\");
const id=process.env.ZOOM_CLIENT_ID; const sec=process.env.ZOOM_CLIENT_SECRET;
const h=Buffer.from(JSON.stringify({alg:\"HS256\",typ:\"JWT\"})).toString(\"base64url\");
const now=Math.floor(Date.now()/1000);
const pl=Buffer.from(JSON.stringify({appKey:id,sdkKey:id,iat:now,exp:now+7200,tokenExp:now+7200})).toString(\"base64url\");
const sig=crypto.createHmac(\"sha256\",sec).update(h+\".\"+pl).digest(\"base64url\");
const jwt=h+\".\"+pl+\".\"+sig;
const sdk=new (require(\"/app/build/Release/zoom_sdk_wrapper\")).ZoomSDK();
sdk.initialize({domain:\"https://zoom.us\",enableLog:false,logSize:1});
sdk.onAuthResult(r=>{console.log(\"auth\",JSON.stringify(r));process.exit(r.success?0:1);});
sdk.authenticate({jwt});
setTimeout(()=>{console.log(\"auth timeout\");process.exit(2);},20000);
"' || true
