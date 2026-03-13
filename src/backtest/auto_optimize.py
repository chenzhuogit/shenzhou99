"""
神州99 自主策略优化引擎 — 灵感来自 Karpathy 的 autoresearch

核心思路：修改参数 → 回测 → 好了保留 → 差了回滚 → 循环

用法:
    # 跑 50 轮优化（约 25 分钟）
    PYTHONPATH=. python3 src/backtest/auto_optimize.py --rounds 50

    # 对单品种优化
    PYTHONPATH=. python3 src/backtest/auto_optimize.py --inst BTC-USDT-SWAP --rounds 30

    # 全品种优化
    PYTHONPATH=. python3 src/backtest/auto_optimize.py --all --rounds 100

    # 查看当前最优参数
    PYTHONPATH=. python3 src/backtest/auto_optimize.py --show-best
"""

import os
import sys
import json
import copy
import random
import asyncio
import argparse
from datetime import datetime
from dataclasses import dataclass

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.backtest.backtest_engine import (
    BacktestEngine, BacktestConfig, BacktestResult,
    fetch_klines, print_result,
)

# ═══ 路径 ═══
CONFIG_DIR = os.path.dirname(__file__)
OPTIMIZE_CONFIG = os.path.join(CONFIG_DIR, "optimize_config.json")
BEST_PARAMS_FILE = os.path.join(CONFIG_DIR, "best_params.json")
RESULTS_FILE = os.path.join(CONFIG_DIR, "results.tsv")


def load_optimize_config() -> dict:
    with open(OPTIMIZE_CONFIG, "r") as f:
        return json.load(f)


def load_best_params() -> dict:
    """加载当前最优参数，没有则用默认值"""
    if os.path.exists(BEST_PARAMS_FILE):
        with open(BEST_PARAMS_FILE, "r") as f:
            return json.load(f)

    # 从 optimize_config 提取默认值
    cfg = load_optimize_config()
    params = {}
    for key, spec in cfg["params"].items():
        params[key] = spec["current"]
    return params


def save_best_params(params: dict):
    with open(BEST_PARAMS_FILE, "w") as f:
        json.dump(params, f, indent=2)


def init_results_file():
    """初始化 results.tsv"""
    if not os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "w") as f:
            f.write("round\tscore\tnet_pnl\twin_rate\tsharpe\tmax_dd\ttrades\tstatus\tchanges\n")


def log_result(round_num: int, score: float, result: BacktestResult,
               status: str, changes: str):
    with open(RESULTS_FILE, "a") as f:
        f.write(f"{round_num}\t{score:.4f}\t{result.net_pnl:.2f}\t"
                f"{result.win_rate:.3f}\t{result.sharpe_ratio:.2f}\t"
                f"{result.max_drawdown_pct:.1f}\t{result.total_trades}\t"
                f"{status}\t{changes}\n")


def calc_score(result: BacktestResult, initial_equity: float) -> float:
    """
    综合评分 = 收益率 × 夏普比 / 最大回撤

    好的策略：高收益 + 高夏普 + 低回撤 → 高分
    差的策略：低收益 或 高回撤 → 低分
    """
    if result.total_trades < 3:
        return -999.0  # 太少交易无统计意义

    net_pnl_pct = result.net_pnl / initial_equity * 100
    sharpe = max(result.sharpe_ratio, -5.0)  # 限制极端负值
    max_dd = max(result.max_drawdown_pct, 1.0)  # 避免除零

    # 惩罚过低胜率
    if result.win_rate < 0.35:
        return -999.0

    # 惩罚过大回撤
    if result.max_drawdown_pct > 30:
        return -999.0

    score = net_pnl_pct * sharpe / max_dd

    # 奖励利润因子 > 1.5
    if result.profit_factor > 1.5:
        score *= 1.1

    # 奖励高胜率
    if result.win_rate > 0.6:
        score *= 1.05

    return score


def params_to_backtest_config(params: dict, inst_id: str) -> BacktestConfig:
    """把优化参数转为回测配置"""
    cfg = BacktestConfig()

    # ADX
    if "ETH" in inst_id:
        cfg.adx_threshold = params.get("adx_threshold_eth", 30)
    else:
        cfg.adx_threshold = params.get("adx_threshold", 25)

    cfg.min_confidence = params.get("min_confidence", 0.60)
    cfg.sl_atr_mult = params.get("sl_atr_mult", 1.5)
    cfg.tp_atr_mult = params.get("tp_atr_mult", 2.5)
    cfg.trailing_activate_atr = params.get("trailing_activate_atr", 1.5)
    cfg.trailing_distance_atr = params.get("trailing_distance_atr", 0.8)
    cfg.cooldown_bars = params.get("cooldown_bars", 8)

    # EMA 周期需要传给回测引擎（需要扩展 BacktestConfig）
    cfg.ema_fast = params.get("ema_fast", 20)
    cfg.ema_slow = params.get("ema_slow", 50)
    cfg.max_distance_ema = params.get("max_distance_ema", 1.5)

    return cfg


