"""插入示例数据"""
import pymysql

conn = pymysql.connect(
    host="rm-hp3mjdo8oxy5j35aqko.mysql.huhehaote.rds.aliyuncs.com",
    port=3306, user="application", password="Trxwtfd12348765#$",
    db="shenzhou99", charset="utf8mb4",
)
c = conn.cursor()

# 资产
assets = [
    ("USDT", 38420.50, 0, 38420.50, 38420.50, 38420.50),
    ("BTC", 0.082, 0.05, 0.032, 7175.00, 7175.00),
    ("ETH", 1.45, 0, 1.45, 4640.00, 4640.00),
    ("SOL", 12.5, 0, 12.5, 1812.50, 1812.50),
]
for a in assets:
    c.execute(
        "INSERT INTO account_assets (currency,balance,frozen,available,usd_value,equity) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
        "balance=VALUES(balance),frozen=VALUES(frozen),available=VALUES(available),"
        "usd_value=VALUES(usd_value),equity=VALUES(equity)", a
    )

# 快照
c.execute(
    "INSERT INTO account_snapshots (total_equity,available_equity,unrealized_pnl,"
    "margin_used,margin_ratio,max_equity,drawdown) VALUES (%s,%s,%s,%s,%s,%s,%s)",
    (52048.00, 38420.50, 270.00, 3200.00, 15.38, 54200.00, 0.0397)
)

# 持仓
positions = [
    ("BTC-USDT-SWAP","SWAP","long","0.05",86200,87500,87480,82100,860,"isolated",10,65.00,0,0.0756,85000,90000,"trend_following","open"),
    ("ETH-USDT-SWAP","SWAP","short","2.0",3280,3200,3198,3500,1312,"isolated",5,160.00,0,0.1220,3400,3100,"mean_reversion","open"),
    ("SOL-USDT-SWAP","SWAP","long","15",142,145,144.8,128,426,"isolated",5,45.00,0,0.1056,138,155,"trend_following","open"),
]
for p in positions:
    c.execute(
        "INSERT INTO positions (inst_id,inst_type,pos_side,size,avg_price,current_price,"
        "mark_price,liquidation_price,margin,margin_mode,leverage,unrealized_pnl,"
        "realized_pnl,pnl_ratio,stop_loss,take_profit,strategy_name,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", p
    )

# 订单
orders = [
    ("380001","sz99_1","BTC-USDT-SWAP","SWAP","limit","buy","long","0.05",86200,"0.05",86200,1.72,None,0.0,"trend_following","趋势做多 强度=0.78","filled"),
    ("380002","sz99_2","ETH-USDT-SWAP","SWAP","limit","sell","short","2.0",3280,"2.0",3280,1.31,None,0.0,"mean_reversion","RSI超买回归","filled"),
    ("380003","sz99_3","SOL-USDT-SWAP","SWAP","limit","buy","long","15",142,"15",142,0.43,None,0.0,"trend_following","趋势做多 强度=0.72","filled"),
    ("380004","sz99_4","BTC-USDT-SWAP","SWAP","market","buy","long","0.03",85800,"0.03",85860,1.29,48.60,0.02,"momentum","动量突破","filled"),
    ("380005","sz99_5","ETH-USDT","SPOT","limit","sell",None,"0.5",3310,"0.5",3310,0.33,15.50,0.01,"grid","网格交易","filled"),
    ("380006","sz99_6","DOGE-USDT","SPOT","limit","buy",None,"5000",0.176,"5000",0.176,0.18,-12.00,-0.01,"mean_reversion","RSI超卖","filled"),
    ("380007","sz99_7","SOL-USDT-SWAP","SWAP","limit","sell","short","10",148,"10",148,0.30,32.00,0.02,"trend_following","趋势做空平仓","filled"),
    ("380008","sz99_8","BTC-USDT-SWAP","SWAP","limit","buy","long","0.02",85500,"0.02",85510,0.34,8.20,0.005,"funding_arb","资金费率套利","filled"),
]
for o in orders:
    c.execute(
        "INSERT INTO orders (ord_id,cl_ord_id,inst_id,inst_type,ord_type,side,pos_side,"
        "size,price,filled_size,filled_price,fee,pnl,slippage,strategy_name,signal_reason,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", o
    )

