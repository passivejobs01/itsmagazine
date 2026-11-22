#!/data/data/com.termux/files/usr/bin/python
# 폰이 돌리는 게임 서버 — "서버가 왜 필요한가" 를 한 화면으로 보여주는 데모
#
#   실행:  python ~/myapp/game.py
#   접속:  http://<폰주소>:8099
#
# 🔑 이 데모의 핵심은 게임이 아니라 **순위표**다.
#    게임 자체는 접속한 사람의 브라우저에서 돈다 — 폰은 ① 파일을 내보내고
#    ② 점수를 보관한다. 그래서 거실 TV·아빠 폰·내 노트북이 **같은 순위표**를 본다.
#    파일만 올려두는 것(dufs)과 서버가 도는 것의 차이가 여기서 눈에 보인다.
#
# ⚠️ server.py 와 달리 HTML 을 .format() 으로 만들지 않는다.
#    CSS·JS 에 중괄호가 수백 개라 {{ }} 로 일일이 이스케이프하면 반드시 사고가 난다.
#    → 페이지는 그대로 내보내고, 동적인 값은 자바스크립트가 /api 로 받아간다.
#
# 🔴 점수는 검증하지 않는다. 집 안(LAN)에서 쓰는 데모라서다.
#    마음먹으면 /api/scores 로 아무 점수나 넣을 수 있다 — 밖으로 열지 않는다.
import html
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("GAME_PORT", "8099"))
HERE = Path(__file__).resolve().parent
DATA = HERE / "scores.json"
MAX_KEEP = 50          # 파일에 남기는 최대 기록
TOP_N = 10             # 화면에 보여주는 수
START = time.time()

_lock = threading.Lock()
_NAME_BAD = re.compile(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ _.\-]")


# ── 점수 저장 ──────────────────────────────────────────────────────────────
def load():
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {"scores": list(d.get("scores", [])), "plays": int(d.get("plays", 0))}
    except Exception:
        pass
    return {"scores": [], "plays": 0}


def save(d):
    # 🔑 임시 파일에 쓰고 바꿔 끼운다. 쓰는 중에 폰이 꺼져도 파일이 깨지지 않는다.
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8", newline="\n")
    os.replace(tmp, DATA)


def device_name():
    # 🔑 폰 이름을 화면에 띄우면 "이게 폰이다" 가 한 줄로 전달된다.
    #    hostname 은 Termux 에서 늘 'localhost' 라 쓸 수 없다 → 안드로이드 속성을 읽는다.
    for prop in ("ro.product.marketname", "ro.product.model"):
        try:
            v = subprocess.run(["getprop", prop], capture_output=True,
                               text=True, timeout=3).stdout.strip()
            if v:
                return v
        except Exception:
            pass
    h = socket.gethostname()
    return "이 폰" if h in ("", "localhost") else h


DEVICE = device_name()


def clean_name(v):
    s = unicodedata.normalize("NFC", str(v or "")).strip()
    s = _NAME_BAD.sub("", s)[:12].strip()
    return s or "익명"


def add_score(name, score):
    with _lock:
        d = load()
        row = {"name": clean_name(name), "score": int(score), "ts": int(time.time())}
        d["scores"].append(row)
        d["scores"].sort(key=lambda r: (-r["score"], r["ts"]))
        d["scores"] = d["scores"][:MAX_KEEP]
        d["plays"] = int(d.get("plays", 0)) + 1
        save(d)
        rank = next((i + 1 for i, r in enumerate(d["scores"]) if r is row), None)
        return rank, row["ts"]


def bump_play():
    with _lock:
        d = load()
        d["plays"] = int(d.get("plays", 0)) + 1
        save(d)
        return d["plays"]


def snapshot():
    d = load()
    return {
        "top": d["scores"][:TOP_N],
        "plays": d.get("plays", 0),
        "uptime_sec": round(time.time() - START, 1),
        "host": DEVICE,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "port": PORT,
    }


