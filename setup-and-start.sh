#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MEMORY_DIR="${ROOT_DIR}/TencentDB-Agent-Memory/MemoryCore"

cd "${ROOT_DIR}"
[[ -f "${ROOT_DIR}/data/.config.yaml" ]] || {
    echo "[ERROR] 缺少 data/.config.yaml，请先复制配置文件" >&2
    exit 1
}
command -v node >/dev/null 2>&1 || {
    echo "[ERROR] 未找到 Node.js >= 22.16.0" >&2
    exit 1
}
command -v npm >/dev/null 2>&1 || {
    echo "[ERROR] 未找到 npm" >&2
    exit 1
}
[[ -d "${MEMORY_DIR}" ]] || {
    echo "[ERROR] 缺少内置 TencentDB-Agent-Memory 源码" >&2
    exit 1
}

if [[ ! -d "${MEMORY_DIR}/node_modules" ]]; then
    echo "[INFO] 首次安装 MemoryCore Node 依赖"
    npm --prefix "${MEMORY_DIR}" install
fi

if [[ ! -f "${MEMORY_DIR}/dist/index.mjs" ]]; then
    echo "[INFO] 首次构建 MemoryCore"
    npm --prefix "${MEMORY_DIR}" run build
fi

echo "[INFO] 启动 MemoryCore、MCP、QQ、Web UI 和中控"
exec "${ROOT_DIR}/start-all.sh"
