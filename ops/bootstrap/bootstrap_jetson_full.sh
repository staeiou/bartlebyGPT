#!/usr/bin/env bash
# Bootstrap native vLLM on Jetson Orin Nano (BnB + AWQ)
# Tested: L4T R36.4.7, CUDA 12.6, SM87, /opt/vllm-venv
# Usage:
#   sudo ./ops/bootstrap/bootstrap_jetson_full.sh            # AWQ service (default)
#   sudo VLLM_MODE=bnb ./ops/bootstrap/bootstrap_jetson_full.sh  # BnB service
#   sudo BUILD_BNB=0 ./ops/bootstrap/bootstrap_jetson_full.sh    # skip BnB rebuild

set -euo pipefail

SUDO=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  SUDO="sudo"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

VENV="/opt/vllm-venv"
PYTHON_BIN="${VENV}/bin/python3"
UV_BIN="${UV_BIN:-}"
if [[ -z "${UV_BIN}" ]]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  elif [[ -x /home/ubuntu/.local/bin/uv ]]; then
    UV_BIN="/home/ubuntu/.local/bin/uv"
  else
    UV_BIN="uv"
  fi
fi
SERVICE_FILE="/etc/systemd/system/vllm.service"
RULE_FILE="/etc/udev/rules.d/99-nvidia-jetson.rules"
POWER_RULE='SUBSYSTEM=="nvidia-gpu-v2-power", GROUP="video", MODE="0660"'
WORKDIR="${WORKDIR:-${REPO_ROOT}}"

# AWQ model (primary)
AWQ_MODEL="${AWQ_MODEL:-staeiou/bartleby-qwen3-1.7b_v4-awq}"
# BnB model (safetensors)
BNB_MODEL="${BNB_MODEL:-staeiou/bartleby-qwen3-1.7b_v4}"
# Which mode to write into systemd service: awq (default) or bnb
VLLM_MODE="${VLLM_MODE:-awq}"

WHEEL_GLOB="${WHEEL_GLOB:-/home/ubuntu/vllm-build/dist/vllm-*.whl}"

BNB_VERSION="${BNB_VERSION:-0.49.2}"
BNB_REPO="${BNB_REPO:-https://github.com/bitsandbytes-foundation/bitsandbytes.git}"
BNB_BUILD_DIR="${BNB_BUILD_DIR:-/tmp/bitsandbytes-src}"
BNB_ARCHS="${BNB_ARCHS:-87-real;90-virtual}"
BUILD_BNB="${BUILD_BNB:-1}"
BNB_LIB_DST="${VENV}/lib/python3.10/site-packages/bitsandbytes/libbitsandbytes_cuda126.so"

LD_PATH="/usr/local/cuda-12.6/compat:\
${VENV}/lib/python3.10/site-packages/nvidia/cu12/lib:\
${VENV}/lib/python3.10/site-packages/torch/lib:\
${VENV}/lib/python3.10/site-packages/nvidia/nvjitlink/lib:\
${VENV}/lib/python3.10/site-packages/nvidia/cuda_nvrtc/lib:\
/usr/lib/aarch64-linux-gnu/nvidia:\
/usr/local/cuda/targets/aarch64-linux/lib"

# --- Preflight ---

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "ERROR: Missing Python venv at ${PYTHON_BIN}" >&2
  exit 1
fi

if ! command -v "${UV_BIN}" >/dev/null 2>&1; then
  echo "ERROR: Missing uv at ${UV_BIN}. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

WHEEL_PATH="${VLLM_WHEEL_PATH:-}"
if [[ -z "${WHEEL_PATH}" ]]; then
  WHEEL_PATH="$(ls -1t ${WHEEL_GLOB} 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "${WHEEL_PATH}" || ! -f "${WHEEL_PATH}" ]]; then
  echo "ERROR: No source-built vLLM wheel found. Set VLLM_WHEEL_PATH explicitly." >&2
  exit 1
fi

echo "=== Jetson vLLM Bootstrap ==="
echo "  Mode:   ${VLLM_MODE}"
echo "  Wheel:  ${WHEEL_PATH}"
echo "  BnB:    build=${BUILD_BNB} version=${BNB_VERSION}"
echo ""

