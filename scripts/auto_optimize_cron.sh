#!/bin/bash
# 神州99 自动策略优化 — 每6小时由 cron 触发
# 流程：优化参数 → 如果找到更优 → 自动应用到实盘 → 重启引擎

set -e

PROJECT_DIR="/root/.openclaw/workspace/shenzhou99"
LOG_FILE="$PROJECT_DIR/logs/optimize.log"
BEST_PARAMS="$PROJECT_DIR/src/backtest/best_params.json"
LOCK_FILE="$PROJECT_DIR/logs/optimize.lock"

cd "$PROJECT_DIR"

# 防止重复运行
if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') | 优化已在运行 (PID=$pid)，跳过" >> "$LOG_FILE"
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f $LOCK_FILE" EXIT

echo "$(date '+%Y-%m-%d %H:%M:%S') | ═══ 开始自动优化 ═══" >> "$LOG_FILE"

# 记录优化前参数的 hash
OLD_HASH=""
if [ -f "$BEST_PARAMS" ]; then
    OLD_HASH=$(md5sum "$BEST_PARAMS" | cut -d' ' -f1)
fi

# 跑 30 轮优化（5 品种，约 3 分钟）
PYTHONPATH="$PROJECT_DIR" python3 src/backtest/auto_optimize.py \
    --all --rounds 30 --days 90 >> "$LOG_FILE" 2>&1

# 检查参数是否变化
NEW_HASH=""
if [ -f "$BEST_PARAMS" ]; then
    NEW_HASH=$(md5sum "$BEST_PARAMS" | cut -d' ' -f1)
fi

if [ "$OLD_HASH" != "$NEW_HASH" ] && [ -n "$NEW_HASH" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') | 🏆 发现更优参数！准备应用到实盘..." >> "$LOG_FILE"

    # 应用优化参数到 engine.py（通过 apply 脚本）
    PYTHONPATH="$PROJECT_DIR" python3 src/backtest/apply_params.py >> "$LOG_FILE" 2>&1

    # 测试
    cd "$PROJECT_DIR"
    python3 -m pytest tests/ -q >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') | ✅ 测试通过，重启引擎..." >> "$LOG_FILE"
        bash scripts/restart.sh >> "$LOG_FILE" 2>&1

        # Git 提交
        git add -A
        git commit -m "🤖 自动优化: 更新策略参数 $(date '+%m-%d %H:%M')" >> "$LOG_FILE" 2>&1
        git push >> "$LOG_FILE" 2>&1

        echo "$(date '+%Y-%m-%d %H:%M:%S') | ✅ 优化完成，引擎已重启" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') | ❌ 测试失败，放弃应用" >> "$LOG_FILE"
    fi
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') | 📊 当前参数仍是最优，无需更新" >> "$LOG_FILE"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') | ═══ 自动优化结束 ═══" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
