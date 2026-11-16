#!/data/data/com.termux/files/usr/bin/node
/* 폰 기능 제어판 — 브라우저에서 누르면 저쪽 방 폰이 반응한다.
 *
 *   실행:  node ~/myapp/control.js        (포트 8094)
 *   보기:  http://<폰주소>:8094/
 *
 * 🔑 센서 대시보드(sensors.py)와 짝이다.
 *      sensors.py  = 폰에서 **읽는다**  (나침반·조도·기압…)
 *      control.js  = 폰을 **움직인다**  (토치·진동·알림·음성…)
 *    둘이 합쳐져야 "폰이라서 되는 것"이 다 보인다.
 *
 * 🔴 보안 두 가지 — 하드웨어를 건드리는 앱이라 그냥 열어두지 않는다.
 *    ① 로그인을 건다. 4장에서 만든 ~/.webdav_user · ~/.webdav_pass 를 그대로 쓴다.
 *    ② 🔴 브라우저에서 받은 글자를 **쉘에 넘기지 않는다.**
 *       execFile 로 **인자 배열**을 넘긴다. 문자열로 이어 붙여 sh 에 주면
 *       누가 `; rm -rf ~` 를 적어 보내면 그대로 실행된다.
 *
 * npm install 이 필요 없다 — 기본 모듈만 쓴다.
 */

const http = require("http");
const { execFile } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PORT = Number(process.env.PORT || 8094);
const HOME = os.homedir();
const MAX_TEXT = 200;          // 사용자가 보내는 글자 수 상한

// ── 로그인 정보 (4장에서 만든 파일) ─────────────────────────────────────────
function readTrim(p) {
  try { return fs.readFileSync(p, "utf8").replace(/\r?\n$/, ""); } catch { return ""; }
}
const USER = readTrim(path.join(HOME, ".webdav_user"));
const PASS = readTrim(path.join(HOME, ".webdav_pass"));

// ── 실행할 수 있는 것만 적어둔다 (화이트리스트) ─────────────────────────────
// 🔑 "무엇을 실행할지"를 코드에 고정하고, 사용자는 **값만** 넣는다.
//    cmd 는 함수이고, 받은 글자는 반드시 **인자 배열**의 한 칸으로 들어간다.
const ACTIONS = {
  vibrate:  { label: "진동",        cmd: (t) => ["termux-vibrate", "-d", String(Math.min(2000, Number(t) || 300))] },
  torch_on: { label: "손전등 켜기",  cmd: () => ["termux-torch", "on"] },
  torch_off:{ label: "손전등 끄기",  cmd: () => ["termux-torch", "off"] },
  notify:   { label: "알림 보내기",  cmd: (t) => ["termux-notification", "--id", "ctl",
                                                 "--title", "폰 홈서버", "--content", t || "브라우저에서 보냈습니다"] },
  notify_clear: { label: "알림 지우기", cmd: () => ["termux-notification-remove", "ctl"] },
  speak:    { label: "말하기",      cmd: (t) => ["termux-tts-speak", t || "안녕하세요"] },
};

function run(argv, timeoutMs = 25000) {
  return new Promise((resolve) => {
    // 🔴 shell 을 쓰지 않는다. execFile 은 인자를 쉘에 해석시키지 않고 그대로 넘긴다.
    execFile(argv[0], argv.slice(1), { timeout: timeoutMs }, (err, stdout, stderr) => {
      resolve({ ok: !err, out: (stdout || "").trim(), err: (stderr || String(err || "")).trim() });
    });
  });
}

async function status() {
  // 🔴 클립보드(termux-clipboard-get/set)는 여기 넣지 않는다.
  //    Android 10(갤럭시 노트9)에서 **앞에 있든 뒤에 있든 빈 값만** 돌아왔다.
  //    안드로이드가 클립보드 접근을 조이면서 막힌 것으로 보인다 — 실측으로 확인했다.
  const bat = await run(["termux-battery-status"], 15000);
  let battery = null;
  try { battery = JSON.parse(bat.out); } catch { /* 권한이 없으면 null */ }
  return {
    battery,
    uptime_sec: Math.floor(process.uptime()),
    node: process.version,
  };
}

// ── 로그인 확인 ─────────────────────────────────────────────────────────────
function authed(req) {
  if (!USER || !PASS) return true;              // 설정이 없으면 막지 않는다(안내는 화면에)
  const h = req.headers.authorization || "";
  if (!h.startsWith("Basic ")) return false;
  const [u, p] = Buffer.from(h.slice(6), "base64").toString("utf8").split(":");
  return u === USER && p === PASS;
}

