#!/bin/sh
set -e

CONFIG_DIR="${YANAI_CONFIG_DIR:-/app/config}"
CONFIG_FILE="${YANAI_CONFIG_FILE:-$CONFIG_DIR/config.json}"

mkdir -p "$CONFIG_DIR"

if [ -d "$CONFIG_FILE" ]; then
  echo "Warning: $CONFIG_FILE is a directory; remove the broken bind mount and redeploy." >&2
elif [ ! -f "$CONFIG_FILE" ] || [ ! -s "$CONFIG_FILE" ]; then
  cp /app/config.example.json "$CONFIG_FILE"
fi

export YANAI_CONFIG_FILE="$CONFIG_FILE"

python /app/scripts/bootstrap_defaults.py

exec uvicorn main:app --host 0.0.0.0 --port 80 --access-log
