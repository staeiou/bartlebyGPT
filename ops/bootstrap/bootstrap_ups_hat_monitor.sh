#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${OPS_DIR}/.." && pwd)"

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
  echo "[ups-hat-bootstrap] $*"
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

load_profile() {
  if [[ -z "${PROFILE_FILE:-}" ]]; then
    return
  fi
  if [[ "${PROFILE_FILE}" != /* ]]; then
    PROFILE_FILE="${REPO_ROOT}/${PROFILE_FILE#./}"
  fi
  if [[ ! -f "${PROFILE_FILE}" ]]; then
    echo "Profile file not found: ${PROFILE_FILE}" >&2
    exit 1
  fi
  log "Loading profile: ${PROFILE_FILE}"
  set -a
  # shellcheck source=/dev/null
  source "${PROFILE_FILE}"
  set +a
}

main() {
  load_profile

  local enabled="${ENABLE_UPS_HAT_LOGGER:-1}"
  if [[ "${enabled}" != "1" ]]; then
    log "ENABLE_UPS_HAT_LOGGER=${enabled}; nothing to install"
    return
  fi

  local service_name="${UPS_HAT_LOGGER_SERVICE_NAME:-ups-hat-monitor}"
  local service_user="${UPS_HAT_LOGGER_USER:-root}"
  local workdir="${UPS_HAT_LOGGER_WORKDIR:-/opt/bartleby/ups-hat-monitor}"
  local logs_dir="${UPS_HAT_LOG_DIR:-${workdir}/logs}"
  local sqlite_path="${UPS_HAT_SQLITE_PATH:-${logs_dir}/ups_hat.sqlite3}"
  local script_src="${UPS_HAT_LOGGER_SCRIPT:-${OPS_DIR}/services/ups-hat-monitor/ups_hat_monitor.py}"
  local script_name
  script_name="$(basename "${script_src}")"
  local python="${UPS_HAT_LOGGER_PYTHON:-/usr/bin/python3}"
  local i2c_bus="${UPS_HAT_I2C_BUS:-7}"
  local i2c_addr="${UPS_HAT_I2C_ADDR:-0x41}"
  local interval="${UPS_HAT_LOG_INTERVAL:-10}"

  if [[ "${script_src}" != /* ]]; then
    script_src="${REPO_ROOT}/${script_src#./}"
  fi
  if [[ ! -f "${script_src}" ]]; then
    echo "UPS HAT logger script not found: ${script_src}" >&2
    exit 1
  fi
  if [[ ! -x "${python}" ]]; then
    echo "Python interpreter not executable: ${python}" >&2
    exit 1
  fi
  if ! id -u "${service_user}" >/dev/null 2>&1; then
    echo "UPS HAT logger user does not exist: ${service_user}" >&2
    exit 1
  fi

  log "Installing ${service_name}.service"
  "${SUDO[@]}" mkdir -p "${workdir}" "${logs_dir}" "$(dirname "${sqlite_path}")"
  "${SUDO[@]}" install -m 0755 "${script_src}" "${workdir}/${script_name}"
  "${SUDO[@]}" chown -R "${service_user}:${service_user}" "${workdir}" "${logs_dir}" "$(dirname "${sqlite_path}")"

  local tmp_unit
  tmp_unit="$(mktemp)"
  template_render \
    "${OPS_DIR}/templates/systemd.ups-hat-monitor.service.tmpl" \
    "${tmp_unit}" \
    "UPS_HAT_LOGGER_USER=${service_user}" \
    "UPS_HAT_LOGGER_WORKDIR=${workdir}" \
    "UPS_HAT_LOGGER_PYTHON=${python}" \
    "UPS_HAT_LOGGER_SCRIPT=${workdir}/${script_name}" \
    "UPS_HAT_I2C_BUS=${i2c_bus}" \
    "UPS_HAT_I2C_ADDR=${i2c_addr}" \
    "UPS_HAT_LOG_INTERVAL=${interval}" \
    "UPS_HAT_LOG_DIR=${logs_dir}" \
    "UPS_HAT_SQLITE_PATH=${sqlite_path}"

  "${SUDO[@]}" cp "${tmp_unit}" "/etc/systemd/system/${service_name}.service"
  rm -f "${tmp_unit}"

  log "Verifying one INA219 read on bus ${i2c_bus}, addr ${i2c_addr}"
  "${SUDO[@]}" env \
    UPS_HAT_I2C_BUS="${i2c_bus}" \
    UPS_HAT_I2C_ADDR="${i2c_addr}" \
    UPS_HAT_LOG_DIR="${logs_dir}" \
    UPS_HAT_SQLITE_PATH="${sqlite_path}" \
    "${python}" "${workdir}/${script_name}" --once --no-write >/dev/null

  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable "${service_name}" >/dev/null
  "${SUDO[@]}" systemctl restart "${service_name}"
  log "Started ${service_name}.service"
}

main "$@"
