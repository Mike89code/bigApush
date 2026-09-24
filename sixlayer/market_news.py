#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""财经要闻 3 条（新浪 7x24 快讯），供 v5 推送「今日要闻」板块。

数据源：zhibo.sina.com.cn 财经直播（免 key，CI/本地均可访问）。
筛选逻辑：只保留能映射出「影响点评」的快讯，取前 3 条。
失败返回 None，生成器降级为占位行，不阻塞推送。
"""
import re
import json
import urllib.request

API = ("https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=50"
       "&zhibo_id=152&tag_id=0")
HDRS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

# 关键词 → 影响点评（离线规则，宁可粗也别错）
IMPACT = [
    (r"央行|降准|降息|LPR|逆回购|MLF|流动性", "流动性信号，关注资金面"),
    (r"中美|贸易|关税|磋商|出口管制", "影响外贸与市场情绪"),
    (r"美联储|美股|纳斯达克|道琼斯|隔夜外盘", "关注隔夜外盘联动"),
    (r"原油|成品油|油价|OPEC", "关注油气链"),
    (r"黄金|贵金属|金价", "关注贵金属板块"),
    (r"芯片|半导体|算力|AI|人工智能|大模型", "关注科技/AI链"),
    (r"新能源|锂电|光伏|储能|充电", "关注新能源链"),
    (r"医药|集采|创新药|疫苗", "关注医药链"),
    (r"地产|房地产|楼市|房贷", "关注地产链"),
    (r"汽车|车企|以旧换新|智能驾驶", "关注汽车链"),
    (r"国庆|假期|高速免费|出行|旅游", "关注出行消费链"),
    (r"稀土|有色|铜|铝", "关注有色板块"),
]


def _clean(txt):
    t = re.sub(r"\s+", "", txt or "")
    # 去掉开头的【】标题符号，微信里更清爽
    t = re.sub(r"^【([^】]*)】", r"\1｜", t)
    return t[:55] + "…" if len(t) > 55 else t


def fetch_top3():
    """返回 [{t: 标题, c: 影响点评}] × 3，失败返回 None。"""
    try:
        req = urllib.request.Request(API, headers=HDRS)
        raw = urllib.request.urlopen(req, timeout=15).read()
        data = json.loads(raw.decode("utf-8", "replace"))
        lst = (data.get("result", {}).get("data", {})
               .get("feed", {}).get("list", []))
    except Exception:
        return None
    picks, seen = [], set()
    for it in lst:
        rich = _clean(it.get("rich_text") or it.get("text") or "")
        if len(rich) < 12:
            continue
        key = rich[:10]
        if key in seen:
            continue
        seen.add(key)
        impact = None
        for pat, c in IMPACT:
            if re.search(pat, rich):
                impact = c
                break
        if impact:
            picks.append({"t": rich, "c": impact})
        if len(picks) >= 3:
            break
    return picks or None


if __name__ == "__main__":
    for x in (fetch_top3() or []):
        print("•", x["t"], "→", x["c"])
