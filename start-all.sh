#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_DIR="${ROOT_DIR}/tmp/services"
declare -a CHILD_PIDS=()
MAIN_PID=""

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM

    if [[ -n "${MAIN_PID}" ]] && kill -0 "${MAIN_PID}" 2>/dev/null; then
        kill "${MAIN_PID}" 2>/dev/null || true
    fi

    for pid in "${CHILD_PIDS[@]:-}"; do
        if kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" 2>/dev/null || true
        fi
    done

    wait || true
    exit "${exit_code}"
}
trap cleanup EXIT INT TERM

command -v "${PYTHON_BIN}" >/dev/null 2>&1 || {
    echo "[ERROR] 未找到 Python 3: ${PYTHON_BIN}" >&2
    exit 1
}

[[ -f "${ROOT_DIR}/data/.config.yaml" ]] || {
    echo "[ERROR] 缺少 ${ROOT_DIR}/data/.config.yaml" >&2
    exit 1
}

mkdir -p "${LOG_DIR}" "${ROOT_DIR}/data/memory"

memory_config_ok=false
if grep -Eq '^[[:space:]]*Memory:[[:space:]]*server_longterm[[:space:]]*$' "${ROOT_DIR}/config.yaml" \
    || grep -Eq '^[[:space:]]*Memory:[[:space:]]*server_longterm[[:space:]]*$' "${ROOT_DIR}/data/.config.yaml" 2>/dev/null; then
    memory_config_ok=true
fi

memory_write_ok=false
memory_probe="${ROOT_DIR}/data/memory/.write-test"
if touch "${memory_probe}" 2>/dev/null; then
    rm -f "${memory_probe}"
    memory_write_ok=true
fi

if [[ "${memory_config_ok}" == true && "${memory_write_ok}" == true ]]; then
    echo "[OK] 长期记忆配置成功：server_longterm，数据库目录 ${ROOT_DIR}/data/memory"
else
    echo "[ERROR] 长期记忆配置失败：配置=${memory_config_ok}，目录可写=${memory_write_ok}" >&2
    echo "[ERROR] 请检查 config.yaml 的 selected_module.Memory 和 data/memory 写入权限" >&2
    exit 1
fi
cd "${ROOT_DIR}"

export MCP_BACKENDS="${MCP_BACKENDS:-restaurant=ws://127.0.0.1:8766/mcp/}"

port_is_busy() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :${port}" | tail -n +2 | grep -q .
    else
        return 1
    fi
}

start_mcp() {
    local name="$1"
    local script="$2"
    local port="$3"
    local log_file="${LOG_DIR}/mcp-${name}.log"

    [[ -f "${script}" ]] || {
        echo "[ERROR] MCP 服务不存在: ${script}" >&2
        exit 1
    }

    if port_is_busy "${port}"; then
        echo "[ERROR] MCP ${name} 端口 ${port} 已被占用" >&2
        exit 1
    fi

    echo "[INFO] 启动 MCP: ${name} (端口 ${port})"
    "${PYTHON_BIN}" "${script}" >"${log_file}" 2>&1 &
    local pid=$!
    CHILD_PIDS+=("${pid}")
    sleep 1

    if ! kill -0 "${pid}" 2>/dev/null; then
        echo "[ERROR] MCP ${name} 启动失败，日志: ${log_file}" >&2
        exit 1
    fi
}

# MCP 后端使用 8766，聚合器使用 8765（小智只连接聚合器）
start_mcp "restaurant" "${ROOT_DIR}/mcp_restaurant_server/server.py" "8766"
start_mcp "aggregator" "${ROOT_DIR}/mcp_aggregator/server.py" "8765"

# 新增 MCP 后端时，在这里增加一行，并同步设置 MCP_BACKENDS，例如：
# start_mcp "weather" "${ROOT_DIR}/mcp_weather_server/server.py" "8767"

echo "[INFO] 启动小智主服务，日志: ${ROOT_DIR}/tmp/server.log"
"${PYTHON_BIN}" app.py &
MAIN_PID=$!

wait "${MAIN_PID}"
