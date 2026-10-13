# -*- coding: utf-8 -*-
"""把交接文档 md 转成自带样式的 HTML（浅色主题，双击可看，可打印）"""
import markdown, io, os

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "交接文档-股票推送系统-v5.6.md")
OUT = os.path.join(BASE, "交接文档-股票推送系统-v5.6.html")

with io.open(SRC, "r", encoding="utf-8") as f:
    text = f.read()

body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])

CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:32px 16px; background:#eef1f5;
  font-family:-apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;
  color:#1e293b; font-size:14.5px; line-height:1.75; }
.page { max-width:920px; margin:0 auto; background:#fff; border-radius:12px;
  box-shadow:0 2px 14px rgba(15,23,42,.08); padding:44px 52px 52px; }
.banner { background:linear-gradient(135deg,#1d4ed8,#3b82f6); color:#fff;
  border-radius:10px; padding:18px 22px; margin-bottom:28px; }
.banner h1 { margin:0; font-size:21px; letter-spacing:.5px; }
.banner p { margin:6px 0 0; font-size:13px; opacity:.92; }
.banner .badge { display:inline-block; background:rgba(255,255,255,.22);
  border:1px solid rgba(255,255,255,.45); border-radius:999px;
  padding:1px 12px; font-size:12px; margin-left:8px; vertical-align:2px; }
h1 { font-size:21px; color:#0f172a; border-bottom:3px solid #2563eb;
  padding-bottom:10px; margin:8px 0 20px; }
h2 { font-size:17.5px; color:#1e3a8a; border-left:5px solid #2563eb;
  padding-left:11px; margin:36px 0 14px; page-break-after:avoid; }
h3 { font-size:15px; color:#334155; margin:22px 0 8px; page-break-after:avoid; }
p { margin:9px 0; }
table { width:100%; border-collapse:collapse; margin:14px 0; font-size:13px;
  page-break-inside:avoid; }
th { background:#eff6ff; color:#1e3a8a; font-weight:600; }
th, td { border:1px solid #dbe3ee; padding:7px 11px; text-align:left;
  vertical-align:top; }
tr:nth-child(even) td { background:#f8fafc; }
pre { background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;
  padding:14px 16px; overflow-x:auto; font-size:12.5px; line-height:1.65;
  font-family:Consolas,"Cascadia Code","Courier New",monospace; }
code { font-family:Consolas,"Cascadia Code","Courier New",monospace;
  background:#f1f5f9; color:#be123c; border-radius:4px; padding:1px 5px; font-size:12.5px; }
pre code { background:none; color:#0f172a; padding:0; }
blockquote { margin:12px 0; padding:8px 16px; border-left:4px solid #93c5fd;
  background:#f0f7ff; color:#334155; border-radius:0 6px 6px 0; }
blockquote p { margin:4px 0; }
ul,ol { padding-left:26px; margin:8px 0; }
li { margin:4px 0; }
strong { color:#0f172a; }
hr { border:none; border-top:1px dashed #cbd5e1; margin:26px 0; }
a { color:#2563eb; }
.footer { margin-top:36px; padding-top:14px; border-top:1px solid #e2e8f0;
  font-size:12px; color:#94a3b8; text-align:center; }
@media print {
  body { background:#fff; padding:0; }
  .page { box-shadow:none; max-width:100%; padding:0; }
  .banner { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
}
"""

html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>交接文档-股票推送系统-v5.6</title>
<style>%s</style>
</head>
<body>
<div class="page">
<div class="banner">
  <h1>🛠️ A股六层过滤 · 微信推送系统 —— 交接工作日志<span class="badge">v5.6b 已上线</span></h1>
  <p>生成 2026-09-25 ｜ 重建 2026-10-12 ｜ 用途：交接给下一个 AI 接手维护 ｜ 每天 1 条推送（北京 15:10 左右）</p>
</div>
%s
<div class="footer">bigApush / sixlayer · 交接文档 v5.6 · 2026-10-12 重建版</div>
</div>
</body>
</html>
""" % (CSS, body)

with io.open(OUT, "w", encoding="utf-8") as f:
    f.write(html)

print("OK ->", OUT, len(html), "bytes")
