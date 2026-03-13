#!/bin/bash
cd /root/.openclaw/workspace/shenzhou99
mkdir -p logs
pkill -f "src.core.engine" 2>/dev/null
pkill -f "src.monitor.watchdog" 2>/dev/null
kill $(lsof -ti:8899) 2>/dev/null
sleep 2
nohup python3 web/server.py >> logs/web.log 2>&1 &
echo "Web: $!"
nohup python3 -m src.core.engine >> logs/engine.log 2>&1 &
echo "Engine: $!"
nohup python3 -m src.monitor.watchdog >> logs/watchdog_out.log 2>&1 &
echo "Watchdog: $!"
