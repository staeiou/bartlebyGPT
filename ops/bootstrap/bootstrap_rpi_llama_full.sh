#!/usr/bin/env bash
# Full bootstrap for Raspberry Pi llama.cpp + systemd service.
# Builds llama-server from source, downloads the Bartleby GGUF model,
# and installs/starts a persistent systemd unit.

set -euo pipefail

SUDO=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  SUDO="sudo"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PROFILE="${PROFILE:-rpi4-llama}"
PROFILE_FILE="${PROFILE_FILE:-}"
if [[ -z "${PROFILE_FILE}" && -n "${PROFILE}" ]]; then
  PROFILE_FILE="${REPO_ROOT}/ops/config/profiles/${PROFILE}.env"
fi
if [[ -n "${PROFILE_FILE}" ]]; then
  if [[ ! -f "${PROFILE_FILE}" ]]; then
    echo "Profile file not found: ${PROFILE_FILE}" >&2
    exit 1
  fi
  echo "Loading profile: ${PROFILE_FILE}"
  set -a
  # shellcheck source=/dev/null
  source "${PROFILE_FILE}"
  set +a
fi

SERVICE_NAME="${SERVICE_NAME:-bartleby-llama}"
SERVICE_FILE="${SERVICE_FILE:-/etc/systemd/system/${SERVICE_NAME}.service}"
ENV_FILE="${ENV_FILE:-/etc/default/${SERVICE_NAME}}"

WORKDIR="${WORKDIR:-${REPO_ROOT}}"
LLAMA_REPO_URL="${LLAMA_REPO_URL:-https://github.com/ggml-org/llama.cpp}"
LLAMA_REF="${LLAMA_REF:-master}"
LLAMA_REPO_DIR="${LLAMA_REPO_DIR:-/opt/llama.cpp}"
BUILD_DIR="${BUILD_DIR:-${LLAMA_REPO_DIR}/build}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-${BUILD_DIR}/bin/llama-server}"

MODEL_URL="${MODEL_URL:-https://huggingface.co/staeiou/bartleby-qwen3-1.7b_v4/resolve/main/bartleby-qwen3-1.7b_v4-Q4_K_M.gguf}"
MODEL_FILENAME="${MODEL_FILENAME:-bartleby-qwen3-1.7b_v4-Q4_K_M.gguf}"
MODEL_DIR="${MODEL_DIR:-/opt/models/bartleby}"
LLAMA_MODEL="${LLAMA_MODEL:-${MODEL_DIR}/${MODEL_FILENAME}}"

LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-8000}"
LLAMA_CTX="${LLAMA_CTX:-512}"
LLAMA_THREADS="${LLAMA_THREADS:-4}"
LLAMA_PARALLEL="${LLAMA_PARALLEL:-1}"
LLAMA_EXTRA_ARGS="${LLAMA_EXTRA_ARGS:-}"

FORCE_MODEL_DOWNLOAD="${FORCE_MODEL_DOWNLOAD:-0}"
PULL_LATEST="${PULL_LATEST:-1}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_packages() {
  if ! need_cmd apt-get; then
    echo "apt-get is required for bootstrap_rpi_llama_full.sh" >&2
    exit 1
  fi

  echo "[1/6] Installing build/runtime dependencies..."
  ${SUDO} apt-get update
  ${SUDO} env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    git \
    cmake \
    ninja-build \
    build-essential \
    libopenblas-dev
}

prepare_source() {
  echo "[2/6] Preparing llama.cpp source in ${LLAMA_REPO_DIR}..."
  if [[ -d "${LLAMA_REPO_DIR}/.git" ]]; then
    if [[ "${PULL_LATEST}" == "1" ]]; then
      ${SUDO} git -C "${LLAMA_REPO_DIR}" fetch origin
      ${SUDO} git -C "${LLAMA_REPO_DIR}" checkout "${LLAMA_REF}"
      ${SUDO} git -C "${LLAMA_REPO_DIR}" pull --ff-only origin "${LLAMA_REF}"
    else
      echo "  Reusing existing checkout (PULL_LATEST=0)."
    fi
    return
  fi

  ${SUDO} mkdir -p "$(dirname "${LLAMA_REPO_DIR}")"
  ${SUDO} git clone --depth 1 --branch "${LLAMA_REF}" "${LLAMA_REPO_URL}" "${LLAMA_REPO_DIR}"
}

