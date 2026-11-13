#!/usr/bin/env bash
# 一条命令启动本地 MVP：./start.sh
set -euo pipefail
cd "$(dirname "$0")"
exec python3 app.py "$@"
