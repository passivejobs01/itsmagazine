#!/data/data/com.termux/files/usr/bin/python
"""폰 카메라 CCTV — Termux 만으로 (🔴 실패한 방식 · 가이드에서 쓰지 않는다)

🔴🔴 이 스크립트는 "되긴 되는데 쓰면 안 되는" 예시로 남겨 둔 것이다.
     가이드가 안내하는 구성은 `dashboard.py` 다(카메라 앱 + 중계).

     실측 결과:
       · 초당 0.6장  (촬영 1.3초 + 축소 0.52초)
       · CPU 코어의 약 30%
       · 🔴 호출마다 카메라를 열고 '오토포커스를 다시 돌린다'
         → 1.8초마다 렌즈 모터가 움직인다(하루 약 48,000회).
            소리가 들릴 정도라 거주 공간에서 24시간 돌릴 수 없다.

     👉 "요청할 때 1장만 찍기"(예: 메신저로 '지금 집 어때?') 용도로는 쓸 만하다.
        상시 송출로는 결격이다.

  촬영: termux-camera-photo (Termux:API)
  축소: ffmpeg
  송출: MJPEG (브라우저가 <img> 만으로 재생 — 자바스크립트 불필요)

🔴 배터리 보호: 보는 사람이 없으면 촬영하지 않는다(IDLE_STOP 초 뒤 자동 중단).
🔴 인증 필수: ~/.webdav_user / ~/.webdav_pass 를 읽어 Basic 인증을 건다.
"""
import base64
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("CCTV_PORT", 8095))
CAM = os.environ.get("CCTV_CAM", "0")          # 0=후면, 1·2=전면
WIDTH = os.environ.get("CCTV_WIDTH", "1280")
QUALITY = os.environ.get("CCTV_QUALITY", "5")  # 낮을수록 고화질(2~31)
IDLE_STOP = 20                                 # 마지막 요청 후 n초면 촬영 중단

HOME = os.path.expanduser("~")
TMP = os.path.join(HOME, ".cam_raw.jpg")
OUT = os.path.join(HOME, ".cam_out.jpg")


def _read(path):
    with open(path, encoding="utf-8") as fp:
        return fp.read().strip()


TOKEN = base64.b64encode(
    "{}:{}".format(
        _read(os.path.join(HOME, ".webdav_user")),
        _read(os.path.join(HOME, ".webdav_pass")),
    ).encode()
).decode()

S = {"frame": None, "ts": 0.0, "n": 0, "last_req": 0.0, "err": None, "ms": 0}
LOCK = threading.Lock()


def capture():
    """사진 1장 → 축소 → 바이트로 반환. (소요 약 1.8초)"""
    t0 = time.time()
    subprocess.run(["termux-camera-photo", "-c", CAM, TMP],
                   timeout=25, check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", TMP,
                    "-vf", "scale={}:-1".format(WIDTH), "-q:v", QUALITY, OUT],
                   timeout=25, check=True, capture_output=True)
    with open(OUT, "rb") as fp:
        return fp.read(), int((time.time() - t0) * 1000)


