#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

command -v "${PYTHON_BIN}" >/dev/null 2>&1 || {
  echo "[ERROR] Python 3 is required" >&2
  exit 1
}

command -v ffmpeg >/dev/null 2>&1 || {
  echo "[WARN] ffmpeg not found; audio processing may fail" >&2
}

[[ -f "${ROOT_DIR}/data/.config.yaml" ]] || {
  echo "[ERROR] Missing ${ROOT_DIR}/data/.config.yaml" >&2
  exit 1
}

cd "${ROOT_DIR}"
exec "${PYTHON_BIN}" app.py
