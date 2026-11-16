#!/data/data/com.termux/files/usr/bin/python
"""폰 홈서버 대시보드 — 카메라 + 기기 상태 + 서비스 링크를 한 페이지에.

  카메라: 카메라 앱(IP Webcam)의 MJPEG 를 그대로 중계한다.
          브라우저는 <img> 하나로 재생한다 — 자바스크립트 플레이어가 필요 없다.

  🔑 왜 중계하는가(직접 http://폰:8080/video 를 쓰지 않는 이유):
     ① 카메라 앱 포트는 LAN·tailnet 에 그대로 열린다. 집 와이파이에 붙은
        누구나 영상을 볼 수 있다. 중계하면 대시보드의 인증 하나로 막힌다.
     ② 바깥에 열어 둘 포트가 8095 하나로 줄어든다.

  ⚠️ MJPEG 는 프레임마다 JPEG 를 통째로 보낸다 — 1280x720 에서 약 7Mbps.
     보는 사람이 2명이면 2배다. 혼자 볼 때를 전제로 한다.

실행:  nohup python ~/myapp/dashboard.py > ~/dashboard.log 2>&1 &
중지:  pkill -f dashboard.py
"""
import base64
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("DASH_PORT", 8095))
CAMAPI = os.environ.get("CAMAPI", "http://127.0.0.1:8080")   # 카메라 앱
MJPEG_PATH = os.environ.get("MJPEG_PATH", "/video")          # 앱의 MJPEG 경로

HOME = os.path.expanduser("~")

# 서비스 타일 — (이름, 포트, 설명)
# 🔴 카메라앱은 Termux 바깥이라 runit 이 못 살린다. 그래서 상태만이라도 보여준다.
# (이름, 포트, 설명, 링크 스킴)
# 🔑 Syncthing 만 https 다 — 로컬이 아닌 접속에는 Syncthing 이 **스스로 TLS 로 올린다**
#    (비밀번호를 평문으로 보내지 않기 위해). 자체 서명 인증서라 브라우저 경고가 한 번 뜬다.
SERVICES = [
    ("Jellyfin", 8096, "영상 스트리밍", "http"),
    ("파일", 8092, "업로드 · WebDAV", "http"),
    ("Syncthing", 8384, "파일 동기화 (⚠️ 인증서 경고 한 번)", "https"),
    # 🔑 이름에 언어를 같이 적는다 — 부록 A 의 "내가 짠 스크립트도 서버가 된다"가
    #    대시보드에서 바로 보이게 하려는 것이다.
    ("🧭 센서 · Python", 8093, "나침반 · 조도 · 기압 · 기울기", "http"),
    ("🔦 제어판 · Node", 8094, "진동 · 손전등 · 알림 · 음성", "http"),
    ("카메라앱", 8080, "⚠️ 꺼지면 폰에서 수동 실행", "http"),
]


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as fp:
            return fp.read().strip()
    except OSError:
        return default


def _basic(user, pw):
    return base64.b64encode("{}:{}".format(user, pw).encode()).decode()


# 대시보드 자신의 인증 — 파일에만 둔다(스크립트·설정에 적지 않는다)
TOKEN = _basic(_read(os.path.join(HOME, ".webdav_user")),
               _read(os.path.join(HOME, ".webdav_pass")))

# 카메라 앱에도 비밀번호를 걸었다면 그 값을 읽어 중계할 때 대신 넣는다.
_cu, _cp = _read(os.path.join(HOME, ".cam_user")), _read(os.path.join(HOME, ".cam_pass"))
CAM_TOKEN = _basic(_cu, _cp) if _cu and _cp else None


# ── 기기 상태 ──────────────────────────────────────────────────────────────
_cache = {"at": 0.0, "data": {}}
_lock = threading.Lock()


def _run(cmd, timeout=8):
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)
        return out.stdout.strip()
    except Exception:
        return ""


def _port_open(port):
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _cam_get(path, timeout=5):
    """카메라 앱에 요청한다(인증이 걸려 있으면 대신 넣어 준다)."""
    req = urllib.request.Request(CAMAPI + path)
    if CAM_TOKEN:
        req.add_header("Authorization", "Basic " + CAM_TOKEN)
    return urllib.request.urlopen(req, timeout=timeout)


