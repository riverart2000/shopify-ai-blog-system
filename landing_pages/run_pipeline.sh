#!/usr/bin/env bash
# run_pipeline.sh
# End-to-end automation script for AWS Lightsail / Linux servers.
# It runs the prompt generator, social generator, landing page publisher, and RSS generator.

set -e

# Change to the directory where the script is located
cd "$(dirname "$0")"

# Load Python virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "========================================"
echo "Starting Marketing Pipeline: $(date)"
echo "========================================"

# 1. Generate Prompts (fetch product info, download images, create JSON)
echo "--> 1. Running generate_prompts.py..."
python generate_prompts.py --fetcher shopify --generator grok

# 2. Generate Social Images and Text (incremental, uses Grok edit endpoint)
echo "--> 2. Running generate_social.py..."
python generate_social.py

# 3. Publish Landing Page to Shopify
echo "--> 3. Running publish_landing_page.py..."
python publish_landing_page.py --published

# 4. Generate RSS Feed for Publer
# Ensure the RSS Daemon is running in the background to drip-feed social posts to Publer
BASE_URL=${PUBLIC_BASE_URL:-"https://yourdomain.com/social/"}
if pgrep -f "generate_rss.py" > /dev/null; then
    echo "--> 4. RSS Daemon is already running."
else
    echo "--> 4. Starting RSS Daemon in background with base URL: $BASE_URL..."
    nohup python generate_rss.py --base-url "$BASE_URL" > rss_daemon.log 2>&1 &
fi

echo "========================================"
echo "Pipeline completed: $(date)"
echo "========================================"
