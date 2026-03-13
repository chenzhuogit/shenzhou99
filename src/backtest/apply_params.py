"""
读取 best_params.json 并应用到 engine.py

这个脚本会修改 engine.py 里的策略参数，使其与优化结果一致。
由 auto_optimize_cron.sh 调用。
"""

import os
import re
import json
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BEST_PARAMS = os.path.join(os.path.dirname(__file__), "best_params.json")
ENGINE_FILE = os.path.join(PROJECT_ROOT, "src/core/engine.py")


def load_params() -> dict:
    with open(BEST_PARAMS, "r") as f:
        return json.load(f)


def apply_params():
    params = load_params()
    
    with open(ENGINE_FILE, "r") as f:
        code = f.read()
    
    original = code
    changes = []

    # 1. ADX 门槛（非 ETH）
    adx = params.get("adx_threshold", 26)
    pattern = r'(adx_min = 30 if "ETH" in inst_id else )\d+'
    new_code = re.sub(pattern, rf'\g<1>{adx}', code)
    if new_code != code:
        changes.append(f"adx_threshold: {adx}")
        code = new_code

    # 2. ADX 门槛（ETH）
    adx_eth = params.get("adx_threshold_eth", 30)
    pattern = r'(adx_min = )\d+( if "ETH" in inst_id)'
    new_code = re.sub(pattern, rf'\g<1>{adx_eth}\2', code)
    if new_code != code:
        changes.append(f"adx_threshold_eth: {adx_eth}")
        code = new_code

    # 3. EMA 慢线周期
    ema_slow = params.get("ema_slow", 53)
    # 只改 engine.py 里所有 span=XX（非 span=20 的）
    pattern = r'(ewm\(span=)(\d+)(, adjust=False\)\.mean\(\))'
    def replace_ema_slow(m):
        old_val = int(m.group(2))
        if old_val >= 40:  # 慢线范围
            return f'{m.group(1)}{ema_slow}{m.group(3)}'
        return m.group(0)
    new_code = re.sub(pattern, replace_ema_slow, code)
    if new_code != code:
        changes.append(f"ema_slow: {ema_slow}")
        code = new_code

    # 4. TP ATR 倍数
    tp = params.get("tp_atr_mult", 2.35)
    # 匹配 tp = current_price + atr_4h * X.XX 和 tp = current_price - atr_4h * X.XX
    pattern = r'(tp = current_price [+-] atr_4h \* )[\d.]+'
    new_code = re.sub(pattern, rf'\g<1>{tp}', code)
    if new_code != code:
        changes.append(f"tp_atr_mult: {tp}")
        code = new_code

    # 5. 移动止盈跟随距离
    trail_dist = params.get("trailing_distance_atr", 0.75)
    pattern = r'(highest_profit_atr" - )[\d.]+'
    new_code = re.sub(pattern, rf'\g<1>{trail_dist}', code)
    if new_code != code:
        changes.append(f"trailing_distance: {trail_dist}")
        code = new_code

    # 阶梯移动止损
    pattern = r'(\* atr \* )[\d.]+'
    def replace_trail(m):
        old = float(m.group(0).split('* ')[-1])
        if 0.5 <= old <= 1.0:  # 只改跟随距离范围的
            return f'{m.group(1)}{trail_dist}'
        return m.group(0)
    new_code = re.sub(pattern, replace_trail, code)
    if new_code != code:
        code = new_code

    if code != original:
        with open(ENGINE_FILE, "w") as f:
            f.write(code)
        print(f"✅ 已应用 {len(changes)} 项参数更新:")
        for c in changes:
            print(f"   {c}")
    else:
        print("📊 参数无变化，无需更新")


if __name__ == "__main__":
    apply_params()