def camera():
    """카메라 앱 상태. 앱이 꺼져 있으면 빈 dict."""
    try:
        with _cam_get("/status.json?show_avail=0", timeout=4) as r:
            raw = json.loads(r.read().decode())
    except Exception:
        return {}
    cur = raw.get("curvals", {})
    return {
        "app": "IP Webcam",
        "size": cur.get("video_size"),
        "quality": cur.get("quality"),
        "zoom": cur.get("zoom"),
        "front": cur.get("ffc") == "on",
        "torch": cur.get("torch") == "on",
        "night": cur.get("night_vision") == "on",
        "focus": cur.get("focusmode"),
        "clients": int(raw.get("video_connections", 0) or 0),
    }


def status():
    """3초 캐시 — 새로고침을 반복해도 폰을 괴롭히지 않는다."""
    with _lock:
        if time.time() - _cache["at"] < 3 and _cache["data"]:
            return _cache["data"]

    bat = {}
    try:
        bat = json.loads(_run(["termux-battery-status"]) or "{}")
    except Exception:
        pass

    mem_total = mem_avail = 0
    for line in (_run(["free", "-m"]) or "").splitlines():
        parts = line.split()
        if parts and parts[0].startswith("Mem"):
            mem_total = int(parts[1])
            mem_avail = int(parts[-1])

    disk_free = ""
    df = _run(["df", "-h", HOME]).splitlines()
    if len(df) > 1:
        cols = df[-1].split()
        if len(cols) >= 4:
            disk_free = cols[3]

    up = _run(["uptime"])
    uptime = up.split(" up ")[1].split(",")[0].strip() if " up " in up else ""

    cam = camera()
    data = {
        "battery": bat.get("percentage"),
        "plugged": bat.get("plugged", "").replace("PLUGGED_", "") or None,
        "temp_c": bat.get("temperature"),
        "mem_total_mb": mem_total,
        "mem_avail_mb": mem_avail,
        "disk_free": disk_free,
        "uptime": uptime,
        "camera_app": bool(cam),
        "cam": cam or None,
        "services": {name: _port_open(port) for name, port, _, _scheme in SERVICES},
    }
    with _lock:
        _cache.update(at=time.time(), data=data)
    return data


# ── 페이지 ────────────────────────────────────────────────────────────────
PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phone Home Server</title>
<style>
  :root {{ color-scheme: dark; --bg:#0e1014; --card:#171a20; --line:#262b34;
           --tx:#e9ebee; --mu:#9aa1ab; --ac:#5ec27e; --warn:#e0a05a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; background:var(--bg); color:var(--tx);
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
         padding:16px; display:flex; justify-content:center; }}
  .wrap {{ width:100%; max-width:860px; }}
  header {{ display:flex; align-items:baseline; gap:10px; margin:6px 2px 16px; flex-wrap:wrap; }}
  h1 {{ margin:0; font-size:21px; letter-spacing:-.01em; }}
  .sub {{ color:var(--mu); font-size:13px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:16px;
          padding:16px; margin-bottom:14px; }}
  .camhead {{ display:flex; align-items:center; gap:10px; margin-bottom:12px; flex-wrap:wrap; }}
  .dot {{ width:8px; height:8px; border-radius:50%; background:var(--mu); flex:none; }}
  .dot.on {{ background:var(--ac); animation:p 1.8s ease-in-out infinite; }}
  @keyframes p {{ 50% {{ opacity:.3; }} }}
  #cam {{ display:block; width:100%; aspect-ratio:16/9; background:#000;
         border-radius:11px; object-fit:contain; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:13px; padding:13px; }}
  .stat .k {{ color:var(--mu); font-size:12px; margin-bottom:5px; }}
  .stat .v {{ font-size:19px; font-variant-numeric:tabular-nums; }}
  .svc {{ display:flex; align-items:center; gap:9px; padding:11px 13px; border-radius:11px;
         border:1px solid var(--line); background:var(--card); text-decoration:none; color:var(--tx); }}
  .svc:hover {{ border-color:var(--ac); }}
  .svc .nm {{ font-size:14px; }} .svc .ds {{ color:var(--mu); font-size:12px; }}
  button, a.btn {{ background:#222730; color:var(--tx); border:1px solid var(--line);
           border-radius:9px; padding:7px 13px; font-size:13px; cursor:pointer;
           text-decoration:none; display:inline-block; }}
  button:hover, a.btn:hover {{ border-color:var(--ac); }}
  footer {{ color:var(--mu); font-size:12px; text-align:center; margin:18px 0 6px; }}
  .opts {{ margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }}
  .opts summary {{ color:var(--mu); font-size:13px; cursor:pointer; }}
  .optrow {{ display:flex; align-items:center; gap:7px; margin-top:9px; flex-wrap:wrap; }}
  .optrow .k {{ color:var(--mu); font-size:12px; min-width:64px; }}