# ── 페이지 ─────────────────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#4aa3df">
<title>폰이 돌리는 게임</title>
<style>
  :root{
    color-scheme: light dark;
    --bg:#eef1f5; --card:#fff; --tx:#1f2328; --mu:#6b7280;
    --ac:#2f80c2; --line:rgba(0,0,0,.09); --me:#fff3cd;
  }
  @media (prefers-color-scheme: dark){
    :root{ --bg:#101216; --card:#191c22; --tx:#e8eaed; --mu:#99a0a8;
           --ac:#6cb3e8; --line:rgba(255,255,255,.10); --me:#3b3523; }
  }
  *{ box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body{ margin:0; }
  body{
    min-height:100dvh; background:var(--bg); color:var(--tx);
    font-family:system-ui,-apple-system,"Segoe UI","Noto Sans KR",sans-serif;
    padding:14px 14px calc(14px + env(safe-area-inset-bottom));
    display:flex; flex-direction:column; align-items:center; gap:12px;
  }
  .wrap{ width:100%; max-width:420px; display:flex; flex-direction:column; gap:12px; }
  .badge{
    display:flex; align-items:center; gap:7px; justify-content:center;
    font-size:12.5px; color:var(--mu); letter-spacing:.01em;
  }
  .dot{ width:7px; height:7px; border-radius:50%; background:#30c06a;
        box-shadow:0 0 0 3px rgba(48,192,106,.18); flex:none; }
  .stage{ position:relative; width:100%; aspect-ratio:360/640; border-radius:18px;
          overflow:hidden; box-shadow:0 10px 34px rgba(0,0,0,.18); background:#8ecbe8; }
  canvas{ display:block; width:100%; height:100%; touch-action:manipulation; }
  .veil{
    position:absolute; inset:0; display:grid; place-items:center; text-align:center;
    padding:22px; background:rgba(8,16,24,.52); backdrop-filter:blur(3px);
    color:#fff; gap:0; transition:opacity .18s;
  }
  .veil.hide{ opacity:0; pointer-events:none; }
  .veil h2{ margin:0 0 8px; font-size:clamp(24px,7vw,32px); letter-spacing:-.02em; }
  .veil p{ margin:0; font-size:14.5px; line-height:1.6; opacity:.92; }
  .big{ font-size:46px; font-weight:700; margin:2px 0 4px; font-variant-numeric:tabular-nums; }
  .hint{ margin-top:14px; font-size:13px; opacity:.75; }
  .row{ display:flex; gap:8px; margin-top:16px; }
  input,button{ font:inherit; border-radius:11px; border:0; padding:11px 14px; }
  input{ flex:1; min-width:0; background:rgba(255,255,255,.93); color:#1f2328; }
  button{ background:var(--ac); color:#fff; font-weight:600; cursor:pointer; }
  button.ghost{ background:rgba(255,255,255,.18); color:#fff; }
  button:active{ transform:translateY(1px); }
  .card{ background:var(--card); border-radius:16px; padding:14px 16px;
         box-shadow:0 4px 18px rgba(0,0,0,.07); }
  .card h3{ margin:0 0 10px; font-size:13px; letter-spacing:.06em; text-transform:uppercase;
            color:var(--mu); font-weight:600; }
  table{ width:100%; border-collapse:collapse; font-size:14.5px; }
  td{ padding:7px 2px; border-top:1px solid var(--line); }
  tr:first-child td{ border-top:0; }
  .rk{ width:30px; color:var(--mu); font-variant-numeric:tabular-nums; }
  .sc{ text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }
  .ago{ width:64px; text-align:right; color:var(--mu); font-size:12px; }
  .mine{ background:var(--me); }
  .empty{ color:var(--mu); font-size:14px; padding:6px 2px; }
  .note{ font-size:12.5px; color:var(--mu); line-height:1.65; }
  .note b{ color:var(--tx); }
  .meta{ display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:var(--mu);
         justify-content:center; }
</style></head>
<body>
<div class="wrap">

  <div class="badge"><span class="dot"></span>
    <span>이 게임은 <b id="who">폰</b>이 보내주고 있습니다</span>
  </div>

  <div class="stage">
    <canvas id="cv"></canvas>

    <div class="veil" id="vStart">
      <div>
        <h2>🐦 날아보기</h2>
        <p>탭하거나 <b>스페이스</b>로 날갯짓.<br>관을 피해서 멀리 가면 됩니다.</p>
        <div class="row"><button id="bStart">시작</button></div>
        <div class="hint">최고 기록은 <b>폰에 저장</b>됩니다</div>
      </div>
    </div>

    <div class="veil hide" id="vDead">
      <div>
        <h2>부딪혔습니다</h2>
        <div class="big" id="dScore">0</div>
        <p id="dMsg">점수를 순위표에 올려보세요</p>
        <div class="row">
          <input id="nick" maxlength="12" placeholder="이름 (12자)" autocomplete="off">
          <button id="bSave">등록</button>
        </div>
        <div class="row"><button class="ghost" id="bAgain">다시 하기</button></div>
      </div>
    </div>
  </div>

  <div class="card">
    <h3>순위표 · 폰이 보관합니다</h3>
    <table id="board"><tr><td class="empty">불러오는 중…</td></tr></table>
  </div>

  <div class="card">
    <div class="note">
      🔑 <b>게임은 지금 이 화면의 브라우저가 돌리고 있습니다.</b>
      폰이 하는 일은 두 가지입니다 — <b>파일을 보내주는 것</b>과
      <b>점수를 기억하는 것</b>.<br>
      그래서 다른 기기로 접속해도 <b>같은 순위표</b>가 보입니다.
      폰을 껐다 켜도 기록은 남아 있습니다.
    </div>
  </div>

  <div class="meta">
    <span id="mPlays">—</span><span id="mUp">—</span><span id="mPy">—</span>
  </div>
</div>

<script>
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const cv = $("cv"), ctx = cv.getContext("2d");
  const W = 360, H = 640;              // 논리 해상도 (실제 크기는 CSS가 정한다)
  const GROUND = 92;

  // ── 캔버스 해상도 맞추기 (레티나 대응) ─────────────────────────────
  function fit() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = true;
  }
  fit();
  addEventListener("resize", fit);

  // ── 상태 ───────────────────────────────────────────────────────────
  const G = 0.46, FLAP = -7.3, MAXV = 11.5;
  const PW = 64, GAP0 = 158, GAPMIN = 118, SPACING = 212;
  let mode = "ready";                  // ready | play | dead
  let bird, pipes, score, best = 0, spd, frame, shake, flash;

  try { best = parseInt(localStorage.getItem("flap.best") || "0", 10) || 0; } catch (e) {}

  function reset() {
    bird = { x: 96, y: H * 0.42, v: 0, r: 13, rot: 0, wing: 0 };
    pipes = [];
    for (let i = 0; i < 3; i++) pipes.push(makePipe(W + 140 + i * SPACING));
    score = 0; spd = 2.45; frame = 0; shake = 0; flash = 0;
  }

  function makePipe(x) {
    const gap = Math.max(GAPMIN, GAP0 - score * 1.6);
    const top = 70 + Math.random() * (H - GROUND - gap - 150);
    return { x, top, gap, scored: false };
  }

  // ── 구름 (시차 배경) ───────────────────────────────────────────────
  const clouds = Array.from({ length: 7 }, () => ({
    x: Math.random() * W, y: 40 + Math.random() * 230,
    s: 0.45 + Math.random() * 0.9, sp: 0.18 + Math.random() * 0.34,
  }));

  // ── 소리 (첫 입력 뒤에만) ──────────────────────────────────────────
  let ac = null;
  function beep(f, d, type, vol) {
    if (!ac) return;
    try {
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = type || "square"; o.frequency.value = f;
      g.gain.value = vol == null ? 0.045 : vol;
      g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + d);
      o.connect(g); g.connect(ac.destination);
      o.start(); o.stop(ac.currentTime + d);
    } catch (e) {}
  }
  function audioOn() {
    if (ac) return;
    try { ac = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { ac = null; }
  }

  // ── 그리기 ─────────────────────────────────────────────────────────
  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawSky() {
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "#5fb4e0");
    g.addColorStop(0.55, "#9ad6ee");
    g.addColorStop(1, "#d9f0f7");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

    ctx.fillStyle = "rgba(255,255,255,.72)";
    for (const c of clouds) {
      const s = c.s;
      ctx.beginPath();
      ctx.ellipse(c.x, c.y, 26 * s, 13 * s, 0, 0, 7);
      ctx.ellipse(c.x + 20 * s, c.y + 3 * s, 18 * s, 10 * s, 0, 0, 7);
      ctx.ellipse(c.x - 19 * s, c.y + 4 * s, 15 * s, 9 * s, 0, 0, 7);
      ctx.fill();
    }
  }

  function drawPipe(p) {
    const bot = p.top + p.gap;
    const body = ctx.createLinearGradient(p.x, 0, p.x + PW, 0);
    body.addColorStop(0, "#4caf50");
    body.addColorStop(0.35, "#73d175");
    body.addColorStop(1, "#3c8c40");

    ctx.fillStyle = body;
    ctx.fillRect(p.x, 0, PW, p.top);
    ctx.fillRect(p.x, bot, PW, H - GROUND - bot);

    ctx.fillStyle = "#3f9644";
    roundRect(p.x - 5, p.top - 26, PW + 10, 26, 6); ctx.fill();
    roundRect(p.x - 5, bot, PW + 10, 26, 6); ctx.fill();

    ctx.fillStyle = "rgba(255,255,255,.22)";
    ctx.fillRect(p.x + 9, 0, 7, p.top - 26);
    ctx.fillRect(p.x + 9, bot + 26, 7, H - GROUND - bot - 26);
  }

  function drawGround() {
    const y = H - GROUND;
    ctx.fillStyle = "#ded9a4"; ctx.fillRect(0, y, W, GROUND);
    ctx.fillStyle = "#7ec850"; ctx.fillRect(0, y, W, 14);
    ctx.fillStyle = "#68b043";
    const off = (frame * spd) % 24;
    for (let x = -24; x < W + 24; x += 24) {
      ctx.beginPath();
      ctx.moveTo(x - off, y + 14); ctx.lineTo(x - off + 12, y + 14); ctx.lineTo(x - off + 6, y + 6);
      ctx.closePath(); ctx.fill();
    }
    ctx.fillStyle = "rgba(0,0,0,.055)";
    for (let x = -18; x < W + 18; x += 18) ctx.fillRect(x - off, y + 30, 9, 5);
  }

  function drawBird() {
    ctx.save();
    ctx.translate(bird.x, bird.y);
    ctx.rotate(bird.rot);

    ctx.fillStyle = "rgba(0,0,0,.14)";
    ctx.beginPath(); ctx.ellipse(1, 3, 15, 12, 0, 0, 7); ctx.fill();

    const g = ctx.createLinearGradient(0, -13, 0, 13);
    g.addColorStop(0, "#ffe074"); g.addColorStop(1, "#f0ac2b");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.ellipse(0, 0, 14, 11.5, 0, 0, 7); ctx.fill();

    const w = Math.sin(bird.wing) * 7;
    ctx.fillStyle = "#e89a20";
    ctx.beginPath(); ctx.ellipse(-3, 2 + w * 0.35, 8.5, 5.5 - Math.abs(w) * 0.2, w * 0.1, 0, 7);
    ctx.fill();

    ctx.fillStyle = "#fff";
    ctx.beginPath(); ctx.arc(6, -4, 4.4, 0, 7); ctx.fill();
    ctx.fillStyle = "#23262b";
    ctx.beginPath(); ctx.arc(7.4, -4, 2.1, 0, 7); ctx.fill();

    ctx.fillStyle = "#f2663c";
    ctx.beginPath();
    ctx.moveTo(12, -1); ctx.lineTo(20.5, 1.6); ctx.lineTo(12, 4.6);
    ctx.closePath(); ctx.fill();
    ctx.restore();
  }

  function drawScore() {
    if (mode === "ready") return;
    ctx.font = "700 44px system-ui,sans-serif";
    ctx.textAlign = "center";
    ctx.lineWidth = 7; ctx.strokeStyle = "rgba(20,40,60,.55)";
    ctx.strokeText(String(score), W / 2, 76);
    ctx.fillStyle = "#fff"; ctx.fillText(String(score), W / 2, 76);
    if (best > 0) {
      ctx.font = "600 13px system-ui,sans-serif";
      ctx.fillStyle = "rgba(255,255,255,.85)";
      ctx.lineWidth = 4;
      ctx.strokeText("내 최고 " + best, W / 2, 98);
      ctx.fillText("내 최고 " + best, W / 2, 98);
    }
  }

  function render() {
    ctx.save();
    if (shake > 0) {
      ctx.translate((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake);
    }
    drawSky();
    for (const p of pipes) drawPipe(p);
    drawGround();
    drawBird();
    drawScore();
    ctx.restore();
    if (flash > 0) {
      ctx.fillStyle = "rgba(255,255,255," + (flash * 0.5).toFixed(3) + ")";
      ctx.fillRect(0, 0, W, H);
    }
  }

  // ── 물리 ───────────────────────────────────────────────────────────
  function hit() {
    const b = bird;
    if (b.y + b.r > H - GROUND) return true;
    if (b.y - b.r < -40) return true;         // 천장은 살짝 관대하게
    for (const p of pipes) {
      if (b.x + b.r < p.x || b.x - b.r > p.x + PW) continue;
      if (b.y - b.r < p.top || b.y + b.r > p.top + p.gap) return true;
    }
    return false;
  }

  function step() {
    frame++;
    if (shake > 0) shake *= 0.86;
    if (flash > 0) flash -= 0.08;

    for (const c of clouds) {
      c.x -= c.sp * (mode === "play" ? 1 : 0.4);
      if (c.x < -50) { c.x = W + 40; c.y = 40 + Math.random() * 230; }
    }

    if (mode === "ready") {
      bird.y = H * 0.42 + Math.sin(frame / 20) * 7;
      bird.rot = Math.sin(frame / 20) * 0.08;
      bird.wing += 0.26;
      return;
    }
    if (mode === "dead") {
      if (bird.y + bird.r < H - GROUND) {
        bird.v = Math.min(bird.v + G * 1.3, MAXV);
        bird.y += bird.v;
        bird.rot = Math.min(bird.rot + 0.07, 1.5);
      }
      return;
    }

    bird.v = Math.min(bird.v + G, MAXV);
    bird.y += bird.v;
    bird.rot = Math.max(-0.5, Math.min(1.1, bird.v / 11));
    bird.wing += bird.v < 0 ? 0.55 : 0.2;

    spd = 2.45 + Math.min(score * 0.035, 1.5);
    for (const p of pipes) p.x -= spd;

    if (pipes.length && pipes[0].x + PW < -20) {
      pipes.shift();
      pipes.push(makePipe(pipes[pipes.length - 1].x + SPACING));
    }
    for (const p of pipes) {
      if (!p.scored && p.x + PW < bird.x - bird.r) {
        p.scored = true; score++;
        beep(660 + Math.min(score, 20) * 14, 0.07, "square", 0.04);
      }
    }
    if (hit()) die();
  }

  function flap() {
    audioOn();
    if (mode === "ready") { mode = "play"; hideVeils(); }
    if (mode !== "play") return;
    bird.v = FLAP;
    bird.wing = 0;
    beep(420, 0.05, "triangle", 0.035);
  }

  function die() {
    mode = "dead";
    shake = 13; flash = 1;
    beep(170, 0.22, "sawtooth", 0.05);
    setTimeout(() => beep(110, 0.3, "sawtooth", 0.04), 110);
    if (score > best) {
      best = score;
      try { localStorage.setItem("flap.best", String(best)); } catch (e) {}
    }
    $("dScore").textContent = String(score);
    const t = topScores;
    const willRank = t.length < 10 || score > (t[t.length - 1] || {}).score;
    $("dMsg").textContent = score === 0
      ? "한 칸도 못 갔습니다. 다시!"
      : (willRank ? "🎉 순위표에 오를 점수입니다!" : "점수를 순위표에 올려보세요");
    $("bSave").disabled = false;
    $("bSave").textContent = "등록";
    $("vDead").classList.remove("hide");
    postPlay();
  }

  function hideVeils() {
    $("vStart").classList.add("hide");
    $("vDead").classList.add("hide");
  }

  // ── 루프 (고정 시간 간격 — 120Hz 화면에서도 같은 속도) ─────────────
  let acc = 0, last = performance.now();
  const STEP = 1000 / 60;
  function loop(now) {
    acc += Math.min(now - last, 250);
    last = now;
    while (acc >= STEP) { step(); acc -= STEP; }
    render();
    requestAnimationFrame(loop);
  }
  reset();
  requestAnimationFrame(loop);

  // ── 입력 ───────────────────────────────────────────────────────────
  cv.addEventListener("pointerdown", (e) => { e.preventDefault(); flap(); });
  addEventListener("keydown", (e) => {
    if (e.code === "Space" || e.code === "ArrowUp") {
      e.preventDefault();
      if (mode === "dead") restart(); else flap();
    }
  });
  $("bStart").addEventListener("click", () => { audioOn(); mode = "play"; hideVeils(); });
  $("bAgain").addEventListener("click", restart);

  function restart() { reset(); mode = "play"; hideVeils(); }

  // ── 서버와 이야기하기 ──────────────────────────────────────────────
  let topScores = [], myTs = 0;

  function ago(ts) {
    const s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    if (s < 60) return "방금";
    if (s < 3600) return Math.floor(s / 60) + "분";
    if (s < 86400) return Math.floor(s / 3600) + "시간";
    return Math.floor(s / 86400) + "일";
  }

  function paint(d) {
    topScores = d.top || [];
    const tb = $("board");
    tb.textContent = "";
    if (!topScores.length) {
      const tr = tb.insertRow();
      const td = tr.insertCell();
      td.className = "empty";
      td.textContent = "아직 기록이 없습니다 — 첫 번째가 되어보세요";
      return;
    }
    const medal = ["🥇", "🥈", "🥉"];
    topScores.forEach((r, i) => {
      const tr = tb.insertRow();
      if (r.ts === myTs) tr.className = "mine";
      const a = tr.insertCell(); a.className = "rk"; a.textContent = medal[i] || (i + 1);
      const b = tr.insertCell(); b.textContent = r.name;       // textContent = 주입 걱정 없음
      const c = tr.insertCell(); c.className = "sc"; c.textContent = r.score;
      const e = tr.insertCell(); e.className = "ago"; e.textContent = ago(r.ts);
    });
    $("mPlays").textContent = "플레이 " + (d.plays || 0) + "회";
    const u = Math.floor(d.uptime_sec || 0);
    $("mUp").textContent = "서버 가동 " +
      (u >= 3600 ? Math.floor(u / 3600) + "시간" : u >= 60 ? Math.floor(u / 60) + "분" : u + "초");
    $("mPy").textContent = "Python " + (d.python || "?") + " · " + (d.machine || "");
    if (d.host) $("who").textContent = d.host;
  }

  async function refresh() {
    try {
      const r = await fetch("/api/scores", { cache: "no-store" });
      paint(await r.json());
    } catch (e) {}
  }

  async function postPlay() {
    try { await fetch("/api/play", { method: "POST" }); } catch (e) {}
  }

  $("bSave").addEventListener("click", async () => {
    const btn = $("bSave");
    const name = $("nick").value.trim() || "익명";
    try { localStorage.setItem("flap.nick", name); } catch (e) {}
    btn.disabled = true; btn.textContent = "보내는 중";
    try {
      const r = await fetch("/api/scores", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, score: score }),
      });
      const d = await r.json();
      myTs = d.ts || 0;
      paint(d);
      btn.textContent = d.rank ? d.rank + "위!" : "등록됨";
      $("dMsg").textContent = d.rank && d.rank <= 10
        ? "🏆 " + d.rank + "위로 순위표에 올랐습니다"
        : "등록했습니다";
    } catch (e) {
      btn.disabled = false; btn.textContent = "다시 시도";
    }
  });

  try {
    const n = localStorage.getItem("flap.nick");
    if (n) $("nick").value = n;
  } catch (e) {}

  refresh();
  setInterval(() => { if (mode !== "play") refresh(); }, 7000);   // 할 때는 방해하지 않는다
})();
</script>
</body></html>"""


# ── HTTP ───────────────────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    server_version = "PhoneGame"
    protocol_version = "HTTP/1.1"

    def _send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            code,
        )

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/scores":
            return self._json(snapshot())
        self._send(b"not found\n", "text/plain; charset=utf-8", 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/play":
            return self._json({"plays": bump_play()})

        if path != "/api/scores":
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)

        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 2048:
            return self._json({"error": "bad length"}, 400)

        try:
            req = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
            score = int(req.get("score", 0))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        if not (0 <= score <= 99999):
            return self._json({"error": "bad score"}, 400)

        rank, ts = add_score(req.get("name"), score)
        out = snapshot()
        out["rank"] = rank
        out["ts"] = ts      # 🔑 방금 올린 줄을 화면에서 강조하려고 그 시각을 돌려준다
        return self._json(out)

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), flush=True)


if __name__ == "__main__":
    if not DATA.exists():
        save({"scores": [], "plays": 0})
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    srv.daemon_threads = True
    print(
        "game server on 0.0.0.0:%d  (python %s, %s)"
        % (PORT, platform.python_version(), platform.machine()),
        flush=True,
    )
    print("scores: %s" % DATA, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
