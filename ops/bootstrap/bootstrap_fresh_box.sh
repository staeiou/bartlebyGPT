#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${OPS_DIR}/.." && pwd)"

PROFILE=""
PROFILE_FILE=""
SECRETS_FILE=""
FORCE_SOLIX_MONITOR=""
SKIP_CLOUDFLARED="0"
SKIP_DOCTOR="0"
SKIP_INFERENCE_BOOTSTRAP="0"

usage() {
  cat <<USAGE
Usage:
  sudo ./ops/bootstrap/bootstrap_fresh_box.sh --profile <name> [options]

Options:
  --profile <name>           Profile name from ops/config/profiles (e.g. api-jetson)
  --profile-file <path>      Explicit profile env file path
  --secrets-file <path>      Optional env file for secrets (e.g. CLOUDFLARE_TUNNEL_TOKEN)
  --force-solix-monitor      Force install/enable solix-monitor.service
  --skip-inference-bootstrap Do not attempt inference bootstrap if health is down
  --skip-cloudflared         Do not install/configure cloudflared.service
  --skip-doctor              Skip post-bootstrap doctor checks
  -h, --help                 Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --profile-file)
      PROFILE_FILE="$2"
      shift 2
      ;;
    --secrets-file)
      SECRETS_FILE="$2"
      shift 2
      ;;
    --force-solix-monitor)
      FORCE_SOLIX_MONITOR="1"
      shift
      ;;
    --skip-inference-bootstrap)
      SKIP_INFERENCE_BOOTSTRAP="1"
      shift
      ;;
    --skip-cloudflared)
      SKIP_CLOUDFLARED="1"
      shift
      ;;
    --skip-doctor)
      SKIP_DOCTOR="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${PROFILE_FILE}" ]]; then
  if [[ -z "${PROFILE}" ]]; then
    echo "Set --profile or --profile-file" >&2
    usage
    exit 1
  fi
  PROFILE_FILE="${OPS_DIR}/config/profiles/${PROFILE}.env"
fi

