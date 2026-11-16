// 폰에서 도는 Node 서버 — "이런 것도 된다" 데모용
const http = require("http");
const os = require("os");

const PORT = 8094;
const START = Date.now();

const uptimeStr = () => {
  let s = Math.floor((Date.now() - START) / 1000);
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  return (h ? `${h}시간 ` : "") + (h || m ? `${m}분 ` : "") + `${s}초`;
};

const page = () => `<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Node.js on Phone</title>
<style>
  :root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --tx:#1f2328; --mu:#6b7280; --ac:#539e43; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14161a; --card:#1d2026; --tx:#e8eaed; --mu:#9aa0a6; --ac:#7fc96d; }
  }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; padding:16px;
         background:var(--bg); color:var(--tx);
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  .card { background:var(--card); border-radius:20px; padding:40px 32px; text-align:center;
          max-width:520px; width:100%; box-shadow:0 8px 32px rgba(0,0,0,.10); }
  .badge { display:inline-block; font-size:13px; letter-spacing:.08em; text-transform:uppercase;
           color:var(--mu); margin-bottom:14px; }
  h1 { margin:0 0 6px; font-size:clamp(34px,9vw,52px); color:var(--ac); letter-spacing:-.02em; }
  .sub { font-size:17px; color:var(--mu); margin-bottom:28px; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  td { padding:9px 4px; border-top:1px solid color-mix(in srgb, var(--mu) 25%, transparent); text-align:left; }
  td:last-child { text-align:right; font-variant-numeric:tabular-nums; color:var(--mu); }
  .foot { margin-top:24px; font-size:13px; color:var(--mu); }
</style></head>
<body><div class="card">
  <div class="badge">Running on an old Android phone</div>
  <h1>⬢ Node.js</h1>
  <div class="sub">이 페이지는 <b>폰 안의 Node.js</b>가 직접 만들어 보냈습니다</div>
  <table>
    <tr><td>버전</td><td>Node ${process.version}</td></tr>
    <tr><td>포트</td><td>${PORT}</td></tr>
    <tr><td>가동 시간</td><td>${uptimeStr()}</td></tr>
    <tr><td>기기</td><td>${os.arch()}</td></tr>
  </table>
  <div class="foot">JSON 으로 보려면 <code>/api</code></div>
</div></body></html>`;

http.createServer((req, res) => {
  if (req.url.startsWith("/api")) {
    const body = JSON.stringify({
      language: "Node.js",
      version: process.version,
      port: PORT,
      uptime_sec: Number(((Date.now() - START) / 1000).toFixed(1)),
      machine: os.arch(),
      host: os.hostname(),
    }, null, 2);
    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
    return res.end(body);
  }
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(page());
}).listen(PORT, "0.0.0.0", () => {
  console.log(`node ${process.version} listening on 0.0.0.0:${PORT}`);
});
