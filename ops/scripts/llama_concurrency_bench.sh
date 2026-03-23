#!/usr/bin/env bash
set -euo pipefail

# Concurrent 60s llama.cpp OpenAI-style benchmark using curl + bash.
# Measures:
# - TTFT (time_starttransfer)
# - median completion time
# - aggregate tok/s
# - tok/s per connection
#
# Defaults:
#   LLAMA_URL=http://127.0.0.1:8000/v1/chat/completions
#   CONCURRENCY=10
#   DURATION=60
#   MAX_TOKENS=256
#
# Optional:
#   MODEL=<model-id>         # auto-detected from /v1/models if unset
#   LLAMA_API_KEY=<token>    # adds Authorization header
#   TEMPERATURE=0.2
#
# Example:
#   CONCURRENCY=10 DURATION=60 MAX_TOKENS=256 \
#   bash ops/scripts/llama_concurrency_bench.sh

LLAMA_URL="${LLAMA_URL:-http://127.0.0.1:8000/v1/chat/completions}"
CONCURRENCY="${CONCURRENCY:-10}"
DURATION="${DURATION:-60}"
MAX_TOKENS="${MAX_TOKENS:-256}"
TEMPERATURE="${TEMPERATURE:-0.2}"
MODEL="${MODEL:-}"
LLAMA_API_KEY="${LLAMA_API_KEY:-}"

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi

if [[ ! "${CONCURRENCY}" =~ ^[0-9]+$ ]] || (( CONCURRENCY < 1 )); then
  echo "CONCURRENCY must be a positive integer" >&2
  exit 1
fi
if [[ ! "${DURATION}" =~ ^[0-9]+$ ]] || (( DURATION < 1 )); then
  echo "DURATION must be a positive integer" >&2
  exit 1
fi
if [[ ! "${MAX_TOKENS}" =~ ^[0-9]+$ ]] || (( MAX_TOKENS < 1 )); then
  echo "MAX_TOKENS must be a positive integer" >&2
  exit 1
fi

BASE_URL="${LLAMA_URL%/v1/chat/completions}"
MODELS_URL="${BASE_URL}/v1/models"

if [[ -z "${MODEL}" ]]; then
  MODEL="$(
    curl -sS --max-time 5 "${MODELS_URL}" \
      | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
      | head -n1
  )"
fi

if [[ -z "${MODEL}" ]]; then
  echo "Could not auto-detect model id from ${MODELS_URL}. Set MODEL=..." >&2
  exit 1
fi

# 64 marker words, close to the requested 64 input tokens.
PROMPT_64="$(
  for i in $(seq -w 1 64); do
    printf 'w%s ' "${i}"
  done
)"
PROMPT_64="${PROMPT_64% }"

TMP_DIR="$(mktemp -d)"
RESULTS="${TMP_DIR}/results.tsv"
touch "${RESULTS}"
trap 'rm -rf "${TMP_DIR}"' EXIT

AUTH_HEADER=()
if [[ -n "${LLAMA_API_KEY}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${LLAMA_API_KEY}")
fi

END_TS=$(( $(date +%s) + DURATION ))

median_file() {
  local f="$1"
  if [[ ! -s "${f}" ]]; then
    echo "0.000000"
    return
  fi
  sort -n "${f}" | awk '
    { a[NR]=$1 }
    END {
      if (NR == 0) { print "0.000000"; exit }
      if (NR % 2 == 1) { printf "%.6f\n", a[(NR + 1) / 2]; exit }
      printf "%.6f\n", (a[NR / 2] + a[NR / 2 + 1]) / 2
    }
  '
}

worker() {
  local wid="$1"
  local rid=0

  while (( $(date +%s) < END_TS )); do
    rid=$((rid + 1))
    local req="${TMP_DIR}/req_${wid}_${rid}.json"
    local out="${TMP_DIR}/out_${wid}_${rid}.log"

    cat >"${req}" <<EOF
{
  "model": "${MODEL}",
  "messages": [
    {"role":"user","content":"${PROMPT_64}"}
  ],
  "max_tokens": ${MAX_TOKENS},
  "temperature": ${TEMPERATURE},
  "stream": true,
  "stream_options": {"include_usage": true}
}
EOF

    curl -sS -N --http1.1 \
      -H "Content-Type: application/json" \
      "${AUTH_HEADER[@]}" \
      --data-binary @"${req}" \
      -w "\nCURL_METRICS %{time_starttransfer} %{time_total} %{http_code}\n" \
      "${LLAMA_URL}" \
      >"${out}" || true

    local mline ttft total code completion_tokens tokps status
    mline="$(grep 'CURL_METRICS' "${out}" | tail -n1 || true)"
    ttft="$(awk '{print $2}' <<<"${mline:-}")"
    total="$(awk '{print $3}' <<<"${mline:-}")"
    code="$(awk '{print $4}' <<<"${mline:-}")"

    ttft="${ttft:-0}"
    total="${total:-0}"
    code="${code:-000}"

    completion_tokens="$(
      grep -oE '"completion_tokens":[0-9]+' "${out}" | tail -n1 | cut -d: -f2
    )"
    completion_tokens="${completion_tokens:-0}"

    tokps="$(awk -v t="${completion_tokens}" -v d="${total}" 'BEGIN{ if (d>0) printf "%.6f", t/d; else print "0.000000" }')"

    status="ok"
    if [[ "${code}" != "200" ]]; then
      status="http_${code}"
    fi

    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "${wid}" "${rid}" "${ttft}" "${total}" "${completion_tokens}" "${tokps}" "${status}" \
      >>"${RESULTS}"
  done
}

