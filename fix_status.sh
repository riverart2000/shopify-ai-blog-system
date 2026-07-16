#!/usr/bin/env bash
echo "=========================================================="
echo "      Shopify AI Blog System — Production Status          "
echo "=========================================================="
echo ""

cd /home/ubuntu/shopify-ai-blog-system/ai-blog-generator-python-server

# 1. Process Checks
echo "--- 1. Processes ---"
if pgrep -f "uvicorn" > /dev/null || pgrep -f "python main.py" > /dev/null; then
    echo "✅ FastAPI Backend (main.py) is RUNNING"
else
    echo "❌ FastAPI Backend (main.py) is DOWN"
fi

if pgrep -f "node" > /dev/null; then
    echo "✅ React App (Frontend) is RUNNING"
else
    echo "❌ React App (Frontend) is DOWN"
fi

if pgrep -f "caddy" > /dev/null; then
    echo "✅ Caddy (Reverse Proxy) is RUNNING"
else
    echo "❌ Caddy (Reverse Proxy) is DOWN"
fi

if pgrep -f "scheduler.py" > /dev/null; then
    echo "✅ Scheduler is RUNNING"
else
    echo "❌ Scheduler is DOWN"
fi

echo ""
echo "--- 2. Port Checks ---"
# Check if ports are listening (ss is more reliable here than lsof without root)
if ss -tulpn 2>/dev/null | grep -q ":4000\b"; then
    echo "✅ Port 4000 (Backend) is LISTENING"
else
    echo "❌ Port 4000 (Backend) is NOT LISTENING"
fi

if ss -tulpn 2>/dev/null | grep -q ":3001\b"; then
    echo "✅ Port 3001 (Frontend) is LISTENING"
else
    echo "❌ Port 3001 (Frontend) is NOT LISTENING"
fi

if ss -tulpn 2>/dev/null | grep -q ":18090\b"; then
    echo "✅ Port 18090 (RSS/Publer) is LISTENING"
else
    echo "❌ Port 18090 (RSS/Publer) is NOT LISTENING"
fi

if ss -tulpn 2>/dev/null | grep -q ":8443\b"; then
    echo "✅ Port 8443 (Caddy HTTPS) is LISTENING"
else
    echo "❌ Port 8443 (Caddy HTTPS) is NOT LISTENING"
fi

echo ""
echo "--- 3. HTTP Health Checks ---"
# Check Backend Health
HTTP_BACKEND=$(curl -o /dev/null -s -w "%{http_code}\n" http://localhost:4000/)
if [ "$HTTP_BACKEND" == "200" ] || [ "$HTTP_BACKEND" == "303" ]; then
    echo "✅ Backend Health: OK (HTTP $HTTP_BACKEND)"
else
    echo "❌ Backend Health: FAILED (HTTP $HTTP_BACKEND)"
fi

# Check Frontend Health
HTTP_FRONTEND=$(curl -o /dev/null -s -w "%{http_code}\n" http://localhost:3001/)
if [ "$HTTP_FRONTEND" == "200" ] || [ "$HTTP_FRONTEND" == "303" ]; then
    echo "✅ Frontend Health: OK (HTTP $HTTP_FRONTEND)"
else
    echo "❌ Frontend Health: FAILED (HTTP $HTTP_FRONTEND)"
fi

echo ""
echo "--- 4. Recent Errors (Last 5 lines) ---"
echo "[main.log]"
tail -n 5 /home/ubuntu/shopify-ai-blog-system/ai-blog-generator-python-server/logs/main.log | grep -i "error\|exception" || echo "  No recent errors."
echo "[frontend.log]"
tail -n 5 /home/ubuntu/shopify-ai-blog-system/ai-blog-generator-python-server/logs/frontend.log | grep -i "error\|exception" || echo "  No recent errors."
echo "[caddy.log]"
tail -n 5 /home/ubuntu/shopify-ai-blog-system/ai-blog-generator-python-server/logs/caddy.log | grep -i "error\|exception" || echo "  No recent errors."

echo ""
echo "=========================================================="
