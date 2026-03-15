# Jetson llama.cpp Source Build Runbook

## Purpose

Reference for building `llama.cpp` from source on Jetson with CUDA GPU offload (SM87).
This is an alternate backend path, not the primary production path (`vLLM AWQ`).

## Why Source Build

- Official arm64 images tested previously were CPU-only on Jetson for the relevant tags.
- To get GPU offload on Orin, build locally with CUDA and explicit arch `87`.

## Build

```bash
git clone https://github.com/ggml-org/llama.cpp.git /home/ubuntu/vllm_jetson/llama.cpp
cd /home/ubuntu/vllm_jetson/llama.cpp

PATH=/usr/local/cuda/bin:$PATH cmake -S . -B build-sm87-server \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES="87" \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TOOLS=ON

cmake --build build-sm87-server --target llama-server -j6
```

Verify build config:

```bash
rg -n "CMAKE_CUDA_ARCHITECTURES|GGML_CUDA:BOOL" build-sm87-server/CMakeCache.txt
```

Expected:

- `CMAKE_CUDA_ARCHITECTURES=87`
- `GGML_CUDA=ON`

## Run

```bash
sudo /home/ubuntu/vllm_jetson/llama.cpp/build-sm87-server/bin/llama-server \
  -m /home/ubuntu/models/llama_cache/staeiou_bartleby-qwen3-1.7b_v4_bartleby-qwen3-1.7b_v4-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8082 -c 2048 -ngl 99
```

Health checks:

```bash
curl -fsS http://127.0.0.1:8082/health
curl -fsS http://127.0.0.1:8082/v1/models
```

## Observed Performance Snapshot (2026-03-14)

- CUDA llama.cpp: ~44 tok/s aggregate at concurrency 10.
- Prior CPU-only llama.cpp path: ~9.6 tok/s aggregate at concurrency 10.
- vLLM AWQ remained faster than CUDA llama.cpp at sustained concurrency in this setup.

## Notes

- Keep this as a documented fallback/experimental path.
- Primary production path remains `ops/scripts/run-stack.sh` with vLLM.
