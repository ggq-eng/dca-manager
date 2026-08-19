#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_report.py — 复利定投每日监控日报生成器（dca-manager skill）

用法示例：
    python generate_report.py --code sh600000 --name 浦发银行
    python generate_report.py --code sz000001 --name 平安银行 --period 3000 --upper 13 --lower 9
    python generate_report.py --code sh600000 --out "D:/日报" --proxy "http://localhost:8000/quote"

行为：
    1) 行情获取（两级 fallback）：本地代理 → 腾讯直连(qt.gtimg.cn, GBK 解码)
       —— 若两级都失败：退出码非 0，由 AI 用 WebSearch 兜底后继续生成日报。
    2) 档位判定：price > upper → 暂停；price < lower → 加码(period×multiply)；否则正常(period)
    3) 风控点位：止损价 = 参考成本 × (1-sl/100)，止盈价 = 参考成本 × (1+tp/100)（参考成本=现价）
    4) 输出 Markdown 日报到 <out>/YYYY-MM-DD.md 并打印简洁摘要。
    5) 仅输出监控参考，不做任何真实交易。

纯标准库实现，无需安装依赖。
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HEADERS = {
    "Referer": "https://finance.qq.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
}

# 默认参数（与 dca-manager.html 的 DEFAULT_CONFIG 保持一致）
DEFAULTS = dict(
    code="sh600000", name="浦发银行",
    principal=200000, period=5000, frequency="weekly",
    upper=12.00, lower=8.50, multiply=1.5,
    take_profit=20, stop_loss=10,
    trail_trigger=20, trail_drawdown=8,
    reinvest=True, out="日报", proxy="http://localhost:8000/quote",
    quote_api="https://qt.gtimg.cn/q=",
)


def parse_tencent(raw: str) -> dict | None:
    """解析腾讯行情文本 v_sh600000="1~名称~代码~现价~...~涨跌~涨跌%~时间~...~最高~最低" """
    m = re.search(r'"([^"]+)"', raw)
    if not m:
        return None
    p = m.group(1).split("~")
    if len(p) < 35:
        return None
    try:
        return dict(
            ok=True, name=p[1], code=p[2], price=float(p[3]), prevClose=float(p[4]),
            open=float(p[5]), volume=p[6], high=float(p[33]), low=float(p[34]),
            change=float(p[31]), changePct=float(p[32]), time=p[30] or "",
        )
    except (ValueError, IndexError):
        return None