# 成交
trades = [
    ("T001","380004","BTC-USDT-SWAP","SWAP","buy","long",85860,"0.03",1.29,"USDT",48.60,"momentum"),
    ("T002","380005","ETH-USDT","SPOT","sell",None,3310,"0.5",0.33,"USDT",15.50,"grid"),
    ("T003","380006","DOGE-USDT","SPOT","buy",None,0.176,"5000",0.18,"USDT",-12.00,"mean_reversion"),
    ("T004","380007","SOL-USDT-SWAP","SWAP","sell","short",148,"10",0.30,"USDT",32.00,"trend_following"),
    ("T005","380008","BTC-USDT-SWAP","SWAP","buy","long",85510,"0.02",0.34,"USDT",8.20,"funding_arb"),
]
for t in trades:
    c.execute(
        "INSERT INTO trades (trade_id,ord_id,inst_id,inst_type,side,pos_side,"
        "price,size,fee,fee_currency,pnl,strategy_name) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", t
    )

# 系统日志
logs = [
    ("INFO","trading_engine","系统启动完成，进入交易模式"),
    ("INFO","okx_websocket","WebSocket 已连接，订阅 3 个公共频道"),
    ("INFO","strategy","📡 信号: trend_following | BTC-USDT-SWAP | 趋势做多 | 强度=0.78"),
    ("INFO","risk_engine","✅ 风控通过: BTC-USDT-SWAP | RR=2.1 | 风险=1.8%"),
    ("INFO","trading_engine","📈 开多 BTC-USDT-SWAP @ 86,200 | 0.05张 | 杠杆10x"),
    ("WARN","risk_engine","⚠️ RSI(14)=72.3 超买区域，确认 ETH 空头信号"),
    ("INFO","trading_engine","📉 开空 ETH-USDT-SWAP @ 3,280 | 2.0张 | 杠杆5x"),
    ("INFO","trading_engine","📈 开多 SOL-USDT-SWAP @ 142 | 15张 | 杠杆5x"),
    ("INFO","trading_engine","💰 平多 BTC-USDT-SWAP @ 85,860 | 盈利 +$48.60"),
    ("INFO","data_pipeline","📊 资金费率: BTC 0.0085% | ETH 0.0062%"),
    ("INFO","trading_engine","💰 卖出 ETH-USDT @ 3,310 | 网格盈利 +$15.50"),
    ("WARN","risk_engine","🛡️ 止损执行: DOGE-USDT @ 0.174 | 亏损 -$12.00 | 滑点 0.02%"),
    ("INFO","trading_engine","💰 平空 SOL-USDT-SWAP @ 148 | 盈利 +$32.00"),
    ("INFO","trading_engine","💰 费率套利 BTC-USDT-SWAP | 盈利 +$8.20"),
    ("INFO","risk_engine","🛡️ 风控检查: 日PnL +$92.30 | 回撤 3.97% | 正常"),
]
for l in logs:
    c.execute("INSERT INTO system_logs (level,module,message) VALUES (%s,%s,%s)", l)

# 风控日志
risk_logs = [
    ("signal_check","BTC-USDT-SWAP","trend_following","信号审核通过 | RR=2.1 | 风险=1.8%","approved",52048,0.0397,92.30),
    ("signal_check","ETH-USDT-SWAP","mean_reversion","信号审核通过 | RSI超买确认","approved",52048,0.0397,92.30),
    ("stop_loss","DOGE-USDT","mean_reversion","止损触发 @ 0.174 | 亏损 -$12.00 | 滑点 0.02%","stop_executed",52048,0.0397,92.30),
    ("daily_check",None,None,"日度风控检查: 持仓3/5 | 日PnL +$92.30 | 回撤 3.97%","normal",52048,0.0397,92.30),
]
for r in risk_logs:
    c.execute(
        "INSERT INTO risk_logs (event_type,inst_id,strategy_name,detail,action_taken,equity,drawdown,daily_pnl) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", r
    )

conn.commit()
conn.close()
print("✅ 示例数据插入完成！")
