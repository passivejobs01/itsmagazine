#!/data/data/com.termux/files/usr/bin/python
"""폰 센서 실시간 대시보드 — 미니PC·라즈베리파이에는 없는 부품들.

    실행:  python ~/myapp/sensors.py        (포트 8093)
    보기:  http://<폰주소>:8093/

🔑 이 앱이 보여주려는 것
    나침반·조도·기압·기울기·걸음수는 전부 **폰에 원래 달려 있는 부품**이다.
    라즈베리파이로 같은 걸 하려면 센서 보드를 따로 사서 납땜해야 한다.

🔑 설계에서 중요한 두 가지
    ① **센서를 한 번에 묶어 읽는다.**
       `termux-sensor` 는 호출 한 번에 약 1.1초가 든다. 그런데 그 비용은
       **호출당**이지 센서당이 아니다(실측: 1개 1.1초 / 3개 1.1초).
       그래서 따로 부르지 않고 **하나의 스트리밍 프로세스**로 묶는다.
    ② **보는 사람이 없으면 센서를 끈다.**
       화면을 연 사람이 있을 때만 프로세스를 띄우고, 다 나가면 내린다.
       폰 서버에서 제일 비싼 자원은 CPU 가 아니라 **배터리와 열**이다.
"""

import json
import math
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8093"))
DELAY_MS = 400          # 센서 갱신 간격
IDLE_STOP_SEC = 10      # 마지막 시청자가 나가고 이만큼 지나면 센서 정지

# 찾을 센서 — 🔑 기종마다 **부품 이름이 다르다**(갤럭시는 TMD4906, 다른 폰은 또 다름).
#    그래서 이름을 박아두지 않고 `termux-sensor -l` 에서 **열쇳말로 찾아낸다.**
#    없는 센서는 그냥 빠진다. 이 앱이 폰을 가리지 않는 이유다.
WANTED = [
    # (키, 표시이름, 열쇳말 후보, 단위)
    ("compass", "나침반",   ["magnetic field", "magnetometer"], "µT"),
    ("light",   "조도",     ["lux", "light sensor"],            "lx"),
    ("pressure", "기압",    ["barometer", "pressure"],          "hPa"),
    ("accel",   "기울기",   ["lsm6dsl acceleration", "accelerometer", "acceleration sensor"], "m/s²"),
    ("steps",   "걸음 수",  ["step counter"],                   "걸음"),
    ("prox",    "근접",     ["proximity sensor"],               "cm"),
]

state = {"sensors": {}, "values": {}, "running": False, "updated": 0, "reads": 0}
lock = threading.Lock()
watchers = 0
proc = None


def discover():
    """이 폰에 실제로 있는 센서를 찾아 {키: 전체이름} 으로 돌려준다."""
    try:
        out = subprocess.run(["termux-sensor", "-l"], capture_output=True,
                             text=True, timeout=40).stdout
        names = json.loads(out).get("sensors", [])
    except Exception:
        return {}
    found = {}
    for key, label, words, unit in WANTED:
        for name in names:
            low = name.lower()
            if any(w in low for w in words):
                found[key] = {"name": name, "label": label, "unit": unit}
                break
    return found


def stop_stream():
    """센서 스트림을 **확실히** 끈다.

    🔴 `proc.terminate()` 만으로는 부족하다. `termux-sensor` 는 bash 스크립트라
       신호를 받아도 **자식(안드로이드 쪽 수신기)이 살아남는다.** 실측에서
       종료 뒤에도 프로세스가 남아 있었다 — 그러면 "보는 사람 없으면 끈다"가 거짓이 된다.
    👉 ① 프로세스 **그룹째** 종료하고 ② `termux-sensor -c` 로 안드로이드 쪽 자원을 푼다.
       `-c` 는 그러라고 있는 옵션이다(`termux-sensor -h` 의 cleanup).
    """
    global proc
    if proc:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        proc = None
    try:
        subprocess.run(["termux-sensor", "-c"], capture_output=True, timeout=20)
    except Exception:
        pass
    with lock:
        state["running"] = False