def fetch_quote(code: str, proxy: str, quote_api: str) -> dict | None:
    """行情获取：本地代理优先，失败回退腾讯直连(GBK)。两级全失败返回 None。"""
    errors = []
    # 1) 本地代理（CORS 友好）
    try:
        url = f"{proxy}?{urlencode({'code': code})}"
        with urlopen(Request(url, headers=HEADERS), timeout=8) as resp:
            j = json.loads(resp.read().decode("utf-8"))
        if j.get("ok"):
            return j
        errors.append(f"proxy: {j.get('error')}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"proxy: {e}")
    # 2) 腾讯直连（GBK）
    try:
        with urlopen(Request(quote_api + code, headers=HEADERS), timeout=8) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        item = parse_tencent(raw)
        if item:
            return item
        errors.append("直连解析失败")
    except Exception as e:  # noqa: BLE001
        errors.append(f"direct: {e}")
    return None


def decide_band(price: float, upper: float, lower: float) -> str:
    if price > upper:
        return "pause"
    if price < lower:
        return "boost"
    return "normal"


def main() -> int:
    ap = argparse.ArgumentParser(description="复利定投每日监控日报生成器")
    for key, val in DEFAULTS.items():
        ap.add_argument(f"--{key}", type=type(val) if val is not None else str,
                        default=val, help=f"默认 {val}")
    args = ap.parse_args()
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    quote = fetch_quote(args.code, args.proxy, args.quote_api)
    failed = quote is None
    if failed:
        # 用最近一次已知配置值占位，日报标注失败
        quote = dict(name=args.name, code=args.code, price=None, prevClose=None,
                     high=None, low=None, change=None, changePct=None,
                     ok=False, time="", error="本地代理与腾讯直连均失败")
        price_ref = args.lower  # 占位参考，报告中会明确标注
    else:
        price_ref = quote["price"]

    # ---- 档位与金额 ----
    band = decide_band(price_ref, args.upper, args.lower)
    band_info = {
        "pause": ("⏸ 暂停区间", "高于上限，等待回调", 0),
        "boost": ("🔴 加码区间", f"低于下限，加码 ×{args.multiply}", args.period * args.multiply),
        "normal": ("🟢 正常定投区间", "区间内", args.period),
    }[band]
    band_label, band_note, amount = band_info

    # ---- 止损/止盈点位（参考成本 = 现价） ----
    sl_price = price_ref * (1 - args.stop_loss / 100)
    tp_price = price_ref * (1 + args.take_profit / 100)
    sl_dist = (sl_price / price_ref - 1) * 100 if price_ref else None
    tp_dist = (tp_price / price_ref - 1) * 100 if price_ref else None

    # ---- 风险提示 ----
    risks = []
    if not failed and sl_dist is not None:
        if sl_dist > -3:
            risks.append(f"🔴 红色警示：距止损点仅 {sl_dist:.1f}%（<3%）")
        else:
            risks.append(f"距止损点 {sl_dist:.1f}%（>3%，安全）")
    if not failed and tp_dist is not None:
        if tp_dist < 3:
            risks.append(f"🟡 黄色提示：距止盈点仅 {tp_dist:.1f}%（<3%）")
        else:
            risks.append(f"距止盈点 {tp_dist:.1f}%")
    if sl_price < args.lower:
        boost_amount = args.period * args.multiply
        risks.append(
            f"📌 加码区交互：止损价 ¥{sl_price:.2f} < 加码下限 ¥{args.lower:.2f} —— "
            f"价格跌至 ¥{sl_price:.2f}~¥{args.lower:.2f} 之间仍会加码买入 ¥{boost_amount:,.0f}（越跌越买），"
            f"跌破 ¥{sl_price:.2f} 才触发止损，属'加码兜底 + 极端止损'设计。"
        )

    today = date.today().isoformat()
    src = "腾讯行情（本地代理/直连）" if not failed else "行情获取失败，数据为最近一次已知值（占位）"

    # ---- 生成 Markdown ----
    md = [
        f"# 定投监控日报 · {today}",
        "",
        f"> 标的：{quote.get('name', args.name)}（{args.code}）｜策略：复利定投参数",
        f"> 数据来源：{src}｜生成时间：{datetime.now().strftime('%H:%M')}",
        "",
        "## 行情快照",
        "",
        "| 项目 | 数值 |",
        "|---|---|",
    ]
    if failed:
        md += ["| 状态 | ⚠️ 行情获取失败 |",
               f"| 失败原因 | {quote.get('error', '')} |"]
    else:
        chg = quote["change"]; pct = quote["changePct"]
        md += [f"| 现价 | ¥{quote['price']:.2f} |",
               f"| 昨收 | ¥{quote['prevClose']:.2f} |",
               f"| 今日涨跌 | {chg:+.2f}（{pct:+.2f}%） |",
               f"| 最高 / 最低 | {quote['high']:.2f} / {quote['low']:.2f} |"]
    md += [
        "",
        "## 定投档位判定",
        "",
        f"- 触发区间：上限 ¥{args.upper:.2f}（暂停）｜下限 ¥{args.lower:.2f}（加码 ×{args.multiply}）",
        f"- 现价 ¥{price_ref:.2f} → **{band_label}**（{band_note}）",
        f"- 本期应投金额：**¥{amount:,.0f}**",
        "",
        f"## 止损 / 止盈点位表（参考成本 = 现价 ¥{price_ref:.2f}）",
        "",
        "| 点位 | 价格 | 距现价 |",
        "|---|---|---|",
        f"| 止损点（-{args.stop_loss}%） | **¥{sl_price:.2f}** | {sl_dist:+.1f}% |",
        f"| 止盈点（+{args.take_profit}%） | **¥{tp_price:.2f}** | {tp_dist:+.1f}% |",
        f"| 移动止盈启动（浮盈≥{args.trail_trigger}%） | ¥{tp_price:.2f} | {tp_dist:+.1f}% |",
        f"| 移动止盈触发（自高点回撤{args.trail_drawdown}%） | 持仓最高价 × {1 - args.trail_drawdown / 100:.2f} | -- |",
        "",
        "> 基准说明：止损/止盈基于**持仓摊薄成本**计算；此处以现价作参考基准。若已在程序内运行模拟，以「当前价位诊断」中摊薄成本为准。",
        "",
        "## 风险提示",
        "",
    ] + [f"- {r}" for r in risks] + [
        "",
        "## 明日操作建议",
        "",
    ]
    if band == "pause":
        md.append(f"价格高于上限 ¥{args.upper:.2f}，本期暂停定投，等待回调至区间内再执行。")
    elif band == "boost":
        md.append(f"处于加码区：本期加码买入 ¥{amount:,.0f}（{args.period:,.0f} × {args.multiply}）。跌破 ¥{sl_price:.2f} 执行止损出清。")
    else:
        md.append(f"按计划执行本期定投 ¥{amount:,.0f}；若价格跌破 ¥{args.lower:.2f} 转为加码 ¥{args.period * args.multiply:,.0f}；跌破 ¥{sl_price:.2f} 执行止损。")
    md += ["可每日本程序「当前价位诊断」复核点位。", "",
           "---", "*本日报由 dca-manager 自动生成，仅供参考，不构成投资建议。*", ""]

    filepath = os.path.join(out_dir, f"{today}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"✅ 日报已生成: {filepath}")

    # ---- 简洁摘要（供 AI 直接回复用户） ----
    print("\n===== 摘要 =====")
    if failed:
        print(f"⚠️ 行情获取失败（{quote.get('error', '')}），日报使用占位数据。")
    else:
        print(f"现价 ¥{quote['price']:.2f}（{quote['changePct']:+.2f}%） 昨收 ¥{quote['prevClose']:.2f} "
              f"高 {quote['high']:.2f} / 低 {quote['low']:.2f}")
    print(f"档位：{band_label} → 本期应投 ¥{amount:,.0f}")
    print(f"止损点 ¥{sl_price:.2f}（{sl_dist:+.1f}%）｜止盈点 ¥{tp_price:.2f}（{tp_dist:+.1f}%）")
    for r in risks:
        print("- " + r)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