# --- Step 0: nvpmodel super mode (25W) ---
echo "[0/7] Enabling nvpmodel super mode (25W / MAXN_SUPER)..."
SUPER_CONF="/etc/nvpmodel/nvpmodel_p3767_0003_super.conf"
NVPMODEL_CONF="/etc/nvpmodel.conf"
if [[ -f "${SUPER_CONF}" ]]; then
  if ! grep -q "25W" "${NVPMODEL_CONF}" 2>/dev/null; then
    ${SUDO} cp "${NVPMODEL_CONF}" "${NVPMODEL_CONF}.bak" 2>/dev/null || true
    ${SUDO} cp "${SUPER_CONF}" "${NVPMODEL_CONF}"
    echo "  Swapped in super conf (15W/25W/MAXN_SUPER/7W)."
  else
    echo "  Super conf already active."
  fi
  # Set DEFAULT=2 (MAXN_SUPER) in conf so it persists across reboots
  ${SUDO} sed -i 's/< PM_CONFIG DEFAULT=[0-9]* >/< PM_CONFIG DEFAULT=2 >/' "${NVPMODEL_CONF}"
  # Set MAXN_SUPER now
  ${SUDO} nvpmodel -m 2 2>/dev/null && echo "  Power mode: MAXN_SUPER" || echo "  WARNING: nvpmodel -m 2 failed, check conf."
else
  echo "  WARNING: ${SUPER_CONF} not found, skipping 25W setup."
fi

# --- Step 1: udev rule ---
echo "[1/7] Jetson udev GPU power-device rule..."
${SUDO} touch "${RULE_FILE}"
if ! ${SUDO} grep -qxF "${POWER_RULE}" "${RULE_FILE}"; then
  echo "${POWER_RULE}" | ${SUDO} tee -a "${RULE_FILE}" >/dev/null
  echo "  Added rule."
else
  echo "  Already present."
fi
${SUDO} udevadm control --reload-rules
${SUDO} udevadm trigger --subsystem-match=nvidia-gpu-v2-power || true

# --- Step 2: torch + vLLM ---
echo "[2/7] Installing torch (Jetson) + source-built vLLM..."
${SUDO} "${UV_BIN}" pip install \
  --index-url https://pypi.jetson-ai-lab.io/jp6/cu126 \
  "torch==2.10.0" \
  --no-deps \
  --python "${PYTHON_BIN}"

${SUDO} "${UV_BIN}" pip install \
  --no-deps \
  --python "${PYTHON_BIN}" \
  "${WHEEL_PATH}"

# --- Step 3: cuda-compat + bitsandbytes ---
echo "[3/7] Installing cuda-compat-12-6 + bitsandbytes ${BNB_VERSION}..."
${SUDO} apt-get update -qq
${SUDO} apt-get install -y cuda-compat-12-6 git cmake build-essential ninja-build
${SUDO} "${UV_BIN}" pip install \
  --python "${PYTHON_BIN}" \
  "bitsandbytes==${BNB_VERSION}"

# --- Step 4: BnB SM87 source build ---
if [[ "${BUILD_BNB}" == "1" ]]; then
  echo "[4/7] Building bitsandbytes ${BNB_VERSION} for SM87 / CUDA 12.6..."
  ${SUDO} rm -rf "${BNB_BUILD_DIR}"
  ${SUDO} git clone --depth 1 --branch "${BNB_VERSION}" "${BNB_REPO}" "${BNB_BUILD_DIR}"
  ${SUDO} env \
    CUDA_HOME=/usr/local/cuda-12.6 \
    CUDACXX=/usr/local/cuda-12.6/bin/nvcc \
    BNB_CUDA_VERSION=126 \
    BNB_CUDA_ARCH="${BNB_ARCHS}" \
    cmake -S "${BNB_BUILD_DIR}" -B "${BNB_BUILD_DIR}/build" \
      -DCOMPUTE_BACKEND=cuda \
      -DCMAKE_BUILD_TYPE=Release
  ${SUDO} cmake --build "${BNB_BUILD_DIR}/build" -j"$(nproc)"

  ARTIFACT="${BNB_BUILD_DIR}/build/libbitsandbytes_cuda126.so"
  if [[ ! -f "${ARTIFACT}" ]]; then
    echo "ERROR: BnB build artifact missing: ${ARTIFACT}" >&2
    exit 1
  fi
  ${SUDO} install -m 0644 "${ARTIFACT}" "${BNB_LIB_DST}"
  echo "  Installed: ${BNB_LIB_DST}"