echo "Running llama benchmark"
echo "  endpoint:    ${LLAMA_URL}"
echo "  model:       ${MODEL}"
echo "  concurrency: ${CONCURRENCY}"
echo "  duration:    ${DURATION}s"
echo "  max_tokens:  ${MAX_TOKENS}"
echo

START_NS="$(date +%s%N)"
for w in $(seq 0 $((CONCURRENCY - 1))); do
  worker "${w}" &
done
wait
END_NS="$(date +%s%N)"

ELAPSED="$(awk -v s="${START_NS}" -v e="${END_NS}" 'BEGIN{printf "%.6f", (e-s)/1e9}')"
TOTAL_REQ="$(wc -l <"${RESULTS}" | tr -d ' ')"
OK_REQ="$(awk -F'\t' '$7=="ok"{c++} END{print c+0}' "${RESULTS}")"
SUM_TOKENS="$(awk -F'\t' '$7=="ok"{s+=$5} END{print s+0}' "${RESULTS}")"
AVG_TTFT="$(awk -F'\t' '$7=="ok"{s+=$3;n++} END{if(n>0) printf "%.6f", s/n; else print "0.000000"}' "${RESULTS}")"
AGG_TOKPS="$(awk -v t="${SUM_TOKENS}" -v e="${ELAPSED}" 'BEGIN{if(e>0) printf "%.6f", t/e; else print "0.000000"}')"

awk -F'\t' '$7=="ok"{print $3}' "${RESULTS}" >"${TMP_DIR}/ttft.txt"
awk -F'\t' '$7=="ok"{print $4}' "${RESULTS}" >"${TMP_DIR}/total.txt"
MED_TTFT="$(median_file "${TMP_DIR}/ttft.txt")"
MED_TOTAL="$(median_file "${TMP_DIR}/total.txt")"

echo "== Summary =="
echo "Elapsed wall time (s): ${ELAPSED}"
echo "Requests total: ${TOTAL_REQ}"
echo "Requests ok: ${OK_REQ}"
echo "Avg TTFT (s): ${AVG_TTFT}"
echo "Median TTFT (s): ${MED_TTFT}"
echo "Median completion time (s): ${MED_TOTAL}"
echo "Aggregate tok/s: ${AGG_TOKPS}"
echo
echo "== Per-Connection tok/s =="
awk -F'\t' -v n="${CONCURRENCY}" '
  $7=="ok" { tok[$1]+=$5; dur[$1]+=$4; req[$1]++ }
  END {
    for (i=0; i<n; i++) {
      t = tok[i] + 0
      d = dur[i] + 0
      r = (d>0 ? t/d : 0)
      printf "conn_%02d  req=%d  tokens=%d  tok/s=%.6f\n", i, req[i]+0, t, r
    }
  }
' "${RESULTS}"

if [[ "${SUM_TOKENS}" -eq 0 ]]; then
  echo
  echo "Warning: no completion token usage detected in responses."
  echo "If stream usage is omitted by this build, tok/s will report 0."
fi