def worker():
    """보는 사람이 있을 때만 계속 촬영한다."""
    while True:
        if time.time() - S["last_req"] > IDLE_STOP:
            time.sleep(0.5)
            continue
        try:
            data, ms = capture()
            with LOCK:
                S.update(frame=data, ts=time.time(), n=S["n"] + 1, err=None, ms=ms)
        except Exception as exc:
            with LOCK:
                S["err"] = "{}: {}".format(type(exc).__name__, exc)[:200]
            print("capture error:", S["err"], flush=True)
            time.sleep(3)


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phone CCTV</title>
<style>
  :root {{ color-scheme: dark; --bg:#0f1115; --card:#181b21; --tx:#e8eaed; --mu:#9aa0a6; --ac:#e06c5a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:16px;
         background:var(--bg); color:var(--tx);
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }}
  .card {{ background:var(--card); border-radius:18px; padding:22px; max-width:900px; width:100%;
          box-shadow:0 8px 32px rgba(0,0,0,.35); }}
  .top {{ display:flex; align-items:baseline; gap:10px; margin-bottom:14px; flex-wrap:wrap; }}
  h1 {{ margin:0; font-size:22px; letter-spacing:-.01em; }}
  .dot {{ width:9px; height:9px; border-radius:50%; background:var(--ac); display:inline-block;
         animation:p 1.6s ease-in-out infinite; }}
  @keyframes p {{ 50% {{ opacity:.25; }} }}
  .mu {{ color:var(--mu); font-size:13px; }}
  img {{ width:100%; border-radius:12px; display:block; background:#000;
        aspect-ratio:4/3; object-fit:contain; }}
  .row {{ display:flex; gap:8px; margin-top:14px; flex-wrap:wrap; }}
  a.btn {{ color:var(--tx); text-decoration:none; border:1px solid #333a45; padding:7px 13px;
          border-radius:9px; font-size:13px; }}
  a.btn:hover {{ border-color:var(--ac); }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:14px; }}
  td {{ padding:7px 2px; border-top:1px solid #272c35; }}
  td:last-child {{ text-align:right; color:var(--mu); font-variant-numeric:tabular-nums; }}
</style></head>
<body><div class="card">
  <div class="top"><span class="dot"></span><h1>📷 폰 CCTV</h1>
    <span class="mu">Termux 만으로 동작 — 별도 카메라 앱 없음</span></div>
  <img src="/stream.mjpg" alt="live">
  <div class="row">
    <a class="btn" href="/snapshot.jpg">스냅샷 1장</a>
    <a class="btn" href="/api">상태 JSON</a>
    <a class="btn" href="/?cam=0">후면</a>
    <a class="btn" href="/?cam=1">전면</a>
  </div>
  <table>
    <tr><td>카메라</td><td>{cam}</td></tr>
    <tr><td>가로 해상도</td><td>{width}px</td></tr>
    <tr><td>프레임 간격</td><td>약 {ms} ms</td></tr>
    <tr><td>자동 중단</td><td>보는 사람 없으면 {idle}초 뒤</td></tr>
  </table>
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PhoneCCTV"

    def _auth(self):
        if self.headers.get("Authorization", "") == "Basic " + TOKEN:
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="CCTV"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _wait_frame(self, timeout=30):
        S["last_req"] = time.time()
        end = time.time() + timeout
        while time.time() < end:
            with LOCK:
                if S["frame"]:
                    return S["frame"]
            time.sleep(0.2)
        return None

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global CAM
        if not self._auth():
            return

        if "cam=" in self.path:
            CAM = self.path.split("cam=")[1][:1]
            with LOCK:
                S["frame"] = None

        path = self.path.split("?")[0]

        if path == "/stream.mjpg":
            return self._mjpeg()

        if path == "/snapshot.jpg":
            frame = self._wait_frame()
            if not frame:
                return self.send_error(503, "no frame")
            return self._send(frame, "image/jpeg")

        if path.startswith("/api"):
            S["last_req"] = time.time()
            with LOCK:
                body = json.dumps({
                    "app": "Phone CCTV (Termux only)",
                    "camera": CAM,
                    "width": WIDTH,
                    "frames": S["n"],
                    "last_frame_ms": S["ms"],
                    "age_sec": round(time.time() - S["ts"], 1) if S["ts"] else None,
                    "error": S["err"],
                }, ensure_ascii=False, indent=2).encode()
            return self._send(body, "application/json; charset=utf-8")

        html = PAGE.format(cam=CAM, width=WIDTH, ms=S["ms"] or "측정 전", idle=IDLE_STOP)
        self._send(html.encode(), "text/html; charset=utf-8")

    def _mjpeg(self):
        """multipart/x-mixed-replace — 브라우저가 <img> 로 바로 재생한다."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last = 0.0
        try:
            while True:
                S["last_req"] = time.time()
                with LOCK:
                    frame, ts = S["frame"], S["ts"]
                if frame and ts != last:
                    last = ts
                    head = ("--f\r\nContent-Type: image/jpeg\r\n"
                            "Content-Length: {}\r\n\r\n".format(len(frame))).encode()
                    self.wfile.write(head)
                    self.wfile.write(frame + b"\r\n")
                    self.wfile.flush()
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


threading.Thread(target=worker, daemon=True).start()
print("CCTV listening on 0.0.0.0:{} (camera {}, 인증 필요)".format(PORT, CAM), flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