if [[ "${PROFILE_FILE}" != /* ]]; then
  PROFILE_FILE="${REPO_ROOT}/${PROFILE_FILE#./}"
fi

if [[ ! -f "${PROFILE_FILE}" ]]; then
  echo "Profile file not found: ${PROFILE_FILE}" >&2
  exit 1
fi

if [[ -n "${SECRETS_FILE}" && "${SECRETS_FILE}" != /* ]]; then
  SECRETS_FILE="${REPO_ROOT}/${SECRETS_FILE#./}"
fi

if [[ -n "${SECRETS_FILE}" && ! -f "${SECRETS_FILE}" ]]; then
  echo "Secrets file not found: ${SECRETS_FILE}" >&2
  exit 1
fi

SUDO=()
if [[ "${EUID}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
  else
    echo "This script requires root (or sudo)." >&2
    exit 1
  fi
fi

log() {
  echo "[fresh-bootstrap] $*"
}

run_with_profile() {
  if [[ "${#SUDO[@]}" -gt 0 ]]; then
    "${SUDO[@]}" env PROFILE_FILE="${PROFILE_FILE}" "$@"
  else
    env PROFILE_FILE="${PROFILE_FILE}" "$@"
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_base_packages() {
  if ! need_cmd apt-get; then
    echo "apt-get not found; install dependencies manually." >&2
    exit 1
  fi

  log "Installing base packages"
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    python3 \
    python3-venv \
    python3-pip \
    nginx \
    bluez
}

install_cloudflared_package() {
  if need_cmd cloudflared; then
    return
  fi

  log "Installing cloudflared package"

  local keyring="/usr/share/keyrings/cloudflare-main.gpg"
  local list_file="/etc/apt/sources.list.d/cloudflared.list"
  local codename=""

  if [[ -r /etc/os-release ]]; then
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"
  fi
  if [[ -z "${codename}" ]]; then
    codename="stable"
  fi

  "${SUDO[@]}" mkdir -p /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | "${SUDO[@]}" gpg --dearmor -o "${keyring}"
  echo "deb [signed-by=${keyring}] https://pkg.cloudflare.com/cloudflared ${codename} main" | "${SUDO[@]}" tee "${list_file}" >/dev/null

  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y cloudflared
}

install_cloudflared_service() {
  local token="$1"

  if [[ -z "${token}" ]]; then
    log "No CLOUDFLARE_TUNNEL_TOKEN provided; skipping managed cloudflared.service setup"
    return
  fi

  if [[ "${SKIP_CLOUDFLARED}" == "1" ]]; then
    log "Skipping cloudflared setup by request"
    return
  fi

  install_cloudflared_package

  local service_file="/etc/systemd/system/cloudflared.service"

  log "Installing cloudflared.service"
  "${SUDO[@]}" tee "${service_file}" >/dev/null <<SERVICE
[Unit]
Description=cloudflared
After=network-online.target
Wants=network-online.target

[Service]
TimeoutStartSec=15
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run --token ${token}
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
SERVICE

  "${SUDO[@]}" chmod 0600 "${service_file}"
  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable cloudflared >/dev/null
  "${SUDO[@]}" systemctl restart cloudflared
}

choose_solix_user() {
  local candidate="${SOLIX_MONITOR_USER:-}"
  if [[ -n "${candidate}" ]] && id -u "${candidate}" >/dev/null 2>&1; then
    echo "${candidate}"
    return
  fi

  if [[ -n "${SUDO_USER:-}" ]] && id -u "${SUDO_USER}" >/dev/null 2>&1; then
    echo "${SUDO_USER}"
    return
  fi

  if id -u ubuntu >/dev/null 2>&1; then
    echo "ubuntu"
    return
  fi

  echo "root"
}

template_render() {
  local src="$1"
  local dst="$2"
  shift 2

  local sed_expr=()
  local pair
  for pair in "$@"; do
    local key="${pair%%=*}"
    local value="${pair#*=}"
    value="${value//|/\\|}"
    sed_expr+=("-e" "s|{{${key}}}|${value}|g")
  done

  sed "${sed_expr[@]}" "${src}" > "${dst}"
}

install_battery_monitor() {
  local should_install="0"
  local esphome_url="${TELEMETRY_ESPHOME_POWER_URL:-}"

  # Accept both ENABLE_BATTERY_MONITOR (new) and ENABLE_SOLIX_MONITOR (legacy).
  if [[ "${FORCE_SOLIX_MONITOR}" == "1" \
     || "${ENABLE_BATTERY_MONITOR:-0}" == "1" \
     || "${ENABLE_SOLIX_MONITOR:-0}" == "1" ]]; then
    should_install="1"
  elif [[ "${TELEMETRY_POWER_BACKEND:-}" == "esphome" && "${esphome_url}" == *"127.0.0.1:18082"* ]]; then
    should_install="1"
  fi

  if [[ "${should_install}" != "1" ]]; then
    log "Battery monitor not required for this profile"
    return
  fi

  local monitor_user
  monitor_user="$(choose_solix_user)"

  # BATTERY_MONITOR_SCRIPT: path to the monitor script in the repo.
  # Defaults to solix_monitor.py for backwards compatibility.
  local monitor_src="${BATTERY_MONITOR_SCRIPT:-${OPS_DIR}/services/solix-monitor/solix_monitor.py}"
  local monitor_script_name
  monitor_script_name="$(basename "${monitor_src}")"

  local service_name="${BATTERY_MONITOR_SERVICE_NAME:-solix-monitor}"
  local workdir="${SOLIX_MONITOR_WORKDIR:-/opt/bartleby/solix-monitor}"
  local logs_dir="${SOLIX_CSV_DIR:-${workdir}/logs}"
  local history_db_path="${SOLIX_HISTORY_DB_PATH:-${logs_dir}/history.sqlite3}"
  local ble_addr="${SOLIX_BLE_ADDR:-}"
  local host="${SOLIX_HOST:-127.0.0.1}"
  local port="${SOLIX_PORT:-18082}"
  local csv_interval="${SOLIX_CSV_INTERVAL:-60}"
  local capacity_wh="${SOLIX_CAPACITY_WH:-288}"
  local reconnect_delay="${SOLIX_RECONNECT_DELAY:-10}"
  local victron_addr="${VICTRON_BLE_ADDR:-}"
  local victron_key="${VICTRON_ENCRYPTION_KEY:-}"
  local venv="${SOLIX_MONITOR_VENV:-/opt/bartleby-solix-venv}"

  if [[ -z "${ble_addr}" ]]; then
    echo "SOLIX_BLE_ADDR is required when installing battery monitor; refusing to fall back to a default MAC." >&2
    exit 1
  fi

  log "Installing battery monitor service (${service_name})"

  "${SUDO[@]}" mkdir -p "${workdir}" "${logs_dir}" "$(dirname "${history_db_path}")"
  "${SUDO[@]}" cp "${monitor_src}" "${workdir}/${monitor_script_name}"
  "${SUDO[@]}" cp "${OPS_DIR}/history_store.py" "${workdir}/history_store.py"
  "${SUDO[@]}" chmod 0755 "${workdir}/${monitor_script_name}"
  "${SUDO[@]}" chown -R "${monitor_user}:${monitor_user}" "${workdir}" "${logs_dir}" "$(dirname "${history_db_path}")"

  if [[ ! -x "${venv}/bin/python" ]]; then
    "${SUDO[@]}" python3 -m venv "${venv}"
  fi
  "${SUDO[@]}" "${venv}/bin/pip" install --upgrade pip
  "${SUDO[@]}" "${venv}/bin/pip" install --upgrade bleak bleak-retry-connector
  if ! "${SUDO[@]}" "${venv}/bin/pip" install --upgrade SolixBLE; then
    log "SolixBLE not available for this venv interpreter; continuing in TLV-only mode"
  fi
  if ! "${SUDO[@]}" "${venv}/bin/pip" install --upgrade victron-ble; then
    log "victron-ble not available for this venv interpreter; Victron data will be absent"
  fi

  local tmp_unit
  tmp_unit="$(mktemp)"
  template_render \
    "${OPS_DIR}/templates/systemd.battery-monitor.service.tmpl" \
    "${tmp_unit}" \
    "BATTERY_MONITOR_USER=${monitor_user}" \
    "BATTERY_MONITOR_WORKDIR=${workdir}" \
    "BATTERY_MONITOR_PYTHON=${venv}/bin/python" \
    "BATTERY_MONITOR_SCRIPT=${workdir}/${monitor_script_name}" \
    "BLE_ADDR=${ble_addr}" \
    "MONITOR_HOST=${host}" \
    "MONITOR_PORT=${port}" \
    "MONITOR_CSV_DIR=${logs_dir}" \
    "MONITOR_CSV_INTERVAL=${csv_interval}" \
    "MONITOR_HISTORY_DB_PATH=${history_db_path}" \
    "MONITOR_CAPACITY_WH=${capacity_wh}" \
    "MONITOR_RECONNECT_DELAY=${reconnect_delay}" \
    "VICTRON_BLE_ADDR=${victron_addr}" \
    "VICTRON_ENCRYPTION_KEY=${victron_key}"

  "${SUDO[@]}" cp "${tmp_unit}" "/etc/systemd/system/${service_name}.service"
  rm -f "${tmp_unit}"

  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable "${service_name}" >/dev/null
  "${SUDO[@]}" systemctl restart "${service_name}"
}

install_bartleby_stack_service() {
  log "Installing/restarting bartleby-stack service"
  run_with_profile "${OPS_DIR}/bootstrap/bootstrap_stack_service.sh"
}

inference_health_url() {
  local host="${VLLM_HOST:-127.0.0.1}"
  local port="${VLLM_PORT:-8000}"
  echo "http://${host}:${port}/health"
}

public_health_url() {
  local port="${PUBLIC_PORT:-18201}"
  echo "http://127.0.0.1:${port}/health"
}

telemetry_health_url() {
  local port="${PUBLIC_PORT:-18201}"
  echo "http://127.0.0.1:${port}/telemetry/power"
}

inference_is_healthy() {
  curl -fsS --max-time 8 "$(inference_health_url)" >/dev/null 2>&1
}

resolve_vllm_wheel_path() {
  local wheel_glob="${WHEEL_GLOB:-/home/ubuntu/vllm-build/dist/vllm-*.whl}"
  local wheel_path="${VLLM_WHEEL_PATH:-}"
  local wheel_url="${VLLM_WHEEL_URL:-}"
  local wheel_sha256="${VLLM_WHEEL_SHA256:-}"
  local cache_dir="${VLLM_WHEEL_CACHE_DIR:-/tmp/bartleby-wheels}"

  if [[ -n "${wheel_path}" && -f "${wheel_path}" ]]; then
    echo "${wheel_path}"
    return 0
  fi

  wheel_path="$(ls -1t ${wheel_glob} 2>/dev/null | head -n 1 || true)"
  if [[ -n "${wheel_path}" && -f "${wheel_path}" ]]; then
    echo "${wheel_path}"
    return 0
  fi

  if [[ -n "${wheel_url}" ]]; then
    local wheel_name
    wheel_name="$(basename "${wheel_url%%\?*}")"
    if [[ "${wheel_name}" != *.whl ]]; then
      wheel_name="vllm-jetson.whl"
    fi
    wheel_path="${cache_dir}/${wheel_name}"

    mkdir -p "${cache_dir}"
    log "Downloading vLLM wheel from VLLM_WHEEL_URL -> ${wheel_path}"
    curl -fL --retry 3 --connect-timeout 10 --max-time 1800 -o "${wheel_path}" "${wheel_url}"

    if [[ -n "${wheel_sha256}" ]]; then
      echo "${wheel_sha256}  ${wheel_path}" | sha256sum -c - >/dev/null
    fi

    if [[ -f "${wheel_path}" ]]; then
      echo "${wheel_path}"
      return 0
    fi
  fi

  return 1
}

bootstrap_inference_service() {
  local backend="${INFERENCE_BACKEND:-vllm}"
  local script="${INFERENCE_BOOTSTRAP_SCRIPT:-}"

  if [[ "${SKIP_INFERENCE_BOOTSTRAP}" == "1" ]]; then
    log "Inference bootstrap disabled by --skip-inference-bootstrap"
    return
  fi

  if [[ -z "${script}" ]]; then
    if [[ "${backend}" == "llama-server" ]]; then
      script="${OPS_DIR}/bootstrap/bootstrap_rpi_llama_full.sh"
    elif [[ "${backend}" == "vllm" ]]; then
      script="${OPS_DIR}/bootstrap/bootstrap_jetson_full.sh"
    else
      echo "Unsupported INFERENCE_BACKEND for bootstrap: ${backend}" >&2
      exit 1
    fi
  fi

  if [[ "${script}" != /* ]]; then
    script="${REPO_ROOT}/${script#./}"
  fi
  if [[ ! -x "${script}" ]]; then
    echo "Inference bootstrap script not executable: ${script}" >&2
    exit 1
  fi

  if [[ "${backend}" == "vllm" ]]; then
    # Jetson vLLM bootstrap requires a wheel artifact (local path or URL download).
    local wheel_path=""
    if ! wheel_path="$(resolve_vllm_wheel_path)"; then
      local wheel_glob="${WHEEL_GLOB:-/home/ubuntu/vllm-build/dist/vllm-*.whl}"
      echo "vLLM wheel artifact not found." >&2
      echo "Checked local glob: ${wheel_glob}" >&2
      echo "Set one of: VLLM_WHEEL_PATH, VLLM_WHEEL_URL (optional VLLM_WHEEL_SHA256)." >&2
      exit 1
    fi
    export VLLM_WHEEL_PATH="${wheel_path}"
  fi

  log "Bootstrapping inference service using ${script}"
  run_with_profile "${script}"
}

ensure_inference_ready() {
  if inference_is_healthy; then
    log "Inference already healthy at $(inference_health_url)"
    return
  fi

  log "Inference is not healthy; attempting bootstrap"
  bootstrap_inference_service

  local attempts=90
  while (( attempts > 0 )); do
    if inference_is_healthy; then
      log "Inference healthy after bootstrap: $(inference_health_url)"
      return
    fi
    attempts=$((attempts - 1))
    sleep 2
  done

  echo "Inference did not become healthy after bootstrap: $(inference_health_url)" >&2
  exit 1
}

wait_for_public_health() {
  local attempts=30
  while (( attempts > 0 )); do
    if curl -fsS --max-time 5 "$(public_health_url)" >/dev/null 2>&1; then
      log "Public health is ready at $(public_health_url)"
      return
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  log "Public health still not ready at $(public_health_url); doctor will report details"
}

wait_for_telemetry_health() {
  local attempts=30
  while (( attempts > 0 )); do
    if curl -fsS --max-time 5 "$(telemetry_health_url)" >/dev/null 2>&1; then
      log "Telemetry is ready at $(telemetry_health_url)"
      return
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  log "Telemetry still not ready at $(telemetry_health_url); doctor will report details"
}

run_doctor() {
  if [[ "${SKIP_DOCTOR}" == "1" ]]; then
    log "Skipping doctor checks by request"
    return
  fi

  log "Running doctor checks"
  run_with_profile "${OPS_DIR}/scripts/doctor.sh"
}

main() {
  log "Using profile: ${PROFILE_FILE}"

  set -a
  # shellcheck source=/dev/null
  source "${PROFILE_FILE}"
  if [[ -n "${SECRETS_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${SECRETS_FILE}"
  fi
  set +a

  install_base_packages
  install_cloudflared_service "${CLOUDFLARE_TUNNEL_TOKEN:-}"
  ensure_inference_ready
  install_battery_monitor
  install_bartleby_stack_service
  wait_for_public_health
  wait_for_telemetry_health
  run_doctor

  log "Done"
  echo
  echo "Next commands:"
  echo "  sudo systemctl status bartleby-stack --no-pager -n 80"
  echo "  sudo systemctl status cloudflared --no-pager -n 80"
  echo "  sudo systemctl status solix-monitor --no-pager -n 80"
}

main "$@"
