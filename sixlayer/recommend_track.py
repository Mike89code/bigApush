#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""昨日推荐回顾（v5 推送新增）——跨日状态跟踪。

每天 run 把"相对择优"组的 code/name/score 写入 last_recommend.json；
次日 run 读取，用 stock_kline 算这些票"入选日收盘 → 今日收盘"的涨跌幅，
生成回顾板块（达标 ✅ / 止损 ❌）。

首跑无历史 → 返回 None，生成器显示"暂无历史推荐可回顾"。
"""
import os
import json
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "last_recommend.json")

# 达标/止损阈值（相对入选日收盘）
HIT = 1.5      # 涨超 1.5% 算达标
STOP = -3.0    # 跌超 3% 算止损


def save(date, picks):
    """picks: list of {code, name, score}（相对择优组）。"""
    data = {"date": date, "picks": picks}
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def load_prev():
    if not os.path.exists(PATH):
        return None
    try:
        return json.load(open(PATH, encoding="utf-8"))
    except Exception:
        return None


def review(prev, db):
    """算 prev 里每只票从入选日到今日的收益。db = stock_selection.db 路径。"""
    if not prev:
        return None
    sel_date = prev.get("date")
    conn = sqlite3.connect(db)
    out = []
    try:
        for p in prev.get("picks", []):
            code = p.get("code")
            r0 = conn.execute("select close from stock_kline where code=? and date=?",
                              (code, sel_date)).fetchone()
            r1 = conn.execute("select close from stock_kline where code=? "
                              "order by date desc limit 1", (code,)).fetchone()
            if not r0 or not r1 or not r0[0]:
                out.append({**p, "ret": None})
                continue
            ret = round((r1[0] - r0[0]) / r0[0] * 100.0, 2)
            out.append({**p, "ret": ret})
    finally:
        conn.close()
    return {"date": sel_date, "items": out}


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "stock_selection.db")
    prev = load_prev()
    print(json.dumps(review(prev, db), ensure_ascii=False, indent=1))