def mutate_params(params: dict, config: dict, temperature: float = 1.0) -> tuple[dict, str]:
    """
    变异参数 — 随机选 1-2 个参数修改

    temperature: 0.5=保守小改, 1.0=正常, 2.0=激进大改
    返回: (新参数, 变更描述)
    """
    new_params = copy.deepcopy(params)
    param_specs = config["params"]
    param_keys = list(param_specs.keys())

    # 随机选 1-2 个参数
    n_changes = random.choice([1, 1, 1, 2])  # 75% 改 1 个，25% 改 2 个
    selected = random.sample(param_keys, min(n_changes, len(param_keys)))

    changes = []
    for key in selected:
        spec = param_specs[key]
        old_val = new_params.get(key, spec["current"])

        # 变异幅度
        step = spec["step"] * temperature
        direction = random.choice([-1, 1])

        # 有时候大跳（探索），大部分时候小步（利用）
        if random.random() < 0.15:  # 15% 概率大跳
            jump = random.randint(2, 5)
        else:
            jump = 1

        delta = step * direction * jump

        if spec["type"] == "int":
            new_val = int(round(old_val + delta))
        else:
            new_val = round(old_val + delta, 2)

        # 边界约束
        new_val = max(spec["min"], min(spec["max"], new_val))

        # EMA 约束：快线必须 < 慢线
        if key == "ema_fast" and new_val >= new_params.get("ema_slow", 50):
            new_val = new_params.get("ema_slow", 50) - 5
        if key == "ema_slow" and new_val <= new_params.get("ema_fast", 20):
            new_val = new_params.get("ema_fast", 20) + 5

        if new_val != old_val:
            new_params[key] = new_val
            changes.append(f"{key}: {old_val}→{new_val}")

    if not changes:
        # 强制至少一个改动
        key = random.choice(param_keys)
        spec = param_specs[key]
        old_val = new_params.get(key, spec["current"])
        new_val = old_val + spec["step"] * random.choice([-1, 1])
        if spec["type"] == "int":
            new_val = int(round(new_val))
        new_val = max(spec["min"], min(spec["max"], round(new_val, 2)))
        new_params[key] = new_val
        changes.append(f"{key}: {old_val}→{new_val}")

    return new_params, " | ".join(changes)


async def run_backtest_with_params(params: dict, df_cache: dict,
                                   instruments: list, initial_equity: float) -> tuple[float, BacktestResult]:
    """用给定参数跑所有品种的回测，返回加权平均评分"""
    total_score = 0
    combined_result = BacktestResult()
    combined_result.initial_equity = initial_equity
    n = 0

    for inst_id in instruments:
        if inst_id not in df_cache or df_cache[inst_id].empty:
            continue

        cfg = params_to_backtest_config(params, inst_id)
        engine = BacktestEngine(cfg)
        result = engine.run(df_cache[inst_id], inst_id)

        score = calc_score(result, initial_equity)
        if score > -900:
            total_score += score
            n += 1

        # 累加
        combined_result.total_trades += result.total_trades
        combined_result.wins += result.wins
        combined_result.losses += result.losses
        combined_result.net_pnl += result.net_pnl
        combined_result.total_fee += result.total_fee
        if result.max_drawdown_pct > combined_result.max_drawdown_pct:
            combined_result.max_drawdown_pct = result.max_drawdown_pct
        combined_result.sharpe_ratio += result.sharpe_ratio

    if n > 0:
        avg_score = total_score / n
        combined_result.sharpe_ratio /= n
        combined_result.win_rate = combined_result.wins / max(combined_result.total_trades, 1)
        combined_result.final_equity = initial_equity + combined_result.net_pnl
    else:
        avg_score = -999.0

    return avg_score, combined_result


