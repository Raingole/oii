#!/usr/bin/env bash
set -Eeuo pipefail

# Unified setup and startup entrypoint for OII.
# Optional switches: START_MEMORY_CORE=0, START_UI=0, START_MAILPILOT=0,
# START_MCP=0, INSTALL_PYTHON_DEPS=1, UI_PORT, BACKEND_PORT, MAILPILOT_BIN.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NODE_BIN="${NODE_BIN:-node}"
NPM_BIN="${NPM_BIN:-npm}"
LOG_DIR="${ROOT_DIR}/tmp/services"
MEMORY_DIR="${ROOT_DIR}/TencentDB-Agent-Memory/MemoryCore"
declare -a CHILD_PIDS=()
MAIN_PID=""
LOG_TAIL_PID=""

info() { echo "[INFO] $*"; }
fail() { echo "[ERROR] $*" >&2; exit 1; }

cleanup() {
    local code=$?
    trap - EXIT INT TERM
    info "Stopping OII services"
    if [[ -n "${LOG_TAIL_PID}" ]]; then
        kill "${LOG_TAIL_PID}" 2>/dev/null || true
    fi
    for pid in "${MAIN_PID}" "${CHILD_PIDS[@]:-}"; do
        [[ -n "${pid}" ]] || continue
        kill -0 "${pid}" 2>/dev/null && kill "${pid}" 2>/dev/null || true
    done
    wait || true
    exit "${code}"
}
trap cleanup EXIT INT TERM

cd "${ROOT_DIR}"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || fail "Python not found: ${PYTHON_BIN}"
[[ -f data/.config.yaml ]] || fail "Missing data/.config.yaml"
[[ -d "${MEMORY_DIR}" ]] || fail "Missing embedded MemoryCore source"

