#!/usr/bin/env bash
# start.sh — start the AI Blog Generator server
# Usage: ./start.sh [debug]

set -euo pipefail

MODE="${1:-production}"

if [ "$MODE" = "debug" ]; then
  echo "Starting in DEBUG mode..."
  # Temporarily patch mode in config for this run via env override
  export APP_CONFIG_PATH="${APP_CONFIG_PATH:-config.json}"
  # Modify mode inline with a temp config if needed, or just set mode in config.json
fi

export APP_CONFIG_PATH="${APP_CONFIG_PATH:-config.json}"

# Check required env vars are set
required_vars=(DEEPSEEK_API_KEY GROK_API_KEY)
for var in "${required_vars[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: Required environment variable '$var' is not set." >&2
    exit 1
  fi
done

python main.py
