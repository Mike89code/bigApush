#!/usr/env python3
# -*- coding: utf-8 -*-
"""把 v5 推送 markdown 渲染成「微信手机版式」HTML 预览（仅用于审阅，不进 CI）。

v5.4 视觉层级（按重要性三层，告别 .lead 滥用）：
  .plan   操作计划/备选——橙色底，第一眼看到"明天怎么操作"
  .stock  首选股票——白底卡片+绿左边框（带 ⚠️ 的票橙左边框）
  .skip   避开/观察户——灰色小字，不抢焦点
  .muted  样本不足等次要警告——小字
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def md_inline(s):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return s


def render(md):
    lines = md.splitlines()
    out = []
    in_ul = False
    mode = None          # None | 'plan' | 'stock'
    stock_warn = False

    def close_block():
        nonlocal in_ul, mode, stock_warn
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if mode == "plan":
            out.append("</div>")
        elif mode == "stock":
            out.append("</div>")
        mode = None
        stock_warn = False

    for raw in lines:
        raw = raw.rstrip()
        # ---- 块结束条件：标题 / 分割线 / 空行（空行只结束 stock，plan 允许内部空行） ----
        if raw.startswith("### ") or raw.startswith("━━") or raw.startswith("🔍"):
            close_block()
        if not raw.strip():
            if mode == "stock":
                close_block()
            elif in_ul:
                out.append("</ul>")
                in_ul = False
            continue

        # ---- 操作计划块：🎯 开头，直到下一个 ###/━━/🔍 ----
        if raw.startswith("🎯") or raw.startswith("📋 **备选**"):
            close_block()
            mode = "plan"
            out.append('<div class="plan"><p class="plan-t">%s</p>' % md_inline(raw))
            continue
        if mode == "plan":
            if (re.match(r"^\s{2,}", raw) or raw.startswith("- ")
                    or raw.startswith("**")):
                out.append('<p class="plan-l">%s</p>' % md_inline(raw.strip()))
                continue
            close_block()  # 非缩进行 → 计划块结束，落回普通渲染

        # ---- 标题类 ----
        if raw.startswith("### "):
            out.append("<h3>%s</h3>" % md_inline(raw[4:]))
            continue
        if raw.startswith("━━"):
            out.append('<hr class="card">')
            continue
        if raw.startswith("🔍"):
            out.append('<div class="ttl">%s</div>' % md_inline(raw))
            continue
        if raw.startswith("> "):
            out.append('<p class="muted">%s</p>' % md_inline(raw[2:]))
            continue

        # ---- 股票卡片：**code name**（可带 🔥 前缀）→ 新卡片；缩进行归入 ----
        _sl = raw[2:] if raw.startswith("🔥 ") else raw
        if _sl.startswith("**") and "**" in _sl[2:]:
            close_block()
            mode = "stock"
            stock_warn = False
            out.append('<div class="stock"><p class="stock-t">%s</p>' % md_inline(raw))
            continue
        if mode == "stock":
            txt = raw.strip()
            if txt.startswith("⚠️"):
                stock_warn = True
                # 若卡片还没关闭，先关闭再插警告行（warn class 要提前知道——简单起见用行内警示样式）
                out.append('<p class="stock-warn">%s</p>' % md_inline(txt))
                continue
            cls = "stock-l"
            out.append('<p class="%s">%s</p>' % (cls, md_inline(txt)))
            continue

        # ---- 避开/观察户：灰色，不抢焦点 ----
        if raw.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append("<li>%s</li>" % md_inline(raw[2:]))
            continue
        if raw.startswith("🚫") or raw.startswith("👀"):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append('<p class="skip">%s</p>' % md_inline(raw))
            continue
        if raw.startswith("⚠️ 样本不足"):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append('<p class="muted">%s</p>' % md_inline(raw))
            continue

        # ---- 决策卡（━━ 之间）的行保留 lead 强调 ----
        if raw[:1] in ("💡", "🚫", "⚠", "👀", "📌", "📉", "🔻", "🔥"):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append('<p class="lead">%s</p>' % md_inline(raw))
            continue
        if re.match(r"^\s{2,}", raw):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append('<div class="sub">%s</div>' % md_inline(raw.strip()))
            continue

        if in_ul:
            out.append("</ul>")
            in_ul = False
        out.append("<p>%s</p>" % md_inline(raw))
    close_block()
    return "\n".join(out)


CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#e9e9ec; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
.phone { max-width: 390px; margin: 18px auto; background:#ededed; border-radius: 28px;
         box-shadow: 0 10px 40px rgba(0,0,0,.25); overflow:hidden; }
.bar { background:#2e2e2e; color:#fff; text-align:center; padding:10px 0; font-size:13px; }
.screen { padding: 14px 12px 26px; }
.chat { background:#fff; border-radius:10px; padding:12px; }
.ttl { font-size:18px; font-weight:700; color:#1a1a1a; margin:0 0 8px; }
h3 { font-size:15px; color:#07c160; margin:18px 0 8px; border-left:4px solid #07c160; padding-left:8px; }
hr.card { border:0; border-top:1px dashed #d8d8d8; margin:8px 0; }
p { font-size:14px; line-height:1.7; color:#222; margin:6px 0; }
p.lead { background:#f3fbf6; border-radius:8px; padding:8px 10px; margin:8px 0; }
.sub { font-size:13px; color:#555; margin:3px 0 3px 14px; line-height:1.6; }
ul { margin:6px 0; padding-left:18px; }
li { font-size:14px; line-height:1.7; color:#222; margin:3px 0; }
b { color:#000; }
.note { text-align:center; color:#999; font-size:12px; margin-top:14px; }

/* ===== v5.4 三层视觉层级 ===== */
/* 第一层：操作计划 / 备选——橙色底最醒目 */
.plan { background:#fff8e6; border-left:3px solid #ff9500; border-radius:6px;
        padding:10px; margin:10px 0; }
.plan .plan-t { font-weight:700; color:#8a5300; margin:0 0 6px; }
.plan .plan-l { margin:3px 0; font-size:13.5px; color:#5c4a1e; }
/* 第二层：首选股票卡片——白底+绿左边框，卡片间有间隔 */
.stock { background:#fff; border:1px solid #e8e8e8; border-left:4px solid #07c160;
         border-radius:6px; padding:8px 10px; margin:10px 0;
         box-shadow:0 1px 3px rgba(0,0,0,.04); }
.stock .stock-t { font-size:15px; font-weight:700; margin:0 0 4px; }
.stock .stock-l { margin:2px 0; font-size:13.5px; color:#333; }
.stock .stock-warn { margin:4px 0 0; font-size:13px; color:#c0392b; background:#fdf0ee;
                     border-radius:5px; padding:4px 8px; }
/* 第三层：避开户/观察户/次要信息——灰字，不抢焦点 */
p.skip { color:#666; font-size:13px; margin:4px 0; background:none; }
p.muted { color:#999; font-size:12.5px; margin:4px 0; background:none; }
"""


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "微信推送-v5风格-2026-09-24.md")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "微信推送-v5风格-preview.html")
    bar = sys.argv[3] if len(sys.argv) > 3 else "微信 · A股六层过滤推送（v5 预览 · 模拟行情演示）"
    md = open(src, encoding="utf-8").read()
    body = render(md)
    html = ("<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>v5 推送预览</title><style>%s</style></head><body>"
            "<div class=\"phone\"><div class=\"bar\">%s</div>"
            "<div class=\"screen\"><div class=\"chat\">%s</div>"
            "<div class=\"note\">本预览为本地渲染，仅供审阅版式；实际推送经 Server酱 发往微信</div>"
            "</div></div></body></html>") % (CSS, bar, body)
    out_abs = out if os.path.isabs(out) else os.path.join(HERE, out)
    open(out_abs, "w", encoding="utf-8").write(html)
    print(">>> HTML 预览:", out_abs)


if __name__ == "__main__":
    main()
