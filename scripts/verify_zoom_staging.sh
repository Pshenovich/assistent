#!/usr/bin/env bash
# Verify Zoom OAuth redirect URLs for Marketplace (prod + staging).
set -euo pipefail

check() {
  local label="$1"
  local url="$2"
  local expect="${3:-200}"
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' "$url" || echo "000")"
  if [[ "$code" == "$expect" ]]; then
    echo "OK  $label  $url  HTTP $code"
  else
    echo "FAIL $label  $url  HTTP $code (expected $expect)" >&2
    return 1
  fi
}

fail=0
check "prod home" "https://assistant.obuchat.me/zoom/home" 200 || fail=1
check "prod oauth callback (no params)" "https://assistant.obuchat.me/oauth/zoom/callback" 400 || fail=1
check "staging home" "https://stagassistant.obuchat.me/zoom/home" 200 || fail=1
check "staging oauth callback (no params)" "https://stagassistant.obuchat.me/oauth/zoom/callback" 400 || fail=1
check "test plan" "https://assistant.obuchat.me/docs/zoom-test-plan.html" 200 || fail=1

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "All Zoom staging/prod endpoints OK."
