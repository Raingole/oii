#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE="${UPDATE_REMOTE:-origin}"
BRANCH="${UPDATE_BRANCH:-main}"

cd "${ROOT_DIR}"

command -v git >/dev/null 2>&1 || {
    echo "[ERROR] 未找到 git" >&2
    exit 1
}

echo "[INFO] 获取远程更新: ${REMOTE}/${BRANCH}"
git fetch "${REMOTE}" "${BRANCH}"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    STASH_MESSAGE="auto-backup-before-update-$(date +%Y%m%d-%H%M%S)"
    echo "[INFO] 检测到本地修改，先备份到 stash: ${STASH_MESSAGE}"
    git stash push -m "${STASH_MESSAGE}"
fi

git pull --ff-only "${REMOTE}" "${BRANCH}"
echo "[OK] 更新完成: $(git rev-parse --short HEAD)"

if [[ "${RESTART_AFTER_UPDATE:-0}" == "1" ]]; then
    echo "[INFO] 更新完成，启动服务"
    exec "${ROOT_DIR}/start-all.sh"
fi

echo "[INFO] 如需立即启动: RESTART_AFTER_UPDATE=1 ./update.sh"
