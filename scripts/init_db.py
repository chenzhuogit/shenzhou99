"""神州99 数据库初始化脚本"""
import pymysql

DB_CONFIG = {
    "host": "rm-hp3mjdo8oxy5j35aqko.mysql.huhehaote.rds.aliyuncs.com",
    "port": 3306,
    "user": "application",
    "password": "Trxwtfd12348765#$",
}

DB_NAME = "shenzhou99"

TABLES = [
    # ═══ 账户资产表 ═══
    """
    CREATE TABLE IF NOT EXISTS account_assets (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        currency VARCHAR(20) NOT NULL COMMENT '币种',
        balance DECIMAL(30,10) NOT NULL DEFAULT 0 COMMENT '余额',
        frozen DECIMAL(30,10) NOT NULL DEFAULT 0 COMMENT '冻结',
        available DECIMAL(30,10) NOT NULL DEFAULT 0 COMMENT '可用',
        usd_value DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT 'USD估值',
        equity DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '币种权益',
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_currency (currency)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='账户资产'
    """,

    # ═══ 账户快照表（定时记录净值） ═══
    """
    CREATE TABLE IF NOT EXISTS account_snapshots (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        total_equity DECIMAL(20,4) NOT NULL COMMENT '总权益(USD)',
        available_equity DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '可用权益',
        unrealized_pnl DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '未实现盈亏',
        margin_used DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '已用保证金',
        margin_ratio DECIMAL(10,4) DEFAULT NULL COMMENT '保证金率',
        max_equity DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '历史最高净值',
        drawdown DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '当前回撤比例',
        snapshot_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_snapshot_at (snapshot_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='账户净值快照'
    """,

    # ═══ 持仓表 ═══
    """
    CREATE TABLE IF NOT EXISTS positions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        inst_id VARCHAR(30) NOT NULL COMMENT '产品ID',
        inst_type VARCHAR(10) NOT NULL COMMENT '产品类型 SPOT/SWAP/FUTURES',
        pos_side VARCHAR(10) NOT NULL COMMENT '持仓方向 long/short/net',
        size DECIMAL(30,10) NOT NULL DEFAULT 0 COMMENT '持仓量',
        avg_price DECIMAL(20,8) NOT NULL DEFAULT 0 COMMENT '开仓均价',
        current_price DECIMAL(20,8) NOT NULL DEFAULT 0 COMMENT '当前价',
        mark_price DECIMAL(20,8) NOT NULL DEFAULT 0 COMMENT '标记价',
        liquidation_price DECIMAL(20,8) DEFAULT NULL COMMENT '强平价',
        margin DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '保证金',
        margin_mode VARCHAR(10) NOT NULL DEFAULT 'isolated' COMMENT 'cross/isolated',
        leverage INT NOT NULL DEFAULT 1 COMMENT '杠杆倍数',
        unrealized_pnl DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '未实现盈亏',
        realized_pnl DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '已实现盈亏',
        pnl_ratio DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT '收益率',
        stop_loss DECIMAL(20,8) DEFAULT NULL COMMENT '止损价',
        take_profit DECIMAL(20,8) DEFAULT NULL COMMENT '止盈价',
        strategy_name VARCHAR(50) DEFAULT NULL COMMENT '策略名称',
        status VARCHAR(10) NOT NULL DEFAULT 'open' COMMENT 'open/closed',
        opened_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开仓时间',
        closed_at TIMESTAMP NULL DEFAULT NULL COMMENT '平仓时间',
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_inst_status (inst_id, status),
        INDEX idx_strategy (strategy_name),
        INDEX idx_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='持仓记录'
    """,

    # ═══ 订单表 ═══
    """
    CREATE TABLE IF NOT EXISTS orders (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        ord_id VARCHAR(30) DEFAULT NULL COMMENT 'OKX订单ID',
        cl_ord_id VARCHAR(50) DEFAULT NULL COMMENT '自定义订单ID',
        inst_id VARCHAR(30) NOT NULL COMMENT '产品ID',
        inst_type VARCHAR(10) NOT NULL COMMENT '产品类型',
        ord_type VARCHAR(20) NOT NULL COMMENT '订单类型 market/limit/post_only',
        side VARCHAR(10) NOT NULL COMMENT 'buy/sell',
        pos_side VARCHAR(10) DEFAULT NULL COMMENT 'long/short',
        size DECIMAL(30,10) NOT NULL COMMENT '委托数量',
        price DECIMAL(20,8) DEFAULT NULL COMMENT '委托价格',
        filled_size DECIMAL(30,10) NOT NULL DEFAULT 0 COMMENT '已成交数量',
        filled_price DECIMAL(20,8) DEFAULT NULL COMMENT '成交均价',
        fee DECIMAL(20,8) NOT NULL DEFAULT 0 COMMENT '手续费',
        pnl DECIMAL(20,4) DEFAULT NULL COMMENT '盈亏',
        slippage DECIMAL(10,6) DEFAULT NULL COMMENT '滑点',
        strategy_name VARCHAR(50) DEFAULT NULL COMMENT '策略名称',
        signal_reason TEXT DEFAULT NULL COMMENT '信号原因',
        status VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT 'pending/partially_filled/filled/canceled/failed',
        error_msg TEXT DEFAULT NULL COMMENT '错误信息',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_ord_id (ord_id),
        INDEX idx_cl_ord_id (cl_ord_id),
        INDEX idx_inst_id (inst_id),
        INDEX idx_strategy (strategy_name),
        INDEX idx_status (status),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单记录'
    """,

    # ═══ 交易记录表（成交明细） ═══
    """
    CREATE TABLE IF NOT EXISTS trades (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_id VARCHAR(30) DEFAULT NULL COMMENT 'OKX成交ID',
        ord_id VARCHAR(30) DEFAULT NULL COMMENT '关联订单ID',
        inst_id VARCHAR(30) NOT NULL COMMENT '产品ID',
        inst_type VARCHAR(10) NOT NULL COMMENT '产品类型',
        side VARCHAR(10) NOT NULL COMMENT 'buy/sell',
        pos_side VARCHAR(10) DEFAULT NULL COMMENT 'long/short',
        price DECIMAL(20,8) NOT NULL COMMENT '成交价',
        size DECIMAL(30,10) NOT NULL COMMENT '成交量',
        fee DECIMAL(20,8) NOT NULL DEFAULT 0 COMMENT '手续费',
        fee_currency VARCHAR(10) DEFAULT NULL COMMENT '手续费币种',
        pnl DECIMAL(20,4) DEFAULT NULL COMMENT '盈亏',
        strategy_name VARCHAR(50) DEFAULT NULL COMMENT '策略名称',
        traded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '成交时间',
        INDEX idx_trade_id (trade_id),
        INDEX idx_ord_id (ord_id),
        INDEX idx_inst_id (inst_id),
        INDEX idx_strategy (strategy_name),
        INDEX idx_traded (traded_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='成交明细'
    """,

    # ═══ 交易信号表 ═══
    """
    CREATE TABLE IF NOT EXISTS signals (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        strategy_name VARCHAR(50) NOT NULL COMMENT '策略名称',
        inst_id VARCHAR(30) NOT NULL COMMENT '产品ID',
        inst_type VARCHAR(10) NOT NULL COMMENT '产品类型',
        action VARCHAR(20) NOT NULL COMMENT '信号动作',
        price DECIMAL(20,8) NOT NULL COMMENT '信号价格',
        stop_loss DECIMAL(20,8) NOT NULL COMMENT '止损价',
        take_profit DECIMAL(20,8) DEFAULT NULL COMMENT '止盈价',
        reward_risk_ratio DECIMAL(10,4) DEFAULT NULL COMMENT '盈亏比',
        confidence DECIMAL(5,4) NOT NULL DEFAULT 0.5 COMMENT '信心度',
        reason TEXT DEFAULT NULL COMMENT '信号原因',
        risk_check_passed TINYINT(1) NOT NULL DEFAULT 0 COMMENT '风控是否通过',
        risk_check_reason VARCHAR(200) DEFAULT NULL COMMENT '风控审核结果',
        executed TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否已执行',
        ord_id VARCHAR(30) DEFAULT NULL COMMENT '关联订单ID',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_strategy (strategy_name),
        INDEX idx_inst_id (inst_id),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易信号'
    """,

    # ═══ 风控日志表 ═══
    """
    CREATE TABLE IF NOT EXISTS risk_logs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        event_type VARCHAR(30) NOT NULL COMMENT '事件类型 signal_check/daily_loss/drawdown/flash_crash/spread',
        inst_id VARCHAR(30) DEFAULT NULL COMMENT '相关品种',
        strategy_name VARCHAR(50) DEFAULT NULL COMMENT '相关策略',
        detail TEXT NOT NULL COMMENT '详情',
        action_taken VARCHAR(50) DEFAULT NULL COMMENT '采取的措施',
        equity DECIMAL(20,4) DEFAULT NULL COMMENT '当时净值',
        drawdown DECIMAL(10,6) DEFAULT NULL COMMENT '当时回撤',
        daily_pnl DECIMAL(20,4) DEFAULT NULL COMMENT '当日盈亏',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_event_type (event_type),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='风控日志'
    """,

    # ═══ 系统日志表 ═══
    """
    CREATE TABLE IF NOT EXISTS system_logs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        level VARCHAR(10) NOT NULL COMMENT 'DEBUG/INFO/WARN/ERROR/CRITICAL',
        module VARCHAR(50) NOT NULL COMMENT '模块名',
        message TEXT NOT NULL COMMENT '日志内容',
        extra JSON DEFAULT NULL COMMENT '额外数据',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_level (level),
        INDEX idx_module (module),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统日志'
    """,

    # ═══ 模块状态表 ═══
    """
    CREATE TABLE IF NOT EXISTS module_status (
        id INT AUTO_INCREMENT PRIMARY KEY,
        module_name VARCHAR(50) NOT NULL COMMENT '模块名称',
        status VARCHAR(10) NOT NULL DEFAULT 'ok' COMMENT 'ok/warn/error',
        detail VARCHAR(200) DEFAULT NULL COMMENT '状态详情',
        error_msg TEXT DEFAULT NULL COMMENT '错误信息',
        last_heartbeat TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最后心跳',
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_module (module_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模块状态'
    """,

    # ═══ K线数据表 ═══
    """
    CREATE TABLE IF NOT EXISTS kline_data (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        inst_id VARCHAR(30) NOT NULL COMMENT '产品ID',
        bar VARCHAR(10) NOT NULL COMMENT '周期 1m/5m/15m/1H/4H/1D',
        ts BIGINT NOT NULL COMMENT '时间戳(ms)',
        open DECIMAL(20,8) NOT NULL,
        high DECIMAL(20,8) NOT NULL,
        low DECIMAL(20,8) NOT NULL,
        close DECIMAL(20,8) NOT NULL,
        vol DECIMAL(30,4) NOT NULL DEFAULT 0 COMMENT '成交量',
        vol_ccy DECIMAL(30,4) NOT NULL DEFAULT 0 COMMENT '成交额',
        UNIQUE KEY uk_inst_bar_ts (inst_id, bar, ts),
        INDEX idx_inst_bar (inst_id, bar),
        INDEX idx_ts (ts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='K线数据'
    """,

    # ═══ 策略绩效表 ═══
    """
    CREATE TABLE IF NOT EXISTS strategy_performance (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        strategy_name VARCHAR(50) NOT NULL COMMENT '策略名',
        date DATE NOT NULL COMMENT '日期',
        total_trades INT NOT NULL DEFAULT 0 COMMENT '交易次数',
        winning_trades INT NOT NULL DEFAULT 0 COMMENT '盈利次数',
        losing_trades INT NOT NULL DEFAULT 0 COMMENT '亏损次数',
        win_rate DECIMAL(6,4) NOT NULL DEFAULT 0 COMMENT '胜率',
        total_pnl DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '总盈亏',
        avg_win DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '平均盈利',
        avg_loss DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '平均亏损',
        max_win DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '最大单笔盈利',
        max_loss DECIMAL(20,4) NOT NULL DEFAULT 0 COMMENT '最大单笔亏损',
        sharpe_ratio DECIMAL(10,4) DEFAULT NULL COMMENT '夏普比率',
        max_drawdown DECIMAL(10,6) DEFAULT NULL COMMENT '最大回撤',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_strategy_date (strategy_name, date),
        INDEX idx_date (date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略每日绩效'
    """,

    # ═══ 资金费率记录表 ═══
    """
    CREATE TABLE IF NOT EXISTS funding_rates (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        inst_id VARCHAR(30) NOT NULL COMMENT '产品ID',
        funding_rate DECIMAL(12,8) NOT NULL COMMENT '资金费率',
        next_funding_time BIGINT DEFAULT NULL COMMENT '下次收取时间',
        recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_inst (inst_id),
        INDEX idx_recorded (recorded_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资金费率记录'
    """,

    # ═══ 系统配置表 ═══
    """
    CREATE TABLE IF NOT EXISTS system_config (
        id INT AUTO_INCREMENT PRIMARY KEY,
        config_key VARCHAR(100) NOT NULL COMMENT '配置键',
        config_value TEXT NOT NULL COMMENT '配置值',
        description VARCHAR(200) DEFAULT NULL COMMENT '描述',
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_key (config_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统配置'
    """,
]

