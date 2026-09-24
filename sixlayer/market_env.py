#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大盘环境判定（v5 推送新增）。

抓四大指数当日涨跌幅，算大盘评分 + 建议仓位；暴跌日给出空仓信号。
数据源：腾讯 qt.gtimg.cn（CI 与本地均可达）。抓取失败优雅降级，绝不崩。
"""
import os
import re
import json
import ssl
import time
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 四大指数：代码 -> 名称
INDICES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
}


def _fetch():
    q = ",".join(INDICES.keys())
    u = "https://qt.gtimg.cn/q=%s" % q
    for a in range(3):
        try:
            req = urllib.request.Request(u, headers=H)
            with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
                raw = r.read()
            try:
                txt = raw.decode("utf-8", "replace")
            except Exception:
                txt = raw.decode("gbk", "replace")
            return txt
        except Exception:
            if a == 2:
                return ""
            time.sleep(1.0 + a)
    return ""


def _parse(txt):
    out = []
    for line in txt.splitlines():
        m = re.search(r'v_(\w+)="([^"]*)"', line)
        if not m:
            continue
        code = m.group(1)
        if code not in INDICES:
            continue
        f = m.group(2).split("~")
        try:
            cur = float(f[3])
            prev = float(f[4])
        except (IndexError, ValueError):
            continue
        if not prev:
            continue
        pct = (cur / prev - 1.0) * 100.0
        out.append({"code": code, "name": INDICES[code],
                    "pct": round(pct, 2), "cur": round(cur, 2)})
    return out


def _score(avg_pct):
    # avg -2% -> ~10分；0% -> 50分；+2% -> 90分
    s = 50 + avg_pct * 20.0
    return max(0, min(100, round(s)))


def _position(score):
    if score >= 60:
        return "≤ 8 成（偏多，可积极）"
    if score >= 45:
        return "≤ 5 成（中性）"
    if score >= 30:
        return "≤ 3 成（弱势，控仓）"
    return "≤ 1 成（极弱）"


def _adv_dec():
    """全市场涨跌家数（东财 clist 分页，pz=100 上限 → 翻页找零轴）。

    返回 (adv, dec) 或 None；失败不阻塞主流程。
    """
    base = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&np=1"
            "&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f3")
    hdrs = {"User-Agent": "Mozilla/5.0"}

    def _count(desc):
        """desc=1 从涨幅最高往下翻 → 涨家数；desc=0 → 跌家数。"""
        n, page = 0, 1
        while page <= 60:
            try:
                u = base % page
                u += "&po=%d" % (1 if desc else 0)
                req = urllib.request.Request(u, headers=hdrs)
                d = json.loads(urllib.request.urlopen(req, timeout=10, context=ctx)
                               .read().decode("utf-8", "replace"))
                diff = (d.get("data") or {}).get("diff") or []
                if not diff:
                    break
                hit_zero = False
                for x in diff:
                    v = x.get("f3")
                    if not isinstance(v, (int, float)):
                        continue
                    if desc and v <= 0:
                        hit_zero = True
                        break
                    if not desc and v >= 0:
                        hit_zero = True
                        break
                    n += 1
                if hit_zero:
                    return n
                page += 1
            except Exception:
                return None
        return None

    adv = _count(1)
    dec = _count(0)
    if adv is None or dec is None or (adv + dec) < 100:
        return None
    return adv, dec


def get_env():
    """返回大盘环境字典；失败返回 None（调用方优雅降级）。"""
    txt = _fetch()
    if not txt:
        return None
    indices = _parse(txt)
    if not indices:
        return None
    pcts = [x["pct"] for x in indices]
    avg = sum(pcts) / len(pcts)
    score = _score(avg)
    crash = avg <= -2.0
    if avg <= -2.0:
        mood = "🔴 暴跌"
    elif avg <= -0.5:
        mood = "🔴 弱势"
    elif avg < 0.5:
        mood = "🟡 震荡"
    elif avg < 1.5:
        mood = "🟢 偏强"
    else:
        mood = "🟢 强势"
    rng = "%+.2f%%~%+.2f%%" % (min(pcts), max(pcts))
    env = {
        "indices": indices,
        "avg_pct": round(avg, 2),
        "score": score,
        "mood": mood,
        "range": rng,
        "position": _position(score),
        "crash": crash,
    }
    try:
        ad = _adv_dec()
        if ad:
            env["adv"], env["dec"] = ad
    except Exception:
        pass
    return env


if __name__ == "__main__":
    import json
    print(json.dumps(get_env(), ensure_ascii=False, indent=1))