async def main():
    parser = argparse.ArgumentParser(description="神州99 自主策略优化")
    parser.add_argument("--rounds", type=int, default=50, help="优化轮数")
    parser.add_argument("--inst", type=str, default=None, help="单品种优化")
    parser.add_argument("--all", action="store_true", help="全品种优化")
    parser.add_argument("--days", type=int, default=90, help="回测天数")
    parser.add_argument("--equity", type=float, default=1500.0, help="初始资金")
    parser.add_argument("--show-best", action="store_true", help="显示当前最优参数")
    parser.add_argument("--temperature", type=float, default=1.0, help="变异幅度 (0.5=保守, 2.0=激进)")
    args = parser.parse_args()

    if args.show_best:
        params = load_best_params()
        print("\n═══ 当前最优参数 ═══")
        for k, v in sorted(params.items()):
            print(f"  {k}: {v}")

        if os.path.exists(RESULTS_FILE):
            print(f"\n═══ 实验历史 ═══")
            with open(RESULTS_FILE, "r") as f:
                lines = f.readlines()
            print(lines[0].strip())  # header
            for line in lines[-10:]:  # 最近10条
                print(line.strip())
        return

    # 确定品种
    if args.inst:
        instruments = [args.inst]
    elif args.all:
        instruments = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
                       "DOGE-USDT-SWAP", "ATOM-USDT-SWAP"]
    else:
        instruments = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]

    # 加载配置
    config = load_optimize_config()
    best_params = load_best_params()
    init_results_file()

    # 预加载K线数据（一次性，避免每轮都请求API）
    print(f"\n{'='*60}")
    print(f"  神州99 自主策略优化引擎")
    print(f"  品种: {', '.join(instruments)}")
    print(f"  轮数: {args.rounds} | 回测: {args.days}天 | 资金: ${args.equity}")
    print(f"{'='*60}")

    df_cache = {}
    for inst_id in instruments:
        print(f"  ⏳ 加载 {inst_id} K线数据...")
        df_cache[inst_id] = await fetch_klines(inst_id, "4H", args.days)
        if df_cache[inst_id].empty:
            print(f"  ❌ {inst_id} 数据获取失败")
        else:
            print(f"  ✅ {inst_id}: {len(df_cache[inst_id])} 根4H K线")
        await asyncio.sleep(0.3)

    # 基线评分
    print(f"\n  📊 评估基线参数...")
    best_score, baseline_result = await run_backtest_with_params(
        best_params, df_cache, instruments, args.equity
    )
    log_result(0, best_score, baseline_result, "baseline",
               "初始参数")

    print(f"  基线评分: {best_score:.4f}")
    print(f"  净利润: ${baseline_result.net_pnl:.2f}")
    print(f"  胜率: {baseline_result.win_rate:.1%}")
    print(f"  最大回撤: {baseline_result.max_drawdown_pct:.1f}%")

    # ═══ 主循环 ═══
    kept = 0
    discarded = 0
    best_ever_score = best_score

    print(f"\n{'─'*60}")
    print(f"  🚀 开始优化循环 ({args.rounds} 轮)")
    print(f"{'─'*60}\n")

    for round_num in range(1, args.rounds + 1):
        # 1. 变异参数
        # 随着轮次增加，逐渐降低 temperature（从探索到利用）
        temp = args.temperature * max(0.5, 1.0 - round_num / args.rounds * 0.5)
        new_params, changes = mutate_params(best_params, config, temp)

        # 2. 回测
        new_score, new_result = await run_backtest_with_params(
            new_params, df_cache, instruments, args.equity
        )

        # 3. 判断
        improved = new_score > best_score

        if improved:
            status = "keep"
            best_params = new_params
            best_score = new_score
            save_best_params(best_params)
            kept += 1
            marker = "✅"

            if new_score > best_ever_score:
                best_ever_score = new_score
                marker = "🏆"
        else:
            status = "discard"
            discarded += 1
            marker = "❌"

        # 4. 记录
        log_result(round_num, new_score, new_result, status, changes)

        # 5. 打印
        pnl_str = f"${new_result.net_pnl:+.2f}"
        wr_str = f"{new_result.win_rate:.0%}"
        dd_str = f"{new_result.max_drawdown_pct:.1f}%"
        print(f"  {marker} R{round_num:>3} | 评分={new_score:>8.4f} | "
              f"PnL={pnl_str:>9} WR={wr_str:>4} DD={dd_str:>5} | "
              f"{changes}")

    # ═══ 总结 ═══
    print(f"\n{'='*60}")
    print(f"  优化完成！")
    print(f"{'='*60}")
    print(f"  总轮数:    {args.rounds}")
    print(f"  保留:      {kept} ({kept/args.rounds*100:.0f}%)")
    print(f"  丢弃:      {discarded} ({discarded/args.rounds*100:.0f}%)")
    print(f"  基线评分:  {baseline_result.net_pnl:+.2f} (score={calc_score(baseline_result, args.equity):.4f})")
    print(f"  最优评分:  score={best_ever_score:.4f}")
    print(f"{'─'*60}")
    print(f"  最优参数:")
    for k, v in sorted(best_params.items()):
        print(f"    {k}: {v}")
    print(f"{'─'*60}")
    print(f"  参数已保存到: {BEST_PARAMS_FILE}")
    print(f"  实验日志:     {RESULTS_FILE}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
