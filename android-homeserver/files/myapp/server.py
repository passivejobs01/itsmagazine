#!/data/data/com.termux/files/usr/bin/python
# 폰에서 도는 파이썬 서버 — "이런 것도 된다" 데모용
import json, os, platform, socket, time, http.server, socketserver

PORT = 8093
START = time.time()

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Python on Phone</title>
<style>
  :root {{ color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --tx:#1f2328; --mu:#6b7280; --ac:#3776ab; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#14161a; --card:#1d2026; --tx:#e8eaed; --mu:#9aa0a6; --ac:#6ba6dd; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:16px;
         background:var(--bg); color:var(--tx);
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }}
  .card {{ background:var(--card); border-radius:20px; padding:40px 32px; text-align:center;
          max-width:520px; width:100%; box-shadow:0 8px 32px rgba(0,0,0,.10); }}
  .badge {{ display:inline-block; font-size:13px; letter-spacing:.08em; text-transform:uppercase;
           color:var(--mu); margin-bottom:14px; }}
  h1 {{ margin:0 0 6px; font-size:clamp(34px,9vw,52px); color:var(--ac); letter-spacing:-.02em; }}
  .sub {{ font-size:17px; color:var(--mu); margin-bottom:28px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  td {{ padding:9px 4px; border-top:1px solid color-mix(in srgb, var(--mu) 25%, transparent); text-align:left; }}
  td:last-child {{ text-align:right; font-variant-numeric:tabular-nums; color:var(--mu); }}
  .foot {{ margin-top:24px; font-size:13px; color:var(--mu); }}
</style></head>
<body><div class="card">
  <div class="badge">Running on an old Android phone</div>
  <h1>🐍 Python</h1>
  <div class="sub">이 페이지는 <b>폰 안의 파이썬</b>이 직접 만들어 보냈습니다</div>
  <table>
    <tr><td>버전</td><td>Python {pyver}</td></tr>
    <tr><td>포트</td><td>{port}</td></tr>
    <tr><td>가동 시간</td><td>{uptime}</td></tr>
    <tr><td>기기</td><td>{machine}</td></tr>
  </table>
  <div class="foot">JSON 으로 보려면 <code>/api</code></div>
</div></body></html>"""


def uptime_str():
    s = int(time.time() - START)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{h}시간 " if h else "") + (f"{m}분 " if h or m else "") + f"{s}초"


class H(http.server.BaseHTTPRequestHandler):
    server_version = "PhonePy"

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api"):
            body = json.dumps({
                "language": "Python",
                "version": platform.python_version(),
                "port": PORT,
                "uptime_sec": round(time.time() - START, 1),
                "machine": platform.machine(),
                "host": socket.gethostname(),
            }, ensure_ascii=False, indent=2).encode()
            return self._send(body, "application/json; charset=utf-8")

        html = PAGE.format(
            pyver=platform.python_version(),
            port=PORT,
            uptime=uptime_str(),
            machine=platform.machine(),
        )
        self._send(html.encode(), "text/html; charset=utf-8")

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("0.0.0.0", PORT), H) as httpd:
    print(f"python {platform.python_version()} listening on 0.0.0.0:{PORT}", flush=True)
    httpd.serve_forever()
