"""清除示例数据，准备接入真实数据"""
import pymysql

conn = pymysql.connect(
    host="rm-hp3mjdo8oxy5j35aqko.mysql.huhehaote.rds.aliyuncs.com",
    port=3306, user="application", password="Trxwtfd12348765#$",
    db="shenzhou99", charset="utf8mb4",
)
c = conn.cursor()

tables = ["account_assets", "account_snapshots", "positions", "orders",
          "trades", "signals", "risk_logs", "system_logs", "funding_rates"]

for t in tables:
    c.execute(f"TRUNCATE TABLE `{t}`")
    print(f"  🗑️  {t} 已清空")

conn.commit()
conn.close()
print("\n✅ 示例数据已清除，准备接入真实数据")