# Load local deployment secrets without committing them to YAML or Git.
if [[ -f "${ROOT_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.env"
    set +a
fi

mkdir -p "${LOG_DIR}" data/memory

if [[ "${INSTALL_PYTHON_DEPS:-0}" == "1" ]]; then
    info "Installing Python dependencies"
    "${PYTHON_BIN}" -m pip install -r requirements.txt
fi

if [[ "${START_MEMORY_CORE:-1}" == "1" ]]; then
    command -v "${NODE_BIN}" >/dev/null 2>&1 || fail "Node.js 22+ is required"
    command -v "${NPM_BIN}" >/dev/null 2>&1 || fail "npm not found"
    if [[ ! -d "${MEMORY_DIR}/node_modules" ]]; then
        info "Installing MemoryCore dependencies"
        "${NPM_BIN}" --prefix "${MEMORY_DIR}" install
    fi
    if [[ ! -f "${MEMORY_DIR}/dist/index.mjs" ]]; then
        info "Building MemoryCore"
        "${NPM_BIN}" --prefix "${MEMORY_DIR}" run build
    fi
    info "Starting Tencent MemoryCore"
    "${PYTHON_BIN}" deploy/memory-core/start_memory_core.py >"${LOG_DIR}/memory-core.log" 2>&1 &
    MEMORY_PID=$!
    CHILD_PIDS+=("${MEMORY_PID}")
    sleep 2
    kill -0 "${MEMORY_PID}" 2>/dev/null || fail "MemoryCore failed; see ${LOG_DIR}/memory-core.log"
    if command -v curl >/dev/null 2>&1; then
        ready=0
        for _ in {1..20}; do
            if curl -fsS http://127.0.0.1:8420/health >/dev/null 2>&1; then ready=1; break; fi
            sleep 1
        done
        [[ "${ready}" == "1" ]] || fail "MemoryCore health check failed; see ${LOG_DIR}/memory-core.log"
    fi
fi

port_is_busy() {
    command -v ss >/dev/null 2>&1 || return 1
    ss -ltn "sport = :$1" | tail -n +2 | grep -q .
}

start_mcp() {
    local name="$1"
    local script="$2"
    local port="$3"
    local log_file="${LOG_DIR}/mcp-${name}.log"
    [[ -f "${script}" ]] || fail "MCP script not found: ${script}"
    port_is_busy "${port}" && fail "MCP port is already in use: ${port}"
    info "Starting MCP ${name} on ${port}"
    "${PYTHON_BIN}" "${script}" >"${log_file}" 2>&1 &
    local pid=$!
    CHILD_PIDS+=("${pid}")
    sleep 1
    kill -0 "${pid}" 2>/dev/null || fail "MCP ${name} failed; see ${log_file}"
}

if [[ "${START_UI:-1}" == "1" ]]; then
    UI_PORT_VALUE="${UI_PORT:-8010}"
    BACKEND_PORT_VALUE="${BACKEND_PORT:-${HTTP_PORT:-}}"
    if [[ -z "${BACKEND_PORT_VALUE}" ]]; then
        # Read the effective local server HTTP port from the private config.
        # The public config defaults to 8003, while deployments may override it.
        BACKEND_PORT_VALUE="$(awk '
            /^server:[[:space:]]*$/ { in_server=1; next }
            in_server && /^[^[:space:]#]/ { exit }
            in_server && /^[[:space:]]+http_port:[[:space:]]*[0-9]+/ { print $2; exit }
        ' data/.config.yaml 2>/dev/null || true)"
        BACKEND_PORT_VALUE="${BACKEND_PORT_VALUE:-8003}"
    fi
    [[ -f ui_server.py ]] || fail "Missing ui_server.py"
    port_is_busy "${UI_PORT_VALUE}" && fail "UI port is already in use: ${UI_PORT_VALUE}"
    info "Starting UI on ${UI_PORT_VALUE} (controller HTTP ${BACKEND_PORT_VALUE})"
    "${PYTHON_BIN}" ui_server.py --host 0.0.0.0 --port "${UI_PORT_VALUE}" --backend-port "${BACKEND_PORT_VALUE}" >"${LOG_DIR}/ui.log" 2>&1 &
    UI_PID=$!
    CHILD_PIDS+=("${UI_PID}")
    sleep 1
    kill -0 "${UI_PID}" 2>/dev/null || fail "UI failed; see ${LOG_DIR}/ui.log"
fi

if [[ "${START_MAILPILOT:-1}" == "1" ]]; then
    MAILPILOT_CONFIG="${MAILPILOT_CONFIG:-${ROOT_DIR}/data/.config.yaml}"
    MAILPILOT_BIN="${MAILPILOT_BIN:-${ROOT_DIR}/mailpilot/mailpilot}"
    [[ -f "${MAILPILOT_CONFIG}" ]] || fail "Missing MailPilot config: ${MAILPILOT_CONFIG}"
    if [[ ! -x "${MAILPILOT_BIN}" ]]; then
        command -v go >/dev/null 2>&1 || fail "MailPilot binary is missing and Go was not found"
        info "Building MailPilot"
        (cd mailpilot && go build -o "${MAILPILOT_BIN}" .)
    fi
    info "Starting MailPilot"
    "${MAILPILOT_BIN}" daemon -c "${MAILPILOT_CONFIG}" >"${LOG_DIR}/mailpilot.log" 2>&1 &
    MAILPILOT_PID=$!
    CHILD_PIDS+=("${MAILPILOT_PID}")
    sleep 2
    kill -0 "${MAILPILOT_PID}" 2>/dev/null || fail "MailPilot failed; see ${LOG_DIR}/mailpilot.log"
fi

if [[ "${START_MCP:-1}" == "1" ]]; then
    export MCP_BACKENDS="${MCP_BACKENDS:-restaurant=ws://127.0.0.1:8766/mcp/}"
    start_mcp restaurant mcp_restaurant_server/server.py 8766
    start_mcp aggregator mcp_aggregator/server.py 8765
fi

info "Starting OII controller"
"${PYTHON_BIN}" app.py >tmp/server.log 2>&1 &
MAIN_PID=$!

PUBLIC_HOST="${PUBLIC_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}' || true)}"
PUBLIC_HOST="${PUBLIC_HOST:-服务器IP}"
echo
echo "============================================================"
echo "OII services started"
echo "UI:             http://${PUBLIC_HOST}:${UI_PORT_VALUE:-8010}/"
echo "Controller HTTP: http://${PUBLIC_HOST}:${BACKEND_PORT_VALUE:-8003}/"
echo "Cognitive health: http://${PUBLIC_HOST}:${BACKEND_PORT_VALUE:-8003}/cognitive/health"
echo "Cognitive state:  http://${PUBLIC_HOST}:${BACKEND_PORT_VALUE:-8003}/cognitive/state"
echo "ESP32 WebSocket: ws://${PUBLIC_HOST}:8000/xiaozhi/v1/"
echo "MG946R Servo:    ws://${PUBLIC_HOST}:8777/ws"
echo "NapCat OneBot:   ws://${PUBLIC_HOST}:8082/onebot"
echo "MemoryCore:      http://127.0.0.1:8420/health"
echo "MCP Aggregator:  ws://127.0.0.1:8765/mcp/"
echo "Logs:            ${ROOT_DIR}/tmp/server.log"
echo "                 ${ROOT_DIR}/tmp/services/"
echo "============================================================"
echo "Live controller log (Ctrl+C stops all services):"
echo

if command -v tail >/dev/null 2>&1; then
    tail -n 20 -f tmp/server.log &
    LOG_TAIL_PID=$!
fi
wait "${MAIN_PID}"
