#!/usr/bin/env bash
set -euo pipefail

# Fresh-pod bootstrap for:
# 1. vLLM on localhost
# 2. nginx in front with IP-based request rate limiting + connection cap
# 3. optional cloudflared tunnel (quick tunnel by default, or named tunnel via token)
#
# Assumptions:
# - Debian/Ubuntu-like environment
# - NVIDIA drivers/CUDA are already available in the pod
# - uv is already installed and on PATH
# - You want one script that starts both services without systemd
#
# Example:
#   MODEL=Qwen/Qwen3-4B \
#   PUBLIC_PORT=18201 \
#   RATE_LIMIT=2r/s \
#   RATE_BURST=10 \
#   CONNECTION_LIMIT=2 \
#   bash ops/scripts/run-stack.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${OPS_DIR}/.." && pwd)"

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
  echo "Loading profile: ${PROFILE_FILE}"
  set -a
  # shellcheck source=/dev/null
  source "${PROFILE_FILE}"
  set +a
fi

# process: existing foreground stack launcher
# systemd: delegate to host-specific bootstrap scripts that manage services
STACK_MODE="${STACK_MODE:-process}"
INFERENCE_BACKEND="${INFERENCE_BACKEND:-vllm}"
SYSTEMD_BOOTSTRAP_SCRIPT="${SYSTEMD_BOOTSTRAP_SCRIPT:-}"
BOOTSTRAP_PROFILE="${BOOTSTRAP_PROFILE:-}"

VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8000}"
PUBLIC_PORT="${PUBLIC_PORT:-18201}"