</style></head>
<body><div class="wrap">

  <header>
    <h1>📱 폰 홈서버</h1>
    <span class="sub">2018년 폰 한 대로 돌아갑니다</span>
  </header>

  <div class="card">
    <div class="camhead">
      <span class="dot" id="camdot"></span>
      <b>카메라</b>
      <span class="sub" id="camtxt">연결 중…</span>
      <span style="flex:1"></span>
      <button onclick="reconnect()">다시 연결</button>
      <a class="btn" href="/shot.jpg" target="_blank">사진 1장</a>
    </div>
    <img id="cam" alt="live">

    <details class="opts">
      <summary>카메라 설정</summary>
      <div class="optrow"><span class="k">조명</span>
        <button onclick="cam('/enabletorch')">켜기</button>
        <button onclick="cam('/disabletorch')">끄기</button></div>
      <div class="optrow"><span class="k">렌즈</span>
        <button onclick="cam('/settings/ffc?set=off')">후면</button>
        <button onclick="cam('/settings/ffc?set=on')">전면</button></div>
      <div class="optrow"><span class="k">해상도</span>
        <button onclick="cam('/settings/video_size?set=640x480')">640</button>
        <button onclick="cam('/settings/video_size?set=1280x720')">720p</button>
        <button onclick="cam('/settings/video_size?set=1920x1080')">1080p</button></div>
      <div class="optrow"><span class="k">화질</span>
        <button onclick="cam('/settings/quality?set=20')">20</button>
        <button onclick="cam('/settings/quality?set=40')">40</button>
        <button onclick="cam('/settings/quality?set=70')">70</button></div>
      <div class="optrow"><span class="k">초점</span>
        <button onclick="cam('/focus')">다시 맞추기</button>
        <button onclick="cam('/nofocus')">고정</button></div>
      <div class="optrow"><span class="k">야간</span>
        <button onclick="cam('/settings/night_vision?set=on')">켜기</button>
        <button onclick="cam('/settings/night_vision?set=off')">끄기</button></div>
      <div class="sub" id="camcfg" style="margin-top:8px"></div>
      <div class="sub" style="margin-top:6px">
        ⚠️ 해상도·화질을 올리면 대역폭이 같이 올라갑니다 (720p·화질40 ≈ 7Mbps)</div>
    </details>
  </div>

  <div class="grid" id="stats"></div>

  <div class="card" style="margin-top:14px">
    <div class="sub" style="margin-bottom:10px">서비스</div>
    <div class="grid" id="svcs"></div>
  </div>

  <footer>카메라 영상은 이 페이지가 중계합니다 · 자동 새로고침 5초</footer>
</div>