build_llama() {
  echo "[3/6] Building llama-server + llama-bench (Ninja + OpenBLAS)..."
  ${SUDO} cmake -S "${LLAMA_REPO_DIR}" -B "${BUILD_DIR}" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_BLAS=ON \
    -DGGML_BLAS_VENDOR=OpenBLAS
  ${SUDO} cmake --build "${BUILD_DIR}" --target llama-server llama-bench -j"$(nproc)"

  if [[ ! -x "${LLAMA_SERVER_BIN}" ]]; then
    echo "llama-server binary not found after build: ${LLAMA_SERVER_BIN}" >&2
    exit 1
  fi
}

download_model() {
  echo "[4/6] Ensuring model exists at ${LLAMA_MODEL}..."
  ${SUDO} mkdir -p "$(dirname "${LLAMA_MODEL}")"
  if [[ -f "${LLAMA_MODEL}" && "${FORCE_MODEL_DOWNLOAD}" != "1" ]]; then
    echo "  Model already present; skipping download."
    return
  fi

  ${SUDO} curl -L --fail --output "${LLAMA_MODEL}" "${MODEL_URL}"
}

write_env_file() {
  echo "[5/6] Writing ${ENV_FILE}..."
  ${SUDO} mkdir -p "$(dirname "${ENV_FILE}")"
  ${SUDO} tee "${ENV_FILE}" >/dev/null <<EOF
LLAMA_SERVER_BIN=${LLAMA_SERVER_BIN}
LLAMA_MODEL=${LLAMA_MODEL}
LLAMA_HOST=${LLAMA_HOST}
LLAMA_PORT=${LLAMA_PORT}
LLAMA_CTX=${LLAMA_CTX}
LLAMA_THREADS=${LLAMA_THREADS}
LLAMA_PARALLEL=${LLAMA_PARALLEL}
LLAMA_EXTRA_ARGS=${LLAMA_EXTRA_ARGS}
EOF
  ${SUDO} chmod 0644 "${ENV_FILE}"
}

write_service_file() {
  echo "[6/6] Writing systemd unit ${SERVICE_FILE}..."
  ${SUDO} tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=Bartleby llama-server (Raspberry Pi)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${WORKDIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=/bin/bash -lc 'exec "\$LLAMA_SERVER_BIN" -m "\$LLAMA_MODEL" --host "\$LLAMA_HOST" --port "\$LLAMA_PORT" -c "\$LLAMA_CTX" -t "\$LLAMA_THREADS" -np "\$LLAMA_PARALLEL" \$LLAMA_EXTRA_ARGS'
Restart=on-failure
RestartSec=5
LimitNOFILE=65535
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
}

restart_and_verify() {
  echo "Reloading systemd and starting ${SERVICE_NAME}..."
  ${SUDO} systemctl daemon-reload
  ${SUDO} systemctl enable "${SERVICE_NAME}" >/dev/null
  ${SUDO} systemctl restart "${SERVICE_NAME}"

  echo "Waiting for health endpoint..."
  for _ in $(seq 1 120); do
    if curl -fsS "http://${LLAMA_HOST}:${LLAMA_PORT}/health" >/dev/null 2>&1; then
      echo "Healthy: http://${LLAMA_HOST}:${LLAMA_PORT}/health"
      curl -fsS "http://${LLAMA_HOST}:${LLAMA_PORT}/v1/models" || true
      echo ""
      return
    fi
    sleep 1
  done

  echo "Service failed to become healthy. Recent logs:" >&2
  ${SUDO} journalctl -u "${SERVICE_NAME}" -n 120 --no-pager >&2 || true
  exit 1
}

main() {
  echo "=== Raspberry Pi llama-server full bootstrap ==="
  echo "  Service: ${SERVICE_NAME}"
  echo "  Model:   ${LLAMA_MODEL}"
  echo ""

  install_packages
  prepare_source
  build_llama
  download_model
  write_env_file
  write_service_file
  restart_and_verify

  echo ""
  echo "Bootstrap complete."
  echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
}

main "$@"