RATE_LIMIT="${RATE_LIMIT:-2r/s}"
RATE_BURST="${RATE_BURST:-10}"
CONNECTION_LIMIT="${CONNECTION_LIMIT:-2}"
GLOBAL_RATE_LIMIT="${GLOBAL_RATE_LIMIT:-20r/s}"
GLOBAL_RATE_BURST="${GLOBAL_RATE_BURST:-15}"

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
VENV_DIR="${VENV_DIR:-/opt/vllm-venv}"
VLLM_PACKAGE_SPEC="${VLLM_PACKAGE_SPEC:-vllm}"
VLLM_BIN="${VLLM_BIN:-${VENV_DIR}/bin/vllm}"
VLLM_LOG="${VLLM_LOG:-/var/log/vllm.log}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-/opt/llama.cpp/build/bin/llama-server}"
LLAMA_LOG="${LLAMA_LOG:-/var/log/llama-server.log}"
LLAMA_MODEL="${LLAMA_MODEL:-/opt/models/bartleby/bartleby-qwen3-1.7b_v4-Q4_K_M.gguf}"
LLAMA_CTX="${LLAMA_CTX:-512}"
LLAMA_THREADS="${LLAMA_THREADS:-4}"
LLAMA_PARALLEL="${LLAMA_PARALLEL:-1}"
LLAMA_EXTRA_ARGS="${LLAMA_EXTRA_ARGS:-}"
NGINX_ACCESS_LOG="${NGINX_ACCESS_LOG:-/var/log/nginx/vllm_access.log}"
NGINX_ERROR_LOG="${NGINX_ERROR_LOG:-/var/log/nginx/vllm_error.log}"
NGINX_CONF_PATH="${NGINX_CONF_PATH:-/etc/nginx/conf.d/vllm_proxy.conf}"
ENABLE_WEB_APP="${ENABLE_WEB_APP:-1}"
# run-stack lives in ops/scripts, so docs/ is two levels up by default.
WEB_APP_DIR="${WEB_APP_DIR:-${REPO_ROOT}/docs}"
WEB_APP_PUBLIC_DIR="${WEB_APP_PUBLIC_DIR:-/var/www/bartlebygpt}"
if [[ "${WEB_APP_DIR}" != /* ]]; then
  WEB_APP_DIR="${REPO_ROOT}/${WEB_APP_DIR#./}"
fi

IS_JETSON="${IS_JETSON:-}"
if [[ -z "${IS_JETSON}" ]]; then
  if [[ -f /etc/nv_tegra_release ]]; then
    IS_JETSON="1"
  else
    IS_JETSON="0"
  fi
fi

if [[ "${IS_JETSON}" == "1" ]]; then
  DEFAULT_MODEL="staeiou/bartleby-qwen3-1.7b_v4-awq"
  DEFAULT_VLLM_EXTRA_ARGS="--quantization awq_marlin --dtype float16 --gpu-memory-utilization 0.60 --enforce-eager"
  DEFAULT_TELEMETRY_IDLE_GPU_WATTS="2"
  DEFAULT_TELEMETRY_BASE_SYSTEM_WATTS="5.5"
  DEFAULT_TELEMETRY_GPU_COOLING_MULTIPLIER="1.00"
  DEFAULT_TELEMETRY_POWER_BACKEND="jtop"
else
  DEFAULT_MODEL="staeiou/bartleby-qwen3-1.7b_v4"
  DEFAULT_VLLM_EXTRA_ARGS=""
  DEFAULT_TELEMETRY_IDLE_GPU_WATTS="35"
  DEFAULT_TELEMETRY_BASE_SYSTEM_WATTS="300"
  DEFAULT_TELEMETRY_GPU_COOLING_MULTIPLIER="1.35"
  DEFAULT_TELEMETRY_POWER_BACKEND="auto"
fi

MODEL="${MODEL:-${DEFAULT_MODEL}}"

TELEMETRY_SCRIPT="${TELEMETRY_SCRIPT:-${SCRIPT_DIR}/power_telemetry.py}"
TELEMETRY_HOST="${TELEMETRY_HOST:-127.0.0.1}"
TELEMETRY_PORT="${TELEMETRY_PORT:-18081}"
TELEMETRY_LOG="${TELEMETRY_LOG:-/var/log/bartleby_power_telemetry.log}"
TELEMETRY_SAMPLE_INTERVAL="${TELEMETRY_SAMPLE_INTERVAL:-1.0}"
TELEMETRY_IDLE_GPU_WATTS="${TELEMETRY_IDLE_GPU_WATTS:-${DEFAULT_TELEMETRY_IDLE_GPU_WATTS}}"
TELEMETRY_BASE_SYSTEM_WATTS="${TELEMETRY_BASE_SYSTEM_WATTS:-${DEFAULT_TELEMETRY_BASE_SYSTEM_WATTS}}"
TELEMETRY_GPU_COOLING_MULTIPLIER="${TELEMETRY_GPU_COOLING_MULTIPLIER:-${DEFAULT_TELEMETRY_GPU_COOLING_MULTIPLIER}}"
TELEMETRY_POWER_BACKEND="${TELEMETRY_POWER_BACKEND:-${DEFAULT_TELEMETRY_POWER_BACKEND}}"
TELEMETRY_ESPHOME_POWER_URL="${TELEMETRY_ESPHOME_POWER_URL:-}"
TELEMETRY_ESPHOME_BASE_URL="${TELEMETRY_ESPHOME_BASE_URL:-}"
TELEMETRY_ESPHOME_POWER_PATH="${TELEMETRY_ESPHOME_POWER_PATH:-/sensor/power}"
TELEMETRY_HISTORY_24H_BIN_SECONDS="${TELEMETRY_HISTORY_24H_BIN_SECONDS:-300}"

VLLM_API_KEY="${VLLM_API_KEY:-}"
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-${DEFAULT_VLLM_EXTRA_ARGS}}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-2048}"
VLLM_ENABLE_ASYNC_SCHEDULING="${VLLM_ENABLE_ASYNC_SCHEDULING:-1}"
VLLM_ALLOWED_ORIGINS="${VLLM_ALLOWED_ORIGINS:-[\"*\"]}"
VLLM_ALLOWED_METHODS="${VLLM_ALLOWED_METHODS:-[\"GET\",\"POST\",\"OPTIONS\"]}"
VLLM_ALLOWED_HEADERS="${VLLM_ALLOWED_HEADERS:-[\"*\"]}"
VLLM_ALLOW_CREDENTIALS="${VLLM_ALLOW_CREDENTIALS:-0}"
ENABLE_CLOUDFLARE_TUNNEL="${ENABLE_CLOUDFLARE_TUNNEL:-1}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-cloudflared}"
CLOUDFLARED_LOG="${CLOUDFLARED_LOG:-/var/log/cloudflared.log}"
CLOUDFLARE_TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"
CLOUDFLARE_PUBLIC_HOSTNAME="${CLOUDFLARE_PUBLIC_HOSTNAME:-api.bartlebygpt.org}"
NGINX_CORS_ALLOW_ORIGIN="${NGINX_CORS_ALLOW_ORIGIN:-*}"
NGINX_CORS_ALLOW_METHODS="${NGINX_CORS_ALLOW_METHODS:-GET, POST, OPTIONS}"
NGINX_CORS_ALLOW_HEADERS="${NGINX_CORS_ALLOW_HEADERS:-*}"

WAIT_FOR_VLLM_SECONDS="${WAIT_FOR_VLLM_SECONDS:-180}"
WAIT_FOR_CLOUDFLARE_URL_SECONDS="${WAIT_FOR_CLOUDFLARE_URL_SECONDS:-60}"
NGINX_CLIENT_MAX_BODY_SIZE="${NGINX_CLIENT_MAX_BODY_SIZE:-20m}"
USE_EXISTING_INFERENCE="${USE_EXISTING_INFERENCE:-0}"
SKIP_INFERENCE_START=0
NGINX_PID=""
NGINX_MANAGED_BY_SYSTEMD=0
CLOUDFLARED_STARTED=0

if [[ "${INFERENCE_BACKEND}" == "vllm" && -z "${MODEL}" ]]; then
  echo "MODEL is required for INFERENCE_BACKEND=vllm." >&2
  exit 1
fi

if [[ "${INFERENCE_BACKEND}" == "llama-server" && -z "${LLAMA_MODEL}" ]]; then
  echo "LLAMA_MODEL is required for INFERENCE_BACKEND=llama-server." >&2
  exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1; then
  SUDO=(sudo)
else
  echo "This script needs root (or sudo) to install packages and write nginx config." >&2
  exit 1
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

has_systemd_runtime() {
  [[ -d /run/systemd/system ]] && need_cmd systemctl
}

inference_health_url() {
  echo "http://${VLLM_HOST}:${VLLM_PORT}/health"
}

inference_is_healthy() {
  curl -fsS --max-time 2 "$(inference_health_url)" >/dev/null 2>&1
}

check_existing_inference() {
  if [[ "${USE_EXISTING_INFERENCE}" == "1" ]]; then
    SKIP_INFERENCE_START=1
    echo "Configured to reuse existing inference service (USE_EXISTING_INFERENCE=1)."
    return
  fi

  if ! inference_is_healthy; then
    return
  fi

  local url
  url="$(inference_health_url)"
  echo "Inference service already healthy at ${url}." >&2
  echo "Refusing to start a second ${INFERENCE_BACKEND} instance in STACK_MODE=process." >&2
  echo "To proceed, do one of the following:" >&2
  echo "  1) Stop existing inference first (example: sudo systemctl stop vllm)" >&2
  echo "  2) Reuse existing inference by setting USE_EXISTING_INFERENCE=1" >&2
  exit 1
}

run_systemd_bootstrap() {
  local bootstrap_script="${SYSTEMD_BOOTSTRAP_SCRIPT:-}"

  if [[ -z "${bootstrap_script}" ]]; then
    if [[ "${INFERENCE_BACKEND}" == "llama-server" ]]; then
      bootstrap_script="${OPS_DIR}/bootstrap/bootstrap_rpi_llama_fast.sh"
    elif [[ "${INFERENCE_BACKEND}" == "vllm" && "${IS_JETSON}" == "1" ]]; then
      bootstrap_script="${OPS_DIR}/bootstrap/bootstrap_jetson_fast.sh"
    else
      echo "No default systemd bootstrap script for INFERENCE_BACKEND=${INFERENCE_BACKEND}, IS_JETSON=${IS_JETSON}." >&2
      echo "Set SYSTEMD_BOOTSTRAP_SCRIPT explicitly, or use STACK_MODE=process for pod/container runtime." >&2
      exit 1
    fi
  fi

  if [[ "${bootstrap_script}" != /* ]]; then
    bootstrap_script="${REPO_ROOT}/${bootstrap_script#./}"
  fi
  if [[ ! -x "${bootstrap_script}" ]]; then
    echo "Systemd bootstrap script is not executable: ${bootstrap_script}" >&2
    exit 1
  fi

  if [[ -z "${BOOTSTRAP_PROFILE}" && "${INFERENCE_BACKEND}" == "llama-server" ]]; then
    BOOTSTRAP_PROFILE="rpi4-llama"
  fi

  echo "STACK_MODE=systemd, delegating to ${bootstrap_script}"
  if [[ -n "${BOOTSTRAP_PROFILE}" ]]; then
    echo "Using bootstrap profile: ${BOOTSTRAP_PROFILE}"
    "${SUDO[@]}" env PROFILE="${BOOTSTRAP_PROFILE}" "${bootstrap_script}"
  else
    "${SUDO[@]}" "${bootstrap_script}"
  fi
}

install_base_packages() {
  if need_cmd nginx && need_cmd python3; then
    return
  fi

  if ! need_cmd apt-get; then
    echo "Required base packages are missing and apt-get is unavailable. Install nginx and python3 manually." >&2
    exit 1
  fi

  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    nginx \
    python3
}

install_uv() {
  if need_cmd "${UV_BIN}"; then
    return
  fi

  if [[ -x /home/ubuntu/.local/bin/uv ]]; then
    UV_BIN="/home/ubuntu/.local/bin/uv"
    return
  fi

  echo "uv not found; installing to /usr/local/bin/uv"
  local tmp_installer
  tmp_installer="$(mktemp)"
  curl -LsSf https://astral.sh/uv/install.sh >"${tmp_installer}"
  "${SUDO[@]}" env UV_INSTALL_DIR=/usr/local/bin sh "${tmp_installer}"
  rm -f "${tmp_installer}"

  if need_cmd /usr/local/bin/uv; then
    UV_BIN="/usr/local/bin/uv"
  elif need_cmd uv; then
    UV_BIN="uv"
  fi

  if ! need_cmd "${UV_BIN}"; then
    echo "Failed to install uv. Set UV_BIN to a valid uv binary path." >&2
    exit 1
  fi
}

bootstrap_vllm_env() {
  if [[ -x "${VLLM_BIN}" ]]; then
    return
  fi

  if ! need_cmd "${UV_BIN}"; then
    echo "uv binary not found: ${UV_BIN}" >&2
    echo "Install uv or set UV_BIN=/path/to/uv. If vLLM is already installed, set VLLM_BIN accordingly." >&2
    exit 1
  fi

  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    "${SUDO[@]}" mkdir -p "$(dirname "${VENV_DIR}")"
    "${SUDO[@]}" "${UV_BIN}" venv "${VENV_DIR}" --python python3
  fi

  if [[ ! -x "${VLLM_BIN}" ]]; then
    "${SUDO[@]}" "${UV_BIN}" pip install --python "${VENV_DIR}/bin/python" "${VLLM_PACKAGE_SPEC}"
  fi
}

write_nginx_config() {
  local auth_header_line
  local cors_credentials_line=""
  local web_app_location_block='    location / { return 404; }'
  if [[ -n "${VLLM_API_KEY}" ]]; then
    auth_header_line="proxy_set_header Authorization \"Bearer ${VLLM_API_KEY}\";"
  else
    auth_header_line="proxy_set_header Authorization \$http_authorization;"
  fi

  if [[ "${VLLM_ALLOW_CREDENTIALS}" == "1" ]]; then
    cors_credentials_line='add_header Access-Control-Allow-Credentials "true" always;'
  fi

  if [[ "${ENABLE_WEB_APP}" == "1" ]]; then
    if [[ ! -d "${WEB_APP_PUBLIC_DIR}" ]]; then
      echo "ENABLE_WEB_APP=1 but WEB_APP_PUBLIC_DIR does not exist: ${WEB_APP_PUBLIC_DIR}" >&2
      exit 1
    fi
    web_app_location_block=$(cat <<EOF
    location = / {
        root ${WEB_APP_PUBLIC_DIR};
        add_header Cache-Control "no-store, no-cache, must-revalidate, max-age=0" always;
        add_header Pragma "no-cache" always;
        expires -1;
        try_files /index.html =404;
    }

    location = /index.html {
        root ${WEB_APP_PUBLIC_DIR};
        add_header Cache-Control "no-store, no-cache, must-revalidate, max-age=0" always;
        add_header Pragma "no-cache" always;
        expires -1;
        try_files \$uri =404;
    }

    location ~* \\.html$ {
        root ${WEB_APP_PUBLIC_DIR};
        add_header Cache-Control "no-store, no-cache, must-revalidate, max-age=0" always;
        add_header Pragma "no-cache" always;
        expires -1;
        try_files \$uri =404;
    }

    location ~* \\.(js|mjs|css)$ {
        root ${WEB_APP_PUBLIC_DIR};
        add_header Cache-Control "no-store, no-cache, must-revalidate, max-age=0" always;
        add_header Pragma "no-cache" always;
        expires -1;
        try_files \$uri =404;
    }

    location / {
        root ${WEB_APP_PUBLIC_DIR};
        try_files \$uri \$uri/ /index.html;
    }
EOF
)
  fi

  "${SUDO[@]}" mkdir -p "$(dirname "${NGINX_CONF_PATH}")"
  "${SUDO[@]}" mkdir -p "$(dirname "${NGINX_ACCESS_LOG}")"
  "${SUDO[@]}" touch "${NGINX_ACCESS_LOG}" "${NGINX_ERROR_LOG}"

  "${SUDO[@]}" tee "${NGINX_CONF_PATH}" >/dev/null <<EOF
map \$http_cf_connecting_ip \$client_limit_key {
    default \$http_cf_connecting_ip;
    ""      \$remote_addr;
}

map \$http_origin \$bartleby_cors_origin {
    default                                             "";
    "~^https://([a-z0-9-]+\.)?auditomatic\.org$"       \$http_origin;
    "~^https://([a-z0-9-]+\.)?bartlebygpt\.org$"       \$http_origin;
}

# Per-IP limits (abuse protection):
limit_req_zone  \$client_limit_key zone=perip_rate:10m rate=${RATE_LIMIT};
limit_conn_zone \$client_limit_key zone=perip_conn:10m;

# Global limit (capacity protection) — all requests share one bucket:
limit_req_zone  \$server_addr zone=global_rate:1m rate=${GLOBAL_RATE_LIMIT};

access_log ${NGINX_ACCESS_LOG};
error_log ${NGINX_ERROR_LOG};

upstream vllm_backend {
    server ${VLLM_HOST}:${VLLM_PORT};
    keepalive 32;
}

server {
    listen ${PUBLIC_PORT};
    server_name _;
    server_tokens off;

    client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};
    limit_req_status 429;
    limit_conn_status 429;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Access-Control-Allow-Origin \$bartleby_cors_origin always;
    add_header Access-Control-Allow-Methods "${NGINX_CORS_ALLOW_METHODS}" always;
    add_header Access-Control-Allow-Headers "${NGINX_CORS_ALLOW_HEADERS}" always;
    ${cors_credentials_line}

    location = /health {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$client_limit_key;
        proxy_set_header X-Forwarded-For \$client_limit_key;
        proxy_set_header CF-Connecting-IP \$client_limit_key;
        proxy_set_header Authorization \$http_authorization;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header Access-Control-Allow-Methods;
        proxy_hide_header Access-Control-Allow-Headers;
        proxy_hide_header Access-Control-Allow-Credentials;
        proxy_connect_timeout 5s;
        proxy_send_timeout 5s;
        proxy_read_timeout 5s;
        proxy_pass http://vllm_backend/health;
    }

    location = /metrics {
        limit_req  zone=perip_rate burst=5 nodelay;
        limit_conn perip_conn ${CONNECTION_LIMIT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$client_limit_key;
        proxy_set_header X-Forwarded-For \$client_limit_key;
        proxy_set_header CF-Connecting-IP \$client_limit_key;
        proxy_set_header Authorization \$http_authorization;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header Access-Control-Allow-Methods;
        proxy_hide_header Access-Control-Allow-Headers;
        proxy_hide_header Access-Control-Allow-Credentials;
        proxy_connect_timeout 5s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
        proxy_pass http://vllm_backend/metrics;
    }

    location = /load {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$client_limit_key;
        proxy_set_header X-Forwarded-For \$client_limit_key;
        proxy_set_header CF-Connecting-IP \$client_limit_key;
        proxy_set_header Authorization \$http_authorization;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header Access-Control-Allow-Methods;
        proxy_hide_header Access-Control-Allow-Headers;
        proxy_hide_header Access-Control-Allow-Credentials;
        proxy_connect_timeout 5s;
        proxy_send_timeout 5s;
        proxy_read_timeout 5s;
        proxy_pass http://vllm_backend/load;
    }

    location = /telemetry/power {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$client_limit_key;
        proxy_set_header X-Forwarded-For \$client_limit_key;
        proxy_set_header CF-Connecting-IP \$client_limit_key;
        proxy_set_header Authorization \$http_authorization;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header Access-Control-Allow-Methods;
        proxy_hide_header Access-Control-Allow-Headers;
        proxy_hide_header Access-Control-Allow-Credentials;
        proxy_connect_timeout 5s;
        proxy_send_timeout 5s;
        proxy_read_timeout 5s;
        proxy_pass http://${TELEMETRY_HOST}:${TELEMETRY_PORT}/telemetry/power;
    }

    location = /telemetry/history {
        limit_req  zone=perip_rate burst=2 nodelay;
        limit_conn perip_conn ${CONNECTION_LIMIT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$client_limit_key;
        proxy_set_header X-Forwarded-For \$client_limit_key;
        proxy_set_header CF-Connecting-IP \$client_limit_key;
        proxy_set_header Authorization \$http_authorization;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header Access-Control-Allow-Methods;
        proxy_hide_header Access-Control-Allow-Headers;
        proxy_hide_header Access-Control-Allow-Credentials;
        proxy_connect_timeout 5s;
        proxy_send_timeout 8s;
        proxy_read_timeout 8s;
        proxy_pass http://${TELEMETRY_HOST}:${TELEMETRY_PORT}/telemetry/history;
    }

    location = /v1/models {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$client_limit_key;
        proxy_set_header X-Forwarded-For \$client_limit_key;
        proxy_set_header CF-Connecting-IP \$client_limit_key;
        proxy_set_header Authorization \$http_authorization;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header Access-Control-Allow-Methods;
        proxy_hide_header Access-Control-Allow-Headers;
        proxy_hide_header Access-Control-Allow-Credentials;
        proxy_connect_timeout 5s;
        proxy_send_timeout 5s;
        proxy_read_timeout 5s;
        proxy_pass http://vllm_backend/v1/models;
    }

    location = /v1/chat/completions {
        if (\$request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin \$bartleby_cors_origin always;
            add_header Access-Control-Allow-Methods "${NGINX_CORS_ALLOW_METHODS}" always;
            add_header Access-Control-Allow-Headers "${NGINX_CORS_ALLOW_HEADERS}" always;
            ${cors_credentials_line}
            add_header Content-Length 0;
            add_header Content-Type text/plain;
            return 204;
        }

        limit_req  zone=global_rate burst=${GLOBAL_RATE_BURST} nodelay;
        limit_req  zone=perip_rate burst=${RATE_BURST} nodelay;
        limit_conn perip_conn ${CONNECTION_LIMIT};

        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$client_limit_key;
        proxy_set_header X-Forwarded-For \$client_limit_key;
        proxy_set_header CF-Connecting-IP \$client_limit_key;
        proxy_set_header Connection "";
        ${auth_header_line}

        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header Access-Control-Allow-Methods;
        proxy_hide_header Access-Control-Allow-Headers;
        proxy_hide_header Access-Control-Allow-Credentials;

        proxy_connect_timeout 30s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;

        proxy_pass http://vllm_backend;
    }

    location ^~ /v1/ {
        limit_req  zone=global_rate burst=${GLOBAL_RATE_BURST} nodelay;
        limit_req  zone=perip_rate burst=${RATE_BURST} nodelay;
        limit_conn perip_conn ${CONNECTION_LIMIT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$client_limit_key;
        proxy_set_header X-Forwarded-For \$client_limit_key;
        proxy_set_header CF-Connecting-IP \$client_limit_key;
        proxy_set_header Authorization \$http_authorization;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_hide_header Access-Control-Allow-Methods;
        proxy_hide_header Access-Control-Allow-Headers;
        proxy_hide_header Access-Control-Allow-Credentials;
        proxy_connect_timeout 30s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
        proxy_pass http://vllm_backend;
    }

${web_app_location_block}
}
EOF

  # Ubuntu/Debian include `sites-enabled/*`; renaming to `*.disabled` still matches.
  # Remove default site symlink/file from sites-enabled entirely.
  if [[ -e /etc/nginx/sites-enabled/default ]]; then
    "${SUDO[@]}" rm -f /etc/nginx/sites-enabled/default
  fi
  if [[ -e /etc/nginx/sites-enabled/default.disabled ]]; then
    "${SUDO[@]}" rm -f /etc/nginx/sites-enabled/default.disabled
  fi

  "${SUDO[@]}" nginx -t
}

stop_existing_nginx() {
  if "${SUDO[@]}" pgrep -x nginx >/dev/null 2>&1; then
    echo "Stopping existing nginx workers before foreground start..."
    "${SUDO[@]}" nginx -s quit >/dev/null 2>&1 || true

    local deadline=$((SECONDS + 10))
    while (( SECONDS < deadline )); do
      if ! "${SUDO[@]}" pgrep -x nginx >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done

    if "${SUDO[@]}" pgrep -x nginx >/dev/null 2>&1; then
      echo "nginx workers still running after graceful stop; forcing termination..."
      "${SUDO[@]}" pkill -x nginx >/dev/null 2>&1 || true
    fi
  fi
}

start_nginx() {
  echo "Starting nginx on port ${PUBLIC_PORT}"

  if has_systemd_runtime && "${SUDO[@]}" systemctl list-unit-files nginx.service >/dev/null 2>&1; then
    "${SUDO[@]}" systemctl restart nginx
    if ! "${SUDO[@]}" systemctl is-active --quiet nginx; then
      echo "nginx.service failed to become active after restart." >&2
      "${SUDO[@]}" systemctl status nginx --no-pager -n 120 >&2 || true
      exit 1
    fi
    NGINX_MANAGED_BY_SYSTEMD=1
    return
  fi

  stop_existing_nginx
  "${SUDO[@]}" nginx -g 'daemon off;' &
  NGINX_PID=$!

  sleep 1
  if ! "${SUDO[@]}" pgrep -x nginx >/dev/null 2>&1; then
    echo "nginx did not remain running after start." >&2
    "${SUDO[@]}" tail -n 120 "${NGINX_ERROR_LOG}" >&2 || true
    exit 1
  fi
}

prepare_web_app_assets() {
  if [[ "${ENABLE_WEB_APP}" != "1" ]]; then
    return
  fi

  if [[ ! -d "${WEB_APP_DIR}" ]]; then
    echo "ENABLE_WEB_APP=1 but WEB_APP_DIR does not exist: ${WEB_APP_DIR}" >&2
    exit 1
  fi

  "${SUDO[@]}" mkdir -p "${WEB_APP_PUBLIC_DIR}"
  "${SUDO[@]}" cp -a "${WEB_APP_DIR}/." "${WEB_APP_PUBLIC_DIR}/"

  # Ensure nginx workers can traverse/read regardless of source dir perms.
  "${SUDO[@]}" find "${WEB_APP_PUBLIC_DIR}" -type d -exec chmod 755 {} +
  "${SUDO[@]}" find "${WEB_APP_PUBLIC_DIR}" -type f -exec chmod 644 {} +
}

start_vllm() {
  "${SUDO[@]}" mkdir -p "$(dirname "${VLLM_LOG}")"
  "${SUDO[@]}" touch "${VLLM_LOG}"

  if [[ ! -x "${VLLM_BIN}" ]]; then
    echo "vllm binary not found: ${VLLM_BIN}" >&2
    exit 1
  fi

  local -a args=(
    "${VLLM_BIN}" serve "${MODEL}"
    --host "${VLLM_HOST}"
    --port "${VLLM_PORT}"
    --max-model-len "${VLLM_MAX_MODEL_LEN}"
    --allowed-origins "${VLLM_ALLOWED_ORIGINS}"
    --allowed-methods "${VLLM_ALLOWED_METHODS}"
    --allowed-headers "${VLLM_ALLOWED_HEADERS}"
  )

  if [[ "${VLLM_ENABLE_ASYNC_SCHEDULING}" == "1" ]]; then
    args+=(--async-scheduling)
  fi

  if [[ "${VLLM_ALLOW_CREDENTIALS}" == "1" ]]; then
    args+=(--allow-credentials)
  fi

  if [[ -n "${VLLM_API_KEY}" ]]; then
    args+=(--api-key "${VLLM_API_KEY}")
  fi

  if [[ -n "${VLLM_EXTRA_ARGS}" ]]; then
    # Intentional word splitting so callers can pass raw extra CLI flags.
    # Example: VLLM_EXTRA_ARGS="--tensor-parallel-size 2 --max-model-len 4096"
    read -r -a extra_args <<<"${VLLM_EXTRA_ARGS}"
    args+=("${extra_args[@]}")
  fi

  echo "Starting vLLM: ${args[*]}"
  if [[ ${#SUDO[@]} -eq 0 ]]; then
    "${args[@]}" >>"${VLLM_LOG}" 2>&1 &
  else
    local quoted_args=()
    local arg
    for arg in "${args[@]}"; do
      quoted_args+=("$(printf '%q' "${arg}")")
    done
    "${SUDO[@]}" bash -lc "exec ${quoted_args[*]} >>$(printf '%q' "${VLLM_LOG}") 2>&1" &
  fi
  VLLM_PID=$!
}

start_llama_server() {
  "${SUDO[@]}" mkdir -p "$(dirname "${LLAMA_LOG}")"
  "${SUDO[@]}" touch "${LLAMA_LOG}"

  if [[ ! -x "${LLAMA_SERVER_BIN}" ]]; then
    echo "llama-server binary not found: ${LLAMA_SERVER_BIN}" >&2
    echo "Run bootstrap_rpi_llama_full.sh first, or set LLAMA_SERVER_BIN." >&2
    exit 1
  fi

  if [[ ! -f "${LLAMA_MODEL}" ]]; then
    echo "llama model not found: ${LLAMA_MODEL}" >&2
    echo "Run bootstrap_rpi_llama_full.sh first, or set LLAMA_MODEL." >&2
    exit 1
  fi

  local -a args=(
    "${LLAMA_SERVER_BIN}"
    -m "${LLAMA_MODEL}"
    --host "${VLLM_HOST}"
    --port "${VLLM_PORT}"
    -c "${LLAMA_CTX}"
    -t "${LLAMA_THREADS}"
    -np "${LLAMA_PARALLEL}"
  )

  if [[ -n "${LLAMA_EXTRA_ARGS}" ]]; then
    # Intentional word splitting so callers can pass raw extra CLI flags.
    read -r -a extra_args <<<"${LLAMA_EXTRA_ARGS}"
    args+=("${extra_args[@]}")
  fi

  echo "Starting llama-server: ${args[*]}"
  if [[ ${#SUDO[@]} -eq 0 ]]; then
    "${args[@]}" >>"${LLAMA_LOG}" 2>&1 &
  else
    local quoted_args=()
    local arg
    for arg in "${args[@]}"; do
      quoted_args+=("$(printf '%q' "${arg}")")
    done
    "${SUDO[@]}" bash -lc "exec ${quoted_args[*]} >>$(printf '%q' "${LLAMA_LOG}") 2>&1" &
  fi
  VLLM_PID=$!
}

start_telemetry() {
  "${SUDO[@]}" mkdir -p "$(dirname "${TELEMETRY_LOG}")"
  "${SUDO[@]}" touch "${TELEMETRY_LOG}"

  if [[ ! -f "${TELEMETRY_SCRIPT}" ]]; then
    echo "Telemetry script not found: ${TELEMETRY_SCRIPT}" >&2
    exit 1
  fi

  local -a env_args=(
    "TELEMETRY_HOST=${TELEMETRY_HOST}"
    "TELEMETRY_PORT=${TELEMETRY_PORT}"
    "TELEMETRY_SAMPLE_INTERVAL=${TELEMETRY_SAMPLE_INTERVAL}"
    "TELEMETRY_IDLE_GPU_WATTS=${TELEMETRY_IDLE_GPU_WATTS}"
    "TELEMETRY_BASE_SYSTEM_WATTS=${TELEMETRY_BASE_SYSTEM_WATTS}"
    "TELEMETRY_GPU_COOLING_MULTIPLIER=${TELEMETRY_GPU_COOLING_MULTIPLIER}"
    "TELEMETRY_POWER_BACKEND=${TELEMETRY_POWER_BACKEND}"
    "TELEMETRY_ESPHOME_POWER_URL=${TELEMETRY_ESPHOME_POWER_URL}"
    "TELEMETRY_ESPHOME_BASE_URL=${TELEMETRY_ESPHOME_BASE_URL}"
    "TELEMETRY_ESPHOME_POWER_PATH=${TELEMETRY_ESPHOME_POWER_PATH}"
    "TELEMETRY_VLLM_BASE_URL=http://${VLLM_HOST}:${VLLM_PORT}"
    "TELEMETRY_HISTORY_24H_BIN_SECONDS=${TELEMETRY_HISTORY_24H_BIN_SECONDS}"
  )

  echo "Starting power telemetry server on ${TELEMETRY_HOST}:${TELEMETRY_PORT}"
  if [[ ${#SUDO[@]} -eq 0 ]]; then
    env "${env_args[@]}" python3 "${TELEMETRY_SCRIPT}" >>"${TELEMETRY_LOG}" 2>&1 &
  else
    local quoted_env=()
    local item
    for item in "${env_args[@]}"; do
      quoted_env+=("$(printf '%q' "${item}")")
    done
    "${SUDO[@]}" bash -lc "exec env ${quoted_env[*]} python3 $(printf '%q' "${TELEMETRY_SCRIPT}") >>$(printf '%q' "${TELEMETRY_LOG}") 2>&1" &
  fi
  TELEMETRY_PID=$!
}

wait_for_telemetry() {
  local deadline=$((SECONDS + 30))

  while (( SECONDS < deadline )); do
    if curl -fsS "http://${TELEMETRY_HOST}:${TELEMETRY_PORT}/health" >/dev/null 2>&1; then
      echo "Power telemetry is healthy at http://${TELEMETRY_HOST}:${TELEMETRY_PORT}/telemetry/power"
      return
    fi
    sleep 1
  done

  echo "Timed out waiting for power telemetry. Recent log tail:" >&2
  "${SUDO[@]}" tail -n 80 "${TELEMETRY_LOG}" >&2 || true
  exit 1
}

start_cloudflare_tunnel() {
  CLOUDFLARED_STARTED=0

  if [[ "${ENABLE_CLOUDFLARE_TUNNEL}" != "1" ]]; then
    return
  fi

  # On managed hosts, prefer the system cloudflared service if active.
  # This avoids running an extra quick tunnel process in parallel.
  if [[ -z "${CLOUDFLARE_TUNNEL_TOKEN}" ]] && has_systemd_runtime; then
    if "${SUDO[@]}" systemctl is-active --quiet cloudflared; then
      echo "Detected active system cloudflared service; skipping local quick tunnel."
      return
    fi
  fi

  if ! need_cmd "${CLOUDFLARED_BIN}"; then
    echo "cloudflared binary not found: ${CLOUDFLARED_BIN}" >&2
    exit 1
  fi

  "${SUDO[@]}" mkdir -p "$(dirname "${CLOUDFLARED_LOG}")"
  if [[ ${#SUDO[@]} -eq 0 ]]; then
    : >"${CLOUDFLARED_LOG}"
  else
    "${SUDO[@]}" bash -lc ": >$(printf '%q' "${CLOUDFLARED_LOG}")"
  fi

  local -a args=(
    "${CLOUDFLARED_BIN}" tunnel
  )

  if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
    args+=(
      --protocol http2
      --edge-ip-version 4
      run
      --token "${CLOUDFLARE_TUNNEL_TOKEN}"
    )
  else
    args+=(
      --protocol http2
      --edge-ip-version 4
      --url "http://127.0.0.1:${PUBLIC_PORT}"
    )
  fi

  echo "Starting cloudflared: ${args[*]}"
  if [[ ${#SUDO[@]} -eq 0 ]]; then
    "${args[@]}" >>"${CLOUDFLARED_LOG}" 2>&1 &
  else
    local quoted_args=()
    local arg
    for arg in "${args[@]}"; do
      quoted_args+=("$(printf '%q' "${arg}")")
    done
    "${SUDO[@]}" bash -lc "exec ${quoted_args[*]} >>$(printf '%q' "${CLOUDFLARED_LOG}") 2>&1" &
  fi
  CLOUDFLARED_PID=$!
  CLOUDFLARED_STARTED=1
}

wait_for_cloudflare_url() {
  if [[ "${ENABLE_CLOUDFLARE_TUNNEL}" != "1" || "${CLOUDFLARED_STARTED}" != "1" ]]; then
    return
  fi

  local deadline=$((SECONDS + WAIT_FOR_CLOUDFLARE_URL_SECONDS))
  local tunnel_url=""

  if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
    echo "Waiting for named tunnel to register connections..."
    while (( SECONDS < deadline )); do
      if grep -q "Registered tunnel connection" "${CLOUDFLARED_LOG}" 2>/dev/null; then
        echo "Cloudflare named tunnel connected for ${CLOUDFLARE_PUBLIC_HOSTNAME}"
        echo "cloudflared log: ${CLOUDFLARED_LOG}"
        return
      fi
      sleep 1
    done
    echo "Timed out waiting for tunnel connections. Check ${CLOUDFLARED_LOG}" >&2
    "${SUDO[@]}" tail -n 40 "${CLOUDFLARED_LOG}" >&2 || true
    exit 1
  fi

  while (( SECONDS < deadline )); do
    tunnel_url="$(grep -Eo 'https://[[:alnum:].-]+\.trycloudflare\.com' "${CLOUDFLARED_LOG}" 2>/dev/null | tail -n 1 || true)"
    if [[ -n "${tunnel_url}" ]]; then
      echo "Cloudflare tunnel URL: ${tunnel_url}"
      echo "cloudflared log: ${CLOUDFLARED_LOG}"
      return
    fi
    sleep 1
  done

  echo "Cloudflare tunnel started but no URL was found yet. Check ${CLOUDFLARED_LOG}" >&2
}

wait_for_public_endpoint() {
  if [[ "${ENABLE_CLOUDFLARE_TUNNEL}" != "1" ]] || [[ -z "${CLOUDFLARE_PUBLIC_HOSTNAME}" ]]; then
    return
  fi

  local url="https://${CLOUDFLARE_PUBLIC_HOSTNAME}/health"
  local deadline=$((SECONDS + 60))

  echo "Validating public endpoint ${url} ..."
  while (( SECONDS < deadline )); do
    if /usr/bin/curl -fsS --max-time 5 "${url}" >/dev/null 2>&1; then
      echo "Public endpoint is reachable: ${url}"
      return
    fi
    sleep 2
  done

  echo "WARNING: public endpoint ${url} did not become reachable within 60s. Check tunnel and vLLM logs." >&2
}

wait_for_vllm() {
  local deadline=$((SECONDS + WAIT_FOR_VLLM_SECONDS))

  while (( SECONDS < deadline )); do
    if curl -fsS "http://${VLLM_HOST}:${VLLM_PORT}/health" >/dev/null 2>&1; then
      echo "${INFERENCE_BACKEND} is healthy at http://${VLLM_HOST}:${VLLM_PORT}"
      return
    fi
    sleep 1
  done

  echo "Timed out waiting for ${INFERENCE_BACKEND} health endpoint. Recent log tail:" >&2
  if [[ "${INFERENCE_BACKEND}" == "llama-server" ]]; then
    "${SUDO[@]}" tail -n 80 "${LLAMA_LOG}" >&2 || true
  else
    "${SUDO[@]}" tail -n 80 "${VLLM_LOG}" >&2 || true
  fi
  exit 1
}

cleanup() {
  if [[ -n "${VLLM_PID:-}" ]] && kill -0 "${VLLM_PID}" >/dev/null 2>&1; then
    kill "${VLLM_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${TELEMETRY_PID:-}" ]] && kill -0 "${TELEMETRY_PID}" >/dev/null 2>&1; then
    kill "${TELEMETRY_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${CLOUDFLARED_PID:-}" ]] && kill -0 "${CLOUDFLARED_PID}" >/dev/null 2>&1; then
    kill "${CLOUDFLARED_PID}" >/dev/null 2>&1 || true
  fi
  if [[ "${NGINX_MANAGED_BY_SYSTEMD}" != "1" ]]; then
    if [[ -n "${NGINX_PID:-}" ]] && kill -0 "${NGINX_PID}" >/dev/null 2>&1; then
      kill "${NGINX_PID}" >/dev/null 2>&1 || true
    fi
    "${SUDO[@]}" nginx -s quit >/dev/null 2>&1 || true
  fi
}

main() {
  if [[ "${STACK_MODE}" == "systemd" ]]; then
    run_systemd_bootstrap
    return
  fi

  if [[ "${INFERENCE_BACKEND}" != "vllm" && "${INFERENCE_BACKEND}" != "llama-server" ]]; then
    echo "Unsupported INFERENCE_BACKEND in STACK_MODE=process: ${INFERENCE_BACKEND}" >&2
    echo "Supported backends: vllm, llama-server" >&2
    exit 1
  fi

  trap cleanup EXIT INT TERM

  install_base_packages
  if [[ "${INFERENCE_BACKEND}" == "vllm" ]]; then
    install_uv
    bootstrap_vllm_env
  fi
  prepare_web_app_assets
  write_nginx_config
  check_existing_inference
  if [[ "${SKIP_INFERENCE_START}" == "1" ]]; then
    echo "Skipping ${INFERENCE_BACKEND} start; using existing service."
  else
    if [[ "${INFERENCE_BACKEND}" == "llama-server" ]]; then
      start_llama_server
    else
      start_vllm
    fi
  fi
  wait_for_vllm
  start_telemetry
  wait_for_telemetry

  start_nginx

  start_cloudflare_tunnel
  wait_for_cloudflare_url

  wait_for_public_endpoint

  wait
}

main "$@"