<script>
  const host = location.hostname;
  const img = document.getElementById('cam');

  // 🔑 MJPEG 는 <img> 하나로 재생된다. 쿼리를 붙여 캐시를 피한다.
  const reconnect = () => {{ img.src = '/cam.mjpg?t=' + Date.now(); }};

  // 끊기면(절전·앱 종료) 3초 뒤 다시 붙는다
  img.addEventListener('error', () => setTimeout(reconnect, 3000));

  const cam = async (p) => {{
    await fetch('/api/cam?p=' + encodeURIComponent(p)).catch(() => {{}});
    setTimeout(refresh, 800);
    setTimeout(reconnect, 1200);    // 해상도를 바꾸면 스트림이 끊긴다
  }};
  window.cam = cam; window.reconnect = reconnect;

  const fmt = (v, s = '') => (v === null || v === undefined || v === '') ? '—' : v + s;

  async function refresh() {{
    let d;
    try {{ d = await (await fetch('/api/status')).json(); }} catch {{ return; }}

    const c = d.cam;
    document.getElementById('camdot').className = 'dot' + (c ? ' on' : '');
    document.getElementById('camtxt').textContent = c
      ? `${{c.app}} · 보는 사람 ${{c.clients}}명`
      : '🔴 카메라 앱이 꺼져 있습니다 — 폰에서 직접 실행해야 합니다';

    document.getElementById('camcfg').textContent = c
      ? `현재 ${{fmt(c.size)}} · 화질 ${{fmt(c.quality)}} · ${{c.front ? '전면' : '후면'}}`
        + `${{c.torch ? ' · 조명 켜짐' : ''}}${{c.night ? ' · 야간모드' : ''}}`
        + ` · 초점 ${{fmt(c.focus)}}`
      : '';

    document.getElementById('stats').innerHTML = [
      ['배터리', fmt(d.battery, '%') + (d.plugged ? ' ⚡' : '')],
      ['온도', fmt(d.temp_c, '℃')],
      ['남은 메모리', fmt(d.mem_avail_mb, 'MB')],
      ['남은 저장공간', fmt(d.disk_free)],
      ['가동 시간', fmt(d.uptime)],
    ].map(([k, v]) => `<div class="stat"><div class="k">${{k}}</div><div class="v">${{v}}</div></div>`).join('');

    document.getElementById('svcs').innerHTML = {services_js}
      .map(s => `<a class="svc" href="${{s.scheme}}://${{host}}:${{s.port}}/" target="_blank">
        <span class="dot ${{d.services[s.name] ? 'on' : ''}}"></span>
        <span><div class="nm">${{s.name}}</div><div class="ds">${{s.desc}}</div></span></a>`).join('');
  }}

  reconnect();
  refresh();
  setInterval(refresh, 5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PhoneDash"

    def _auth(self):
        if self.headers.get("Authorization", "") == "Basic " + TOKEN:
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Phone Home Server"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _relay_stream(self):
        """카메라 앱의 MJPEG 를 그대로 흘려보낸다(재인코딩 없음).

        🔑 길이를 알 수 없는 스트림이므로 Connection: close 로 끝을 정의한다.
        """
        try:
            up = _cam_get(MJPEG_PATH, timeout=10)
        except Exception as exc:
            return self._send(str(exc).encode()[:200], "text/plain; charset=utf-8", 502)
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", up.headers.get(
            "Content-Type", "multipart/x-mixed-replace; boundary=--jpgboundary"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            shutil.copyfileobj(up, self.wfile, 64 * 1024)
        except Exception:
            pass                      # 브라우저가 닫은 것 — 정상
        finally:
            up.close()

    def do_GET(self):
        if not self._auth():
            return
        path = self.path.split("?")[0]

        if path == "/cam.mjpg":
            return self._relay_stream()

        if path == "/shot.jpg":
            try:
                with _cam_get("/shot.jpg", timeout=10) as r:
                    return self._send(r.read(), "image/jpeg")
            except Exception as exc:
                return self._send(str(exc).encode()[:200], "text/plain; charset=utf-8", 502)

        # 카메라 앱 제어 중계 — 브라우저의 교차출처 문제와 인증을 한 번에 해결한다
        if path == "/api/cam":
            import urllib.parse
            p = urllib.parse.parse_qs(self.path.partition("?")[2]).get("p", ["/"])[0]
            if not p.startswith("/"):
                p = "/" + p
            try:
                with _cam_get(p, timeout=8) as r:
                    body = r.read()[:400]
                ok = {"ok": True, "reply": body.decode("utf-8", "replace")}
            except Exception as exc:
                ok = {"ok": False, "error": str(exc)[:120]}
            return self._send(json.dumps(ok, ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")

        if path == "/api/status":
            return self._send(json.dumps(status(), ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")

        services_js = json.dumps(
            [{"name": n, "port": p, "desc": d, "scheme": s} for n, p, d, s in SERVICES],
            ensure_ascii=False)
        self._send(PAGE.format(services_js=services_js).encode(),
                   "text/html; charset=utf-8")

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


print("dashboard on 0.0.0.0:{} (camera={}{})".format(
    PORT, CAMAPI, " 인증O" if CAM_TOKEN else ""), flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
