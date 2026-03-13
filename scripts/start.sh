#!/bin/bash
cd /root/.openclaw/workspace/shenzhou99
mkdir -p logs

# 停止旧进程
pkill -f "src.core.engine" 2>/dev/null
pkill -f "src.monitor.watchdog" 2>/dev/null
kill $(lsof -ti:8899) 2>/dev/null
sleep 2

# 启动 Web
nohup python3 web/server.py >> logs/web.log 2>&1 &
echo $! > logs/web.pid
echo "🌐 Web PID: $!"

# 启动引擎
nohup python3 -m src.core.engine >> logs/engine.log 2>&1 &
echo $! > logs/engine.pid
echo "⚡ Engine PID: $!"

# 启动看门狗
nohup python3 -m src.monitor.watchdog >> logs/watchdog.log 2>&1 &
echo $! > logs/watchdog.pid
echo "🐕 Watchdog PID: $!"

sleep 3
echo ""
echo "=== 进程状态 ==="
ps aux | grep -E "(engine|server|watchdog)" | grep -v grep
echo ""
echo "=== 服务 ==="
echo "  控制台: http://43.162.89.175:8899"
echo "  引擎日志: logs/engine.log"
echo "  看门狗日志: logs/watchdog.log"
