# -*- coding: utf-8 -*-
"""把生成的 v4 风格推送文案发到微信（Server酱 sctapi.ftqq.com）。

用法：
    python push_send.py <SENDKEY> [md文件路径]
    # 或作为模块：
    from push_send import send, find_md
    send(os.environ["SERVERCHAN_KEY"], find_md())

SendKey 来自 Server酱 官网「Key」一栏（SCT 开头）。
文案默认读 sixlayer/微信推送-v4风格-<date>.md（取最新一份）。
"""
import os, sys, json, glob, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))


def find_md():
    fs = sorted(glob.glob(os.path.join(HERE, "微信推送-v4风格-*.md")))
    return fs[-1] if fs else None


def send(key, md):
    """发送 md 文案到 Server酱。返回 (code, msg)。"""
    if not key:
        return (None, "缺少 SERVERCHAN_KEY")
    if not md or not os.path.exists(md):
        return (None, "找不到文案 md: %s" % md)
    text = open(md, encoding="utf-8").read()
    # 标题优先取 v5 的 🔍 日报标题行，其次 # 标题行，最后兜底
    title = "A股六层过滤复盘"
    for ln in text.splitlines():
        t = ln.strip()
        if t.startswith("🔍"):
            title = t.lstrip("🔍").strip().strip("*")
            break
        if t.startswith("#"):
            title = t.lstrip("#").strip()
            break
    if len(text) > 28000:
        text = text[:27800] + "\n\n……内容过长已截断"
    url = "https://sctapi.ftqq.com/%s.send" % key
    data = urllib.parse.urlencode({"title": title, "desp": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
        try:
            code = json.loads(body).get("code")
        except Exception:
            code = None
        msg = "Server酱 返回: %s %s" % (r.status, body[:200])
        return (code, msg)
    except Exception as e:
        return (None, "请求异常: %s" % e)


def main():
    if len(sys.argv) < 2:
        key = (os.environ.get("SERVERCHAN_KEY") or "").strip()
        if not key:
            print("用法: python push_send.py <SENDKEY> [md路径]")
            sys.exit(2)
    else:
        key = sys.argv[1].strip().strip("\"'")
    md = sys.argv[2] if len(sys.argv) > 2 else find_md()
    code, msg = send(key, md)
    print(msg)
    if code == 0:
        print("✅ 推送成功")
        sys.exit(0)
    else:
        print("!! 推送失败（code=%s）：SendKey 错 / 或当日 5 条免费用完" % code)
        sys.exit(1)


if __name__ == "__main__":
    main()
