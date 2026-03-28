#!/usr/bin/env bash
# Bootstrap solix-monitor.service on a Raspberry Pi.
# Installs solix_monitor.py, pip dependencies, and a systemd unit,
# then starts the service.
#
# Required env var:
#   SOLIX_BLE_ADDR   BLE MAC address of the Anker Solix battery for this machine
#
# Optional env vars (defaults shown):
#   SOLIX_USER         user to run the service as (default: current non-root user)
#   SOLIX_INSTALL_DIR  where to install the script (default: ~/solix-monitor)
#   SOLIX_PORT         HTTP listen port (default: 18082)
#   SOLIX_CSV_INTERVAL seconds between CSV rows (default: 60)
#   SOLIX_CAPACITY_WH  battery capacity Wh for hours-remaining calc (default: 288)
#   SERVICE_NAME       systemd unit name (default: solix-monitor)

set -euo pipefail

SUDO=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  SUDO="sudo"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ---- required ---------------------------------------------------------------
if [[ -z "${SOLIX_BLE_ADDR:-}" ]]; then
  echo "ERROR: SOLIX_BLE_ADDR is required (BLE MAC of the Solix battery)" >&2
  exit 1
fi

# ---- defaults ---------------------------------------------------------------
SOLIX_USER="${SOLIX_USER:-$(logname 2>/dev/null || echo "${SUDO_USER:-$USER}")}"
SOLIX_INSTALL_DIR="${SOLIX_INSTALL_DIR:-$(eval echo "~${SOLIX_USER}/solix-monitor")}"
SOLIX_PORT="${SOLIX_PORT:-18082}"
SOLIX_CSV_INTERVAL="${SOLIX_CSV_INTERVAL:-60}"
SOLIX_HISTORY_DB_PATH="${SOLIX_HISTORY_DB_PATH:-${SOLIX_INSTALL_DIR}/logs/history.sqlite3}"
SOLIX_CAPACITY_WH="${SOLIX_CAPACITY_WH:-288}"
SERVICE_NAME="${SERVICE_NAME:-solix-monitor}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "==> solix-monitor bootstrap"
echo "    BLE MAC:      ${SOLIX_BLE_ADDR}"
echo "    User:         ${SOLIX_USER}"
echo "    Install dir:  ${SOLIX_INSTALL_DIR}"
echo "    Service:      ${SERVICE_NAME}"

# ---- pip dependencies -------------------------------------------------------
echo "==> Installing Python dependencies..."
$SUDO pip install bleak bleak-retry-connector SolixBLE --break-system-packages -q

# ---- install script ---------------------------------------------------------
echo "==> Installing solix_monitor.py to ${SOLIX_INSTALL_DIR}..."
$SUDO mkdir -p "${SOLIX_INSTALL_DIR}/logs"
$SUDO cp "${REPO_ROOT}/ops/services/solix-monitor/solix_monitor.py" "${SOLIX_INSTALL_DIR}/solix_monitor.py"
$SUDO cp "${REPO_ROOT}/ops/history_store.py" "${SOLIX_INSTALL_DIR}/history_store.py"
$SUDO chown -R "${SOLIX_USER}:${SOLIX_USER}" "${SOLIX_INSTALL_DIR}"

# ---- systemd unit -----------------------------------------------------------
echo "==> Writing ${SERVICE_FILE}..."
$SUDO tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=Anker Solix C300X DC BLE Monitor
After=bluetooth.target network.target
Wants=bluetooth.target

[Service]
Type=simple
User=${SOLIX_USER}
WorkingDirectory=${SOLIX_INSTALL_DIR}
ExecStart=/usr/bin/python3 ${SOLIX_INSTALL_DIR}/solix_monitor.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

Environment=SOLIX_BLE_ADDR=${SOLIX_BLE_ADDR}
Environment=SOLIX_HOST=127.0.0.1
Environment=SOLIX_PORT=${SOLIX_PORT}
Environment=SOLIX_CSV_DIR=${SOLIX_INSTALL_DIR}/logs
Environment=SOLIX_CSV_INTERVAL=${SOLIX_CSV_INTERVAL}
Environment=SOLIX_HISTORY_DB_PATH=${SOLIX_HISTORY_DB_PATH}
Environment=SOLIX_CAPACITY_WH=${SOLIX_CAPACITY_WH}

[Install]
WantedBy=multi-user.target
EOF

# ---- enable + start ---------------------------------------------------------
echo "==> Enabling and starting ${SERVICE_NAME}..."
$SUDO systemctl daemon-reload
$SUDO systemctl enable "${SERVICE_NAME}"
$SUDO systemctl restart "${SERVICE_NAME}"

echo ""
echo "==> Done. Verify with:"
echo "    sudo systemctl status ${SERVICE_NAME}"
echo "    sudo journalctl -u ${SERVICE_NAME} -f"
echo "    curl -s http://127.0.0.1:${SOLIX_PORT}/solix/power | python3 -m json.tool"
echo ""
echo "    Firmware type will be auto-detected on first connect and cached to:"
echo "    ${SOLIX_INSTALL_DIR}/logs/firmware_type.txt"
