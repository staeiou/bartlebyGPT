#!/usr/bin/env bash
# Install a boot-persistent systemd unit that runs the full process stack
# (run-stack.sh) for a selected deployment profile.
#
# Typical usage:
#   sudo PROFILE=eco-jetson ./ops/bootstrap/bootstrap_stack_service.sh
#   sudo PROFILE=rpi4-llama-live ./ops/bootstrap/bootstrap_stack_service.sh

set -euo pipefail

SUDO=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  SUDO="sudo"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PROFILE="${PROFILE:-}"
PROFILE_FILE="${PROFILE_FILE:-}"
if [[ -z "${PROFILE_FILE}" ]]; then
  if [[ -z "${PROFILE}" ]]; then
    echo "Set PROFILE (recommended) or PROFILE_FILE." >&2
    exit 1
  fi
  PROFILE_FILE="${REPO_ROOT}/ops/config/profiles/${PROFILE}.env"
fi

if [[ ! -f "${PROFILE_FILE}" ]]; then
  echo "Profile file not found: ${PROFILE_FILE}" >&2
  exit 1
fi

if [[ "${PROFILE_FILE}" != /* ]]; then
  PROFILE_FILE="${REPO_ROOT}/${PROFILE_FILE#./}"
fi

set -a
# shellcheck source=/dev/null
source "${PROFILE_FILE}"
set +a

SERVICE_NAME="${SERVICE_NAME:-bartleby-stack}"
SERVICE_FILE="${SERVICE_FILE:-/etc/systemd/system/${SERVICE_NAME}.service}"
ENV_FILE="${ENV_FILE:-/etc/default/${SERVICE_NAME}}"

RUN_STACK="${RUN_STACK:-${REPO_ROOT}/ops/scripts/run-stack.sh}"
WORKDIR="${WORKDIR:-${REPO_ROOT}}"

if [[ ! -x "${RUN_STACK}" ]]; then
  echo "run-stack script is not executable: ${RUN_STACK}" >&2
  exit 1
fi

STACK_MODE="process"
USE_EXISTING_INFERENCE="${USE_EXISTING_INFERENCE:-1}"
INFERENCE_BACKEND="${INFERENCE_BACKEND:-vllm}"

if [[ "${INFERENCE_BACKEND}" == "llama-server" ]]; then
  INFERENCE_SERVICE_DEFAULT="bartleby-llama.service"
else
  INFERENCE_SERVICE_DEFAULT="vllm.service"
fi
INFERENCE_SYSTEMD_UNIT="${INFERENCE_SYSTEMD_UNIT:-${INFERENCE_SERVICE_DEFAULT}}"

echo "Installing ${SERVICE_NAME} using profile: ${PROFILE_FILE}"
echo "Inference backend: ${INFERENCE_BACKEND}"
echo "Inference dependency: ${INFERENCE_SYSTEMD_UNIT}"

${SUDO} mkdir -p "$(dirname "${ENV_FILE}")"
${SUDO} tee "${ENV_FILE}" >/dev/null <<EOF
PROFILE_FILE=${PROFILE_FILE}
STACK_MODE=${STACK_MODE}
USE_EXISTING_INFERENCE=${USE_EXISTING_INFERENCE}
EOF
${SUDO} chmod 0644 "${ENV_FILE}"

${SUDO} tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=Bartleby full stack (${SERVICE_NAME})
Wants=network-online.target ${INFERENCE_SYSTEMD_UNIT}
After=network-online.target ${INFERENCE_SYSTEMD_UNIT}

[Service]
Type=simple
User=root
WorkingDirectory=${WORKDIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=/bin/bash -lc 'cd ${WORKDIR} && exec ${RUN_STACK}'
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

${SUDO} systemctl daemon-reload
${SUDO} systemctl enable "${SERVICE_NAME}" >/dev/null
${SUDO} systemctl restart "${SERVICE_NAME}"

echo
echo "Installed and started: ${SERVICE_NAME}"
echo "Check status: sudo systemctl status ${SERVICE_NAME} --no-pager -n 80"
echo "Follow logs: sudo journalctl -u ${SERVICE_NAME} -f"
