#!/usr/bin/env bash
# Сборка Zoom Meeting SDK native addon для vexa-lite и установка в /opt/vexa-zoom-sdk.
set -euo pipefail

ASSISTANT_ROOT="${ASSISTANT_ROOT:-/opt/assistant}"
VEXA_SRC="${VEXA_SRC:-/opt/vexa-src}"
SDK_ROOT="${SDK_ROOT:-/opt/vexa-zoom-sdk}"
WHEEL_CACHE="${SDK_ROOT}/zoom_meeting_sdk.whl"
BUILD_DIR="${SDK_ROOT}/build-tree"

_need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Нужна команда: $1" >&2
    exit 1
  }
}

_need docker
_need python3
_need curl

mkdir -p "$SDK_ROOT/libs" "$BUILD_DIR"

if [[ ! -f "$WHEEL_CACHE" ]]; then
  echo "==> Скачивание zoom-meeting-sdk (libmeetingsdk)…"
  pip3 download zoom-meeting-sdk -d "$SDK_ROOT" --no-deps
  mv "$SDK_ROOT"/zoom_meeting_sdk-*.whl "$WHEEL_CACHE"
fi

echo "==> Распаковка SDK libs…"
rm -rf "$SDK_ROOT/wheel"
mkdir -p "$SDK_ROOT/wheel"
unzip -qo "$WHEEL_CACHE" -d "$SDK_ROOT/wheel"
cp -a "$SDK_ROOT/wheel/zoom_meeting_sdk.libs/." "$SDK_ROOT/libs/"

MEETING_SO="$(find "$SDK_ROOT/libs" -name 'libmeetingsdk-*.so.*' | head -1)"
if [[ -z "$MEETING_SO" ]]; then
  echo "libmeetingsdk не найден в wheel" >&2
  exit 1
fi
cp -f "$MEETING_SO" "$SDK_ROOT/libs/libmeetingsdk.so"
ln -sf libmeetingsdk.so "$SDK_ROOT/libs/libmeetingsdk.so.1"
QT_CORE="$(find "$SDK_ROOT/libs" -name 'libQt5Core-*.so.5' | head -1)"
if [[ -n "$QT_CORE" ]]; then
  ln -sf "$(basename "$QT_CORE")" "$SDK_ROOT/libs/libQt5Core.so.5"
fi

if [[ ! -d "$VEXA_SRC/services/vexa-bot" ]]; then
  echo "==> Клонирование Vexa (binding.gyp)…"
  git clone --depth 1 https://github.com/Vexa-ai/vexa.git "$VEXA_SRC"
fi

echo "==> Подготовка дерева сборки native addon…"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/src/platforms/zoom/native/src"
mkdir -p "$BUILD_DIR/src/platforms/zoom/native/zoom_meeting_sdk/h"
cp "$VEXA_SRC/services/vexa-bot/core/src/platforms/zoom/native/src/zoom_wrapper.cpp" \
  "$BUILD_DIR/src/platforms/zoom/native/src/"
cp -a "$VEXA_SRC/services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/h/." \
  "$BUILD_DIR/src/platforms/zoom/native/zoom_meeting_sdk/h/"
cp "$SDK_ROOT/libs/libmeetingsdk.so" "$BUILD_DIR/src/platforms/zoom/native/zoom_meeting_sdk/"
ln -sf libmeetingsdk.so "$BUILD_DIR/src/platforms/zoom/native/zoom_meeting_sdk/libmeetingsdk.so.1"

cat >"$BUILD_DIR/binding.gyp" <<'GYP'
{
  "targets": [{
    "target_name": "zoom_sdk_wrapper",
    "sources": [
      "src/platforms/zoom/native/src/zoom_wrapper.cpp"
    ],
    "include_dirs": [
      "<!@(node -p \"require('node-addon-api').include\")",
      "src/platforms/zoom/native/zoom_meeting_sdk/h",
      "<!@(pkg-config --cflags-only-I Qt5Core 2>/dev/null | tr ' ' '\\n' | sed 's/^-I//')"
    ],
    "cflags!": [ "-fno-exceptions" ],
    "cflags_cc!": [ "-fno-exceptions" ],
    "cflags_cc": [ "-std=c++17", "-fexceptions" ],
    "defines": [ "NAPI_DISABLE_CPP_EXCEPTIONS", "NAPI_VERSION=7" ],
    "conditions": [[ "OS=='linux'", {
      "libraries": [
        "-L<(module_root_dir)/src/platforms/zoom/native/zoom_meeting_sdk",
        "-lmeetingsdk",
        "-lpthread",
        "<!@(pkg-config --libs Qt5Core 2>/dev/null)",
        "-Wl,-rpath,$$ORIGIN/../../src/platforms/zoom/native/zoom_meeting_sdk",
        "-Wl,-rpath,/opt/zoom-sdk/libs"
      ]
    }]]
  }]
}
GYP

cat >"$BUILD_DIR/package.json" <<'PKG'
{
  "name": "vexa-zoom-sdk-build",
  "private": true,
  "scripts": { "build:native": "node-gyp rebuild" },
  "dependencies": { "node-addon-api": "^7.1.1" },
  "devDependencies": { "node-gyp": "^12.2.0" }
}
PKG

echo "==> Сборка zoom_sdk_wrapper.node (Docker node:20-bullseye)…"
docker run --rm --platform linux/amd64 \
  -v "$BUILD_DIR:/work" \
  -v "$SDK_ROOT/libs:/opt/zoom-sdk/libs:ro" \
  -w /work \
  -e LD_LIBRARY_PATH="/opt/zoom-sdk/libs" \
  node:20-bullseye \
  bash -c '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq build-essential python3 pkg-config qtbase5-dev libssl-dev libglib2.0-0 libx11-6 libgl1-mesa-glx >/dev/null
  npm install --ignore-scripts >/dev/null
  LD_LIBRARY_PATH=/opt/zoom-sdk/libs npm run build:native
  test -f build/Release/zoom_sdk_wrapper.node
'

cp -f "$BUILD_DIR/build/Release/zoom_sdk_wrapper.node" "$SDK_ROOT/zoom_sdk_wrapper.node"
cp -f "$SDK_ROOT/libs/libmeetingsdk.so" "$SDK_ROOT/libmeetingsdk.so"

echo "==> Установка в контейнер vexa…"
if ! docker ps --format '{{.Names}}' | grep -qx vexa; then
  echo "Контейнер vexa не запущен — SDK собран в $SDK_ROOT" >&2
  exit 0
fi

docker exec vexa mkdir -p /app/build/Release /opt/zoom-sdk/libs
docker cp "$SDK_ROOT/zoom_sdk_wrapper.node" vexa:/app/build/Release/zoom_sdk_wrapper.node
docker cp "$SDK_ROOT/libs/." vexa:/opt/zoom-sdk/libs/

PATCH="${ASSISTANT_ROOT}/scripts/patch_vexa_zoom_join.sh"
if [[ -f "$PATCH" ]]; then
  bash "$PATCH"
fi

echo "==> Zoom SDK установлен:"
echo "   $SDK_ROOT/zoom_sdk_wrapper.node"
echo "   $SDK_ROOT/libmeetingsdk.so"
ls -lh "$SDK_ROOT/zoom_sdk_wrapper.node" "$SDK_ROOT/libmeetingsdk.so"