else
  echo "[4/7] BUILD_BNB=0, skipping BnB source build."
  echo "  Expects working: ${BNB_LIB_DST}"
fi

# --- Step 5: BnB diagnostics ---
echo "[5/7] Verifying bitsandbytes..."
DIAG=$(${SUDO} env LD_LIBRARY_PATH="${LD_PATH}" "${PYTHON_BIN}" -m bitsandbytes 2>&1 || true)
if echo "${DIAG}" | grep -q "SUCCESS"; then
  echo "  bitsandbytes: SUCCESS"
else
  echo "  WARNING: bitsandbytes diagnostics did not print SUCCESS. Output:"
  echo "${DIAG}" | tail -10
  echo "  Continuing anyway — BnB path may not work."
fi

# --- Step 6: Jetson runtime patches ---
echo "[6/7] Applying Jetson vLLM runtime patches..."
${SUDO} "${PYTHON_BIN}" - <<'PY'
from pathlib import Path
import sys

base = Path("/opt/vllm-venv/lib/python3.10/site-packages/vllm/platforms")
init_py = base / "__init__.py"
cuda_py = base / "cuda.py"

if not init_py.exists() or not cuda_py.exists():
    print("  Skipping patches: vLLM platform files not found at expected path.")
    sys.exit(0)

# Patch cuda.py: broaden RuntimeError catches to Exception for Jetson NVML quirks
cuda_text = cuda_py.read_text()
patched = False

old1 = "        except RuntimeError:\n            return None\n"
new1 = (
    "        except Exception:\n"
    "            import torch\n"
    "            try:\n"
    "                major, minor = torch.cuda.get_device_capability(device_id)\n"
    "                from vllm.platforms.cuda import DeviceCapability\n"
    "                return DeviceCapability(major=major, minor=minor)\n"
    "            except Exception:\n"
    "                return None\n"
)
if "torch.cuda.get_device_capability(device_id)" not in cuda_text and old1 in cuda_text:
    cuda_text = cuda_text.replace(old1, new1, 1)
    patched = True

old2 = "        except RuntimeError:\n            return False\n"
new2 = "        except Exception:\n            return False\n"
if old2 in cuda_text:
    cuda_text = cuda_text.replace(old2, new2, 1)
    patched = True

if patched:
    cuda_py.write_text(cuda_text)
    print("  Applied cuda.py patches.")
else:
    print("  cuda.py: no patches needed (already applied or structure changed).")
PY

# --- Step 7: systemd service ---
echo "[7/7] Writing systemd service (mode: ${VLLM_MODE})..."

if [[ "${VLLM_MODE}" == "bnb" ]]; then
  EXEC_MODEL="${BNB_MODEL}"
  QUANT_FLAGS="--quantization bitsandbytes --load-format bitsandbytes --max-model-len 512"
else
  EXEC_MODEL="${AWQ_MODEL}"
  QUANT_FLAGS="--quantization awq_marlin --max-model-len 2048"
fi

${SUDO} tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=vLLM inference server (Jetson Orin Nano)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${WORKDIR}
Environment="LD_LIBRARY_PATH=${LD_PATH}"
ExecStart=${VENV}/bin/vllm serve ${EXEC_MODEL} \\
    --host 0.0.0.0 \\
    --port 8000 \\
    ${QUANT_FLAGS} \\
    --dtype float16 \\
    --gpu-memory-utilization 0.60 \\
    --enforce-eager
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

${SUDO} systemctl daemon-reload
${SUDO} systemctl enable vllm
echo "  Service written and enabled."
echo ""
# --- jetson-stats upgrade (jtop L4T 36.4.x support) ---
echo "[post] Upgrading jetson-stats for L4T 36.4.x support..."
pip3 install -U jetson-stats -q 2>/dev/null && echo "  jetson-stats updated." || echo "  WARNING: jetson-stats upgrade failed (non-fatal)."

echo ""
echo "=== Bootstrap complete ==="
echo "  Power:  sudo nvpmodel -q  (should show 25W)"
echo "  Start:  sudo systemctl start vllm"
echo "  Logs:   sudo journalctl -u vllm -f"
echo "  Health: curl http://localhost:8000/health"
echo "  Models: curl http://localhost:8000/v1/models"