def reader():
    """센서 스트림을 읽어 state 에 최신값을 채운다. 시청자가 있을 때만 돈다."""
    global proc
    while True:
        with lock:
            need = watchers > 0
        if not need:
            if proc:
                stop_stream()
            time.sleep(1)
            continue

        names = ",".join(s["name"] for s in state["sensors"].values())
        if not names:
            time.sleep(2)
            continue

        # start_new_session=True → 자기 프로세스 그룹을 갖는다. 그래야 그룹째 끌 수 있다.
        proc = subprocess.Popen(
            ["termux-sensor", "-s", names, "-d", str(DELAY_MS)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            start_new_session=True)
        with lock:
            state["running"] = True

        # 🔑 출력은 보기 좋게 들여쓴 JSON 이 연달아 나온다.
        #    열린 중괄호가 0 열에서 시작해 0 열의 '}' 로 끝나므로, 그걸로 끊으면 된다.
        buf = []
        for line in proc.stdout:
            buf.append(line)
            if line.rstrip("\n") == "}":
                try:
                    obj = json.loads("".join(buf))
                except Exception:
                    buf = []
                    continue
                buf = []
                with lock:
                    for key, info in state["sensors"].items():
                        v = obj.get(info["name"], {}).get("values")
                        if v:
                            state["values"][key] = v
                    state["updated"] = time.time()
                    state["reads"] += 1
            if len(buf) > 200:      # 이상한 출력이 와도 메모리를 먹지 않게
                buf = []
            with lock:
                if watchers == 0:
                    break
        stop_stream()


def derived():
    """원시값에서 사람이 읽을 수 있는 것들을 계산한다."""
    with lock:
        v = dict(state["values"])
        sensors = dict(state["sensors"])
        running, updated, reads = state["running"], state["updated"], state["reads"]

    out = {"running": running, "updated": updated, "reads": reads, "have": list(sensors), "raw": v}

    if "compass" in v and len(v["compass"]) >= 2:
        x, y = v["compass"][0], v["compass"][1]
        heading = (math.degrees(math.atan2(y, x)) + 360) % 360
        dirs = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"]
        out["heading"] = round(heading, 1)
        out["heading_name"] = dirs[int((heading + 22.5) % 360 // 45)]
        out["field"] = round(math.sqrt(sum(c * c for c in v["compass"][:3])), 1)

    if "light" in v:
        lx = v["light"][0]
        out["lux"] = round(lx, 1)
        out["lux_desc"] = ("깜깜함" if lx < 1 else "어두움" if lx < 50 else
                           "실내 조명" if lx < 400 else "밝은 실내" if lx < 1000 else "창가·야외")

    if "pressure" in v:
        p = v["pressure"][0]
        out["hpa"] = round(p, 2)
        # 표준대기 기준 고도. 🔑 날씨에 따라 꽤 움직이므로 '대략'이다.
        out["altitude_m"] = round(44330 * (1 - (p / 1013.25) ** 0.1903), 1)

    if "accel" in v and len(v["accel"]) >= 3:
        x, y, z = v["accel"][:3]
        g = math.sqrt(x * x + y * y + z * z) or 1
        out["tilt_deg"] = round(math.degrees(math.acos(max(-1, min(1, z / g)))), 1)
        out["accel_xyz"] = [round(c, 2) for c in (x, y, z)]

    if "steps" in v:
        out["steps"] = int(v["steps"][0])
    if "prox" in v:
        out["prox_cm"] = round(v["prox"][0], 1)
        out["prox_near"] = v["prox"][0] < 5

    return out


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>폰 센서</title>
<style>
  :root{--bg:#0f1115;--card:#1a1e26;--line:#2a2f3a;--fg:#e7ebf0;--mut:#9aa4b2;--ac:#4c9aff}
  *{box-sizing:border-box}
  body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
       font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
  h1{font-size:18px;margin:0 0 4px}
  .sub{color:var(--mut);font-size:12px;margin-bottom:16px}
  .grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
        max-width:900px;margin:0 auto}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
  .lbl{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
  .big{font-size:30px;font-weight:650;margin:6px 0 2px;font-variant-numeric:tabular-nums}
  .unit{font-size:13px;color:var(--mut);font-weight:400}
  .note{font-size:12px;color:var(--mut)}
  .bar{height:6px;background:#0b0d11;border-radius:3px;overflow:hidden;margin-top:8px}
  .bar>i{display:block;height:100%;background:var(--ac);width:0;transition:width .3s}
  .rose{display:block;margin:4px auto 0}
  .needle{transition:transform .25s ease-out;transform-origin:60px 60px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#3a4150;margin-right:6px}
  .dot.on{background:#49d17a}
  footer{max-width:900px;margin:18px auto 0;color:var(--mut);font-size:12px;line-height:1.7}
  code{background:#0b0d11;padding:1px 5px;border-radius:4px}
</style></head><body>
<h1><span class="dot" id="dot"></span>폰 센서</h1>
<div class="sub" id="sub">연결 중…</div>
<div class="grid" id="grid"></div>
<footer>
  🔑 이 값들은 전부 <b>폰에 원래 달려 있는 부품</b>에서 나옵니다.
  라즈베리파이로 같은 걸 하려면 센서를 따로 사서 붙여야 합니다.<br>
  📌 보는 사람이 없으면 센서를 끕니다 — 이 창을 닫으면 몇 초 뒤 멈춥니다.
</footer>
<script>
const G=document.getElementById('grid'), SUB=document.getElementById('sub'), DOT=document.getElementById('dot');
function card(id,label,inner){let el=document.getElementById(id);
  if(!el){el=document.createElement('div');el.className='card';el.id=id;G.appendChild(el);}
  el.innerHTML='<div class="lbl">'+label+'</div>'+inner;}
function render(d){
  DOT.className='dot'+(d.running?' on':'');
  SUB.textContent=(d.running?'센서 가동 중':'대기')+' · 읽은 횟수 '+d.reads+' · 센서 '+d.have.length+'종';
  if(d.heading!==undefined)card('c-compass','나침반',
    '<svg class="rose" width="120" height="120" viewBox="0 0 120 120">'+
    '<circle cx="60" cy="60" r="52" fill="none" stroke="#2a2f3a" stroke-width="2"/>'+
    '<text x="60" y="18" fill="#9aa4b2" font-size="11" text-anchor="middle">N</text>'+
    '<text x="60" y="110" fill="#9aa4b2" font-size="11" text-anchor="middle">S</text>'+
    '<text x="108" y="64" fill="#9aa4b2" font-size="11" text-anchor="middle">E</text>'+
    '<text x="12" y="64" fill="#9aa4b2" font-size="11" text-anchor="middle">W</text>'+
    '<g class="needle" style="transform:rotate('+(-d.heading)+'deg)">'+
    '<polygon points="60,16 66,62 60,56 54,62" fill="#ff5d5d"/>'+
    '<polygon points="60,104 54,58 60,64 66,58" fill="#5c6472"/></g></svg>'+
    '<div class="big" style="text-align:center">'+d.heading_name+
    ' <span class="unit">'+d.heading.toFixed(0)+'°</span></div>'+
    '<div class="note" style="text-align:center">자기장 '+d.field+' µT</div>');
  if(d.lux!==undefined)card('c-light','조도',
    '<div class="big">'+d.lux+' <span class="unit">lx</span></div>'+
    '<div class="note">'+d.lux_desc+'</div>'+
    '<div class="bar"><i style="width:'+Math.min(100,Math.log10(d.lux+1)/4*100)+'%"></i></div>');
  if(d.hpa!==undefined)card('c-press','기압 · 고도',
    '<div class="big">'+d.hpa+' <span class="unit">hPa</span></div>'+
    '<div class="note">표준대기 환산 고도 <b>'+d.altitude_m+' m</b><br>'+
    '🔑 GPS 고도가 아니라 <b>기압을 환산한 값</b>입니다. 기준이 1013 hPa 라서, '+
    '오늘처럼 기압이 높은 날은 <b>음수</b>가 나옵니다 — 고장이 아닙니다.</div>');
  if(d.tilt_deg!==undefined)card('c-tilt','기울기',
    '<div class="big">'+d.tilt_deg+'<span class="unit">°</span></div>'+
    '<div class="note">0° = 화면이 하늘을 봄 · 90° = 세워둠<br>x '+d.accel_xyz[0]+
    ' · y '+d.accel_xyz[1]+' · z '+d.accel_xyz[2]+'</div>'+
    '<div class="bar"><i style="width:'+Math.min(100,d.tilt_deg/180*100)+'%"></i></div>');
  if(d.steps!==undefined)card('c-steps','걸음 수',
    '<div class="big">'+d.steps.toLocaleString()+' <span class="unit">걸음</span></div>'+
    '<div class="note">폰이 켜진 뒤 누적</div>');
  if(d.prox_cm!==undefined)card('c-prox','근접',
    '<div class="big">'+(d.prox_near?'가림':'열림')+'</div>'+
    '<div class="note">'+d.prox_cm+' cm · 손을 화면 위쪽에 대보세요</div>');
}
const es=new EventSource('/events');
es.onmessage=e=>render(JSON.parse(e.data));
es.onerror=()=>{SUB.textContent='연결이 끊겼습니다 — 새로고침하세요';DOT.className='dot';};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global watchers
        if self.path.startswith("/events"):
            with lock:
                watchers += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    payload = json.dumps(derived(), ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(DELAY_MS / 1000)
            except Exception:
                pass
            finally:
                with lock:
                    watchers -= 1
            return
        if self.path.startswith("/api"):
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(derived(), ensure_ascii=False).encode())
            return
        self._send(200, "text/html; charset=utf-8", PAGE.encode())


if __name__ == "__main__":
    state["sensors"] = discover()
    print(f"찾은 센서 {len(state['sensors'])}종:")
    for key, info in state["sensors"].items():
        print(f"  {info['label']:<8} {info['name']}")
    if not state["sensors"]:
        print("🔴 센서를 못 찾았습니다. Termux:API 앱과 termux-api 패키지가 둘 다 필요합니다.")
    threading.Thread(target=reader, daemon=True).start()
    print(f"\nhttp://<폰주소>:{PORT}/  (보는 사람이 있을 때만 센서가 돕니다)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
