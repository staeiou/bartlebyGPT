#!/usr/bin/env bash
# Fast same-device rebootstrap — reuses existing venv/wheel, no source builds.
# Use when: vLLM upgrade, patch reapplication, service config reset.
# Use bootstrap_jetson_full.sh for a full fresh install.

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
WHEEL_GLOB="${WHEEL_GLOB:-/home/ubuntu/vllm-build/dist/vllm-*.whl}"
WORKDIR="${WORKDIR:-${REPO_ROOT}}"

AWQ_MODEL="${AWQ_MODEL:-staeiou/bartleby-qwen3-1.7b_v4-awq}"
BNB_MODEL="${BNB_MODEL:-staeiou/bartleby-qwen3-1.7b_v4}"
VLLM_MODE="${VLLM_MODE:-awq}"

LD_PATH="/usr/local/cuda-12.6/compat:\
${VENV}/lib/python3.10/site-packages/nvidia/cu12/lib:\
${VENV}/lib/python3.10/site-packages/torch/lib:\
${VENV}/lib/python3.10/site-packages/nvidia/nvjitlink/lib:\
${VENV}/lib/python3.10/site-packages/nvidia/cuda_nvrtc/lib:\
/usr/lib/aarch64-linux-gnu/nvidia:\
/usr/local/cuda/targets/aarch64-linux/lib"

# --- Preflight ---
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "ERROR: Missing venv at ${PYTHON_BIN}. Run bootstrap_jetson_full.sh first." >&2
  exit 1
fi
if ! command -v "${UV_BIN}" >/dev/null 2>&1; then
  echo "ERROR: Missing uv at ${UV_BIN}." >&2
  exit 1
fi

WHEEL_PATH="${VLLM_WHEEL_PATH:-}"
if [[ -z "${WHEEL_PATH}" ]]; then
  WHEEL_PATH="$(ls -1t ${WHEEL_GLOB} 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "${WHEEL_PATH}" || ! -f "${WHEEL_PATH}" ]]; then
  echo "ERROR: No vLLM wheel found at ${WHEEL_GLOB}. Set VLLM_WHEEL_PATH." >&2
  exit 1
fi

echo "=== Fast local rebootstrap (mode: ${VLLM_MODE}) ==="
echo "  Wheel: ${WHEEL_PATH}"

# --- Step 1: nvpmodel super mode ---
echo "[1/5] nvpmodel super mode (25W)..."
SUPER_CONF="/etc/nvpmodel/nvpmodel_p3767_0003_super.conf"
if [[ -f "${SUPER_CONF}" ]]; then
  if ! grep -q "25W" /etc/nvpmodel.conf 2>/dev/null; then
    ${SUDO} cp /etc/nvpmodel.conf /etc/nvpmodel.conf.bak 2>/dev/null || true
    ${SUDO} cp "${SUPER_CONF}" /etc/nvpmodel.conf
    echo "  Swapped in super conf."
  else
    echo "  Already active."
  fi
  ${SUDO} sed -i 's/< PM_CONFIG DEFAULT=[0-9]* >/< PM_CONFIG DEFAULT=2 >/' /etc/nvpmodel.conf
  ${SUDO} nvpmodel -m 2 2>/dev/null && echo "  Set to MAXN_SUPER." || echo "  WARNING: nvpmodel -m 2 failed."
else
  echo "  Super conf not found, skipping."
fi

# --- Step 2: Validate torch ---
echo "[2/5] Validating torch in venv..."
TORCH_VER="$("${PYTHON_BIN}" -c 'import torch; print(torch.__version__)' 2>/dev/null || echo 'missing')"
echo "  torch: ${TORCH_VER}"
if [[ "${TORCH_VER}" == "missing" ]]; then
  echo "ERROR: torch not importable. Run full bootstrap." >&2
  exit 1
fi

# --- Step 3: Reinstall vLLM wheel ---
echo "[3/5] Reinstalling vLLM wheel (no deps)..."
${SUDO} "${UV_BIN}" pip install --python "${PYTHON_BIN}" --no-deps "${WHEEL_PATH}"

# --- Step 4: Reapply Jetson runtime patches ---
echo "[4/5] Reapplying Jetson vLLM runtime patches..."
${SUDO} "${PYTHON_BIN}" - <<'PY'
from pathlib import Path
import sys

base = Path("/opt/vllm-venv/lib/python3.10/site-packages/vllm/platforms")
cuda_py = base / "cuda.py"

if not cuda_py.exists():
    print("  Skipping: cuda.py not found at expected path.")
    sys.exit(0)

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
if old2 in cuda_text:
    cuda_text = cuda_text.replace(old2, "        except Exception:\n            return False\n", 1)
    patched = True

if patched:
    cuda_py.write_text(cuda_text)
    print("  Patches applied.")
else:
    print("  Already patched.")
PY

# --- Step 5: Rewrite service and restart ---
echo "[5/5] Writing systemd service (mode: ${VLLM_MODE})..."

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
${SUDO} systemctl restart vllm

echo "Waiting for health..."
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "Healthy."
    break
  fi
  sleep 2
done

curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool 2>/dev/null || true
echo ""
echo "=== Fast rebootstrap complete ==="
echo "  Power: $(${SUDO} nvpmodel -q 2>/dev/null | tail -1)"
echo "  Logs:  sudo journalctl -u vllm -f"
