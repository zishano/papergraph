#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if ! command -v uv >/dev/null 2>&1; then
  echo "错误：未找到 uv。请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.11 .venv
fi

cache_dir="${TMPDIR:-/tmp}/papergraph-uv-cache"
UV_CACHE_DIR="$cache_dir" UV_LINK_MODE=copy \
  uv pip install --python .venv/bin/python -e '.[test]'

mkdir -p outputs
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
chmod 600 .env

echo
echo "PaperGraph 环境已安装。"
echo "进入环境：source .venv/bin/activate"
echo "配置密钥：编辑 $project_dir/.env"
echo "查看帮助：papergraph --help"
echo "运行测试：python -m pytest -q"
