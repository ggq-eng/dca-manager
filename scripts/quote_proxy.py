#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quote_proxy.py — 本地行情代理（供「定投管理程序.html」调用真实 A 股行情）

用法：
    python quote_proxy.py            # 默认端口 8000
    python quote_proxy.py 9000       # 自定义端口

接口：
    GET /quote?code=sh600000        → JSON（含 CORS 头，浏览器可直连）
    支持多代码: code=sh600000,sz000001

说明：
    转发腾讯免费行情接口 (qt.gtimg.cn)，解决浏览器直连跨域(CORS)限制。
    纯标准库实现，无需安装任何依赖。
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

HEADERS = {
    "Referer": "https://finance.qq.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
}


def parse_tencent(raw: str) -> dict | None:
    """解析腾讯行情文本：v_sh600000="1~浦发银行~600000~10.50~..." """
    m = re.search(r'"([^"]+)"', raw)
    if not m:
        return None
    p = m.group(1).split("~")
    if len(p) < 35:
        return None
    try:
        return {
            "ok": True,
            "name": p[1],
            "code": p[2],
            "price": float(p[3]),       # 现价
            "prevClose": float(p[4]),   # 昨收
            "open": float(p[5]),        # 今开
            "volume": p[6],             # 成交量(手)
            "high": float(p[33]),       # 最高
            "low": float(p[34]),        # 最低
            "change": float(p[31]),     # 涨跌额
            "changePct": float(p[32]),  # 涨跌幅 %
            "time": p[30] or "",        # 时间戳 YYYYMMDDHHMMSS
        }
    except (ValueError, IndexError):
        return None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/quote":
            self._send(404, {"ok": False, "error": "not found: " + parsed.path})
            return
        codes = parse_qs(parsed.query).get("code", [""])[0]
        if not codes:
            self._send(400, {"ok": False, "error": "missing ?code=sh600000"})
            return
        # 支持逗号分隔的多代码，逐只获取
        results = []
        for code in [c.strip() for c in codes.split(",") if c.strip()]:
            try:
                req = Request(f"https://qt.gtimg.cn/q={code}", headers=HEADERS)
                with urlopen(req, timeout=8) as resp:
                    raw = resp.read().decode("gbk", errors="replace")
                item = parse_tencent(raw)
                if item:
                    results.append(item)
                else:
                    results.append({"ok": False, "code": code, "error": "解析失败"})
            except Exception as e:  # noqa: BLE001
                results.append({"ok": False, "code": code, "error": str(e)})
        self._send(200, results[0] if len(results) == 1 else {"ok": True, "list": results})

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[quote_proxy] " + (fmt % args) + "\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"行情代理已启动: http://localhost:{port}/quote?code=sh600000")
    print("在定投管理程序「行情接口」配置中填入本地代理地址即可（默认已填好）。Ctrl+C 退出。")
    try:
        HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
