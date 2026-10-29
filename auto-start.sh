#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "${ROOT_DIR}"
if [[ -f .env ]]; then
    set -a
    # Local deployment configuration, owned by the operator.
    # shellcheck disable=SC1091
    source .env
    set +a
fi
if [[ -n "${PYTHON_BIN:-}" ]]; then
    PYTHON="${PYTHON_BIN}"
elif [[ -x .venv/bin/python ]]; then
    PYTHON="${ROOT_DIR}/.venv/bin/python"
elif [[ -x venv/bin/python ]]; then
    PYTHON="${ROOT_DIR}/venv/bin/python"
else
    PYTHON=python3
fi
exec "${PYTHON}" "${ROOT_DIR}/deploy/service_manager.py" start "$@"