# 初始化模块状态数据
INIT_MODULES = [
    ("trading_engine", "ok", "就绪"),
    ("okx_websocket", "ok", "待连接"),
    ("okx_rest_api", "ok", "就绪"),
    ("risk_engine", "ok", "就绪"),
    ("strategy_trend", "ok", "待启动"),
    ("strategy_mean_rev", "ok", "待启动"),
    ("data_pipeline", "ok", "待启动"),
    ("backtest_engine", "warn", "空闲"),
]

# 初始化系统配置
INIT_CONFIG = [
    ("max_loss_per_trade", "0.02", "单笔最大亏损比例"),
    ("max_daily_loss", "0.05", "单日最大亏损比例"),
    ("max_drawdown", "0.10", "最大回撤比例"),
    ("max_positions", "5", "最大同时持仓数"),
    ("max_leverage", "10", "最大杠杆倍数"),
    ("min_reward_risk_ratio", "1.5", "最小盈亏比"),
    ("margin_mode", "isolated", "保证金模式"),
    ("system_mode", "paper", "运行模式 paper/live"),
]


def main():
    print("🔌 连接数据库...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # 创建数据库
    print(f"📦 创建数据库 {DB_NAME}...")
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cursor.execute(f"USE `{DB_NAME}`")

    # 创建表
    for i, sql in enumerate(TABLES, 1):
        table_name = sql.strip().split("(")[0].split()[-1].strip('`')
        print(f"  📋 [{i}/{len(TABLES)}] 创建表 {table_name}...")
        cursor.execute(sql)

    # 初始化模块状态
    print("  🔧 初始化模块状态...")
    for name, status, detail in INIT_MODULES:
        cursor.execute(
            "INSERT INTO module_status (module_name, status, detail) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE status=%s, detail=%s",
            (name, status, detail, status, detail)
        )

    # 初始化配置
    print("  ⚙️ 初始化系统配置...")
    for key, value, desc in INIT_CONFIG:
        cursor.execute(
            "INSERT INTO system_config (config_key, config_value, description) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE config_value=%s, description=%s",
            (key, value, desc, value, desc)
        )

    conn.commit()

    # 验证
    cursor.execute("SHOW TABLES")
    tables = cursor.fetchall()
    print(f"\n✅ 数据库初始化完成！共 {len(tables)} 张表：")
    for t in tables:
        cursor.execute(f"SELECT COUNT(*) FROM `{t[0]}`")
        count = cursor.fetchone()[0]
        print(f"   • {t[0]} ({count} 行)")

    cursor.close()
    conn.close()
    print("\n🎉 神州99 数据库就绪！")


if __name__ == "__main__":
    main()
