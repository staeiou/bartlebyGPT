#!/usr/bin/env bash
# Fast rebootstrap for Raspberry Pi llama.cpp + systemd service.
# Reuses existing source/model by default, optionally pulls/rebuilds,
# rewrites service config, and restarts the unit.

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

PULL_LATEST="${PULL_LATEST:-1}"
REBUILD="${REBUILD:-1}"
DOWNLOAD_MODEL_IF_MISSING="${DOWNLOAD_MODEL_IF_MISSING:-1}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

ensure_build_deps() {
  local missing=0
  for c in git cmake ninja; do
    if ! need_cmd "${c}"; then
      missing=1
    fi
  done
  if [[ "${missing}" == "0" ]]; then
    return
  fi

  echo "Installing missing build dependencies..."
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

refresh_source() {
  echo "[1/5] Preparing source checkout..."
  if [[ ! -d "${LLAMA_REPO_DIR}/.git" ]]; then
    ${SUDO} mkdir -p "$(dirname "${LLAMA_REPO_DIR}")"
    ${SUDO} git clone --depth 1 --branch "${LLAMA_REF}" "${LLAMA_REPO_URL}" "${LLAMA_REPO_DIR}"
    return
  fi

  if [[ "${PULL_LATEST}" == "1" ]]; then
    ${SUDO} git -C "${LLAMA_REPO_DIR}" fetch origin
    ${SUDO} git -C "${LLAMA_REPO_DIR}" checkout "${LLAMA_REF}"
    ${SUDO} git -C "${LLAMA_REPO_DIR}" pull --ff-only origin "${LLAMA_REF}"
  else
    echo "  Reusing existing checkout (PULL_LATEST=0)."
  fi
}

build_or_validate_binary() {
  echo "[2/5] Building or validating llama-server..."
  if [[ "${REBUILD}" == "1" ]]; then
    ensure_build_deps
    ${SUDO} cmake -S "${LLAMA_REPO_DIR}" -B "${BUILD_DIR}" -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DGGML_BLAS=ON \
      -DGGML_BLAS_VENDOR=OpenBLAS
    ${SUDO} cmake --build "${BUILD_DIR}" --target llama-server llama-bench -j"$(nproc)"
  fi

  if [[ ! -x "${LLAMA_SERVER_BIN}" ]]; then
    echo "Missing llama-server binary: ${LLAMA_SERVER_BIN}" >&2
    echo "Run full bootstrap or set REBUILD=1." >&2
    exit 1
  fi
}

ensure_model() {
  echo "[3/5] Verifying model artifact..."
  ${SUDO} mkdir -p "$(dirname "${LLAMA_MODEL}")"
  if [[ -f "${LLAMA_MODEL}" ]]; then
    echo "  Model present: ${LLAMA_MODEL}"
    return
  fi
  if [[ "${DOWNLOAD_MODEL_IF_MISSING}" != "1" ]]; then
    echo "Model missing and DOWNLOAD_MODEL_IF_MISSING=0: ${LLAMA_MODEL}" >&2
    exit 1
  fi

  echo "  Downloading missing model..."
  ${SUDO} curl -L --fail --output "${LLAMA_MODEL}" "${MODEL_URL}"
}

write_env_and_service() {
  echo "[4/5] Writing service config..."
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
  echo "[5/5] Restarting ${SERVICE_NAME}..."
  ${SUDO} systemctl daemon-reload
  ${SUDO} systemctl enable "${SERVICE_NAME}" >/dev/null
  ${SUDO} systemctl restart "${SERVICE_NAME}"

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
  echo "=== Raspberry Pi llama-server fast rebootstrap ==="
  echo "  Service: ${SERVICE_NAME}"
  echo "  Model:   ${LLAMA_MODEL}"
  echo ""

  refresh_source
  build_or_validate_binary
  ensure_model
  write_env_and_service
  restart_and_verify

  echo ""
  echo "Fast rebootstrap complete."
  echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
}

main "$@"
