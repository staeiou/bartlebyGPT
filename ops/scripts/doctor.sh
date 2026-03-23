#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROFILE="${PROFILE:-}"
PROFILE_FILE="${PROFILE_FILE:-}"
if [[ -n "${PROFILE}" && -z "${PROFILE_FILE}" ]]; then
  PROFILE_FILE="${OPS_DIR}/config/profiles/${PROFILE}.env"
fi
if [[ -n "${PROFILE_FILE}" ]]; then
  if [[ ! -f "${PROFILE_FILE}" ]]; then
    echo "Profile file not found: ${PROFILE_FILE}" >&2
    exit 1
  fi
  set -a
  # shellcheck source=/dev/null
  source "${PROFILE_FILE}"
  set +a
fi

VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8000}"
PUBLIC_PORT="${PUBLIC_PORT:-18201}"
CLOUDFLARE_PUBLIC_HOSTNAME="${CLOUDFLARE_PUBLIC_HOSTNAME:-}"
SKIP_PUBLIC_CHECK="${SKIP_PUBLIC_CHECK:-0}"

local_errors=0
local_warnings=0

ok() { echo "[ok] $*"; }
warn() { echo "[warn] $*"; local_warnings=$((local_warnings + 1)); }
fail() { echo "[fail] $*"; local_errors=$((local_errors + 1)); }

check_url() {
  local url="$1"
  local label="$2"
  if curl -fsS --max-time 8 "$url" >/dev/null 2>&1; then
    ok "$label ($url)"
    return 0
  fi
  fail "$label unreachable ($url)"
  return 1
}

echo "Doctor checks starting"
echo "- profile: ${PROFILE_FILE:-<none>}"

check_url "http://${VLLM_HOST}:${VLLM_PORT}/health" "Inference health"
check_url "http://127.0.0.1:${PUBLIC_PORT}/health" "Nginx public health"

telemetry_payload=""
if telemetry_payload="$(curl -fsS --max-time 8 "http://127.0.0.1:${PUBLIC_PORT}/telemetry/power" 2>/dev/null)"; then
  ok "Telemetry endpoint reachable"
else
  fail "Telemetry endpoint unreachable (http://127.0.0.1:${PUBLIC_PORT}/telemetry/power)"
fi

if [[ -n "${telemetry_payload}" ]]; then
  python3 - <<'PY' "${telemetry_payload}" || fail "Telemetry JSON parse failed"
import json,sys
obj=json.loads(sys.argv[1])
for key in ["power_backend","source","power_measurement_kind","estimated_total_watts","requests_running","last_error"]:
    print(f"  telemetry.{key}={obj.get(key)!r}")
PY

  expect_solix=0
  url="${TELEMETRY_ESPHOME_POWER_URL:-}"
  if [[ "$url" == *"127.0.0.1:18082"* ]]; then
    expect_solix=1
  fi
  if [[ "${EXPECT_SOLIX_FIELDS:-0}" == "1" ]]; then
    expect_solix=1
  fi

  if [[ "$expect_solix" == "1" ]]; then
    python3 - <<'PY' "${telemetry_payload}" || true
import json,sys
obj=json.loads(sys.argv[1])
missing=[]
for k in ("solix_soc_pct","solix_solar_input_w"):
    if obj.get(k) is None:
        missing.append(k)
if missing:
    print("MISSING_SOLIX:" + ",".join(missing))
PY
    if python3 - <<'PY' "${telemetry_payload}" >/dev/null 2>&1
import json,sys
obj=json.loads(sys.argv[1])
assert obj.get("solix_soc_pct") is not None
assert obj.get("solix_solar_input_w") is not None
PY
    then
      ok "Solix fields present in telemetry"
    else
      warn "Solix fields missing/null in telemetry (solix_soc_pct or solix_solar_input_w)"
    fi
  fi
fi

if [[ "${SKIP_PUBLIC_CHECK}" != "1" && -n "${CLOUDFLARE_PUBLIC_HOSTNAME}" ]]; then
  check_url "https://${CLOUDFLARE_PUBLIC_HOSTNAME}/health" "Cloudflare public health"
fi

echo "Doctor summary: errors=${local_errors}, warnings=${local_warnings}"
if (( local_errors > 0 )); then
  exit 1
fi