const PAGE = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>폰 제어판</title>
<style>
  :root{--bg:#0f1115;--card:#1a1e26;--line:#2a2f3a;--fg:#e7ebf0;--mut:#9aa4b2;--ac:#4c9aff}
  *{box-sizing:border-box}
  body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
       font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
  h1{font-size:18px;margin:0 0 4px}
  .sub{color:var(--mut);font-size:12px;margin-bottom:16px}
  .wrap{max-width:720px;margin:0 auto}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:14px;margin-bottom:12px}
  .lbl{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;
       margin-bottom:10px}
  button{background:#232833;color:var(--fg);border:1px solid var(--line);border-radius:8px;
         padding:10px 14px;font-size:14px;cursor:pointer;margin:0 6px 6px 0}
  button:hover{border-color:var(--ac)}
  button:active{transform:translateY(1px)}
  input{background:#0b0d11;color:var(--fg);border:1px solid var(--line);border-radius:8px;
        padding:10px 12px;font-size:14px;width:100%;margin-bottom:8px}
  .big{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}
  .note{font-size:12px;color:var(--mut);line-height:1.7}
  #toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);
         background:#232833;border:1px solid var(--line);border-radius:8px;
         padding:10px 16px;font-size:13px;opacity:0;transition:opacity .2s;pointer-events:none}
  #toast.on{opacity:1}
</style></head><body>
<div class="wrap">
<h1>폰 제어판</h1>
<div class="sub" id="sub">불러오는 중…</div>

<div class="card">
  <div class="lbl">바로 눌러보기</div>
  <button onclick="go('vibrate',300)">📳 진동</button>
  <button onclick="go('torch_on')">🔦 손전등 켜기</button>
  <button onclick="go('torch_off')">🌑 손전등 끄기</button>
  <button onclick="go('notify_clear')">🧹 알림 지우기</button>
  <div class="note">🔑 누르는 순간 <b>저쪽에 있는 폰</b>이 반응합니다. 미니PC로는 못 하는 것들입니다.</div>
</div>

<div class="card">
  <div class="lbl">글자를 보내기</div>
  <input id="txt" maxlength="200" placeholder="폰에 보낼 문장을 적으세요">
  <button onclick="go('speak',t())">🔊 폰이 소리 내어 읽기</button>
  <button onclick="go('notify',t())">🔔 폰에 알림 띄우기</button>
  <div class="note">🔴 여기 적은 글자는 <b>쉘로 넘어가지 않습니다</b> — 명령의 인자 한 칸으로만 들어갑니다.
  문자열로 이어 붙여 실행했다면 <code>; rm -rf ~</code> 같은 걸 적어 보낼 수 있습니다.</div>
</div>

<div class="card">
  <div class="lbl">폰 상태</div>
  <div class="big" id="bat">—</div>
  <div class="note" id="batnote"></div>
</div>
</div>
<div id="toast"></div>
<script>
const T=document.getElementById('toast');
function toast(m){T.textContent=m;T.className='on';setTimeout(()=>T.className='',1600);}
function t(){return document.getElementById('txt').value;}
async function go(action,text){
  toast('보내는 중…');
  const r=await fetch('/do/'+action,{method:'POST',headers:{'Content-Type':'text/plain'},
                                     body:String(text??'')});
  const d=await r.json();
  toast(d.ok?(d.label+' ✓'):(d.label+' 실패: '+(d.err||'').slice(0,60)));
  load();
}
async function load(){
  const d=await (await fetch('/api/status')).json();
  if(d.battery){
    document.getElementById('bat').textContent=d.battery.percentage+'% · '+d.battery.temperature+'°C';
    document.getElementById('batnote').textContent=
      d.battery.status+' · '+d.battery.plugged+' · 건강 '+d.battery.health;
  } else {
    document.getElementById('bat').textContent='—';
    document.getElementById('batnote').textContent='배터리 정보를 못 읽었습니다 (Termux:API 권한 확인)';
  }
  document.getElementById('sub').textContent='Node '+d.node+' · 가동 '+d.uptime_sec+'초';
}
load(); setInterval(load,10000);
</script></body></html>`;

const server = http.createServer(async (req, res) => {
  const send = (code, type, body) => {
    res.writeHead(code, { "Content-Type": type, "Content-Length": Buffer.byteLength(body) });
    res.end(body);
  };

  if (!authed(req)) {
    res.writeHead(401, { "WWW-Authenticate": 'Basic realm="phone control"' });
    return res.end("로그인이 필요합니다");
  }

  const url = new URL(req.url, "http://x");

  if (url.pathname === "/api/status") {
    return send(200, "application/json; charset=utf-8", JSON.stringify(await status()));
  }

  if (url.pathname.startsWith("/do/")) {
    const key = url.pathname.slice(4);
    const act = ACTIONS[key];
    if (!act) return send(404, "application/json; charset=utf-8",
                          JSON.stringify({ ok: false, label: key, err: "모르는 동작" }));
    // 본문(글자)을 모아서 길이를 자른다
    let body = "";
    for await (const chunk of req) {
      body += chunk;
      if (body.length > MAX_TEXT * 4) break;
    }
    const text = body.slice(0, MAX_TEXT);
    const r = await run(act.cmd(text));
    return send(200, "application/json; charset=utf-8",
                JSON.stringify({ ok: r.ok, label: act.label, out: r.out, err: r.err }));
  }

  return send(200, "text/html; charset=utf-8", PAGE);
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`폰 제어판  http://<폰주소>:${PORT}/`);
  console.log(USER && PASS ? "로그인: ~/.webdav_user · ~/.webdav_pass 사용"
                           : "🔴 로그인 정보가 없습니다 — 4장(install-dufs.sh)을 먼저 하세요.");
});
