#!/bin/bash
cd /root/.openclaw/workspace/shenzhou99
mkdir -p logs

# 强制杀死所有旧进程
pkill -9 -f "python3 web/server.py" 2>/dev/null
pkill -9 -f "python3 -m src.core.engine" 2>/dev/null
pkill -9 -f "python3 -m src.monitor.watchdog" 2>/dev/null
kill -9 $(lsof -ti:8899) 2>/dev/null
sleep 2

# 确认干净
remaining=$(ps aux | grep -E "server.py|src.core.engine|src.monitor.watchdog" | grep -v grep | wc -l)
if [ "$remaining" -gt 0 ]; then
    echo "WARNING: $remaining processes still running, force killing..."
    ps aux | grep -E "server.py|src.core.engine|src.monitor.watchdog" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
    sleep 1
fi

# 启动
nohup python3 web/server.py >> logs/web.log 2>&1 &
echo "Web: $!"
nohup python3 -m src.core.engine >> logs/engine.log 2>&1 &
echo "Engine: $!"
nohup python3 -m src.monitor.watchdog >> logs/watchdog_out.log 2>&1 &
echo "Watchdog: $!"
