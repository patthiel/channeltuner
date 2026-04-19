#!/usr/bin/env node
/**
 * tv_remote.js — Browser-based remote control for tv_channels.py
 *
 * Usage:
 *   node tv_remote.js <simulator_port> [remote_port]
 *
 * Examples:
 *   node tv_remote.js 7777          # simulator on 7777, remote on 8888
 *   node tv_remote.js 7777 9000     # remote on custom port 9000
 *
 * Then open http://<your-ip>:<remote_port> on your phone.
 *
 * The browser can't reach 127.0.0.1 on the server directly, so this
 * server acts as a proxy — phone hits /cmd/next → server curls simulator.
 */

const http = require("http");

const SIM_PORT   = parseInt(process.argv[2], 10) || 7777;
const REMOTE_PORT = parseInt(process.argv[3], 10) || 8888;
const SIM_HOST   = "127.0.0.1";

// ---------------------------------------------------------------------------
// Proxy: forward a command to the simulator and call back with success/fail
// ---------------------------------------------------------------------------
function sendCommand(cmd, callback) {
  const options = {
    hostname: SIM_HOST,
    port:     SIM_PORT,
    path:     "/" + cmd,
    method:   "GET",
  };
  const req = http.request(options, (res) => {
    res.resume();
    callback(res.statusCode === 204 || res.statusCode === 200);
  });
  req.on("error", () => callback(false));
  req.setTimeout(2000, () => { req.destroy(); callback(false); });
  req.end();
}

// ---------------------------------------------------------------------------
// HTML — retro TV remote, served inline so there are zero file dependencies
// ---------------------------------------------------------------------------
const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>📺 TV Remote</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Bebas+Neue&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0a0c10;
    --remote:    #12151c;
    --panel:     #1a1e28;
    --border:    #252a38;
    --amber:     #e8a020;
    --amber-dim: #7a5010;
    --red:       #c0392b;
    --green:     #27ae60;
    --text:      #cdd6e0;
    --dim:       #4a5568;
    --btn-face:  #1e2330;
    --btn-top:   #262c3e;
    --btn-shadow:#0d0f16;
    --radius:    14px;
  }

  html, body {
    height: 100%;
    background: var(--bg);
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'Share Tech Mono', monospace;
    overflow: hidden;
  }

  /* Scanline overlay */
  body::after {
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.08) 2px,
      rgba(0,0,0,0.08) 4px
    );
    pointer-events: none;
    z-index: 100;
  }

  .remote {
    width: min(340px, 94vw);
    background: var(--remote);
    border-radius: 28px 28px 48px 48px;
    border: 1px solid var(--border);
    padding: 24px 20px 36px;
    box-shadow:
      0 0 0 1px #070810,
      0 24px 64px rgba(0,0,0,0.8),
      inset 0 1px 0 rgba(255,255,255,0.04);
    display: flex;
    flex-direction: column;
    gap: 18px;
    user-select: none;
  }

  /* Top display panel */
  .display {
    background: #060810;
    border-radius: 10px;
    border: 1px solid var(--border);
    padding: 10px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: inset 0 2px 8px rgba(0,0,0,0.6);
  }

  .display-label {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 11px;
    letter-spacing: 3px;
    color: var(--dim);
  }

  .status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--amber-dim);
    box-shadow: 0 0 0 2px rgba(232,160,32,0.1);
    transition: background 0.15s, box-shadow 0.15s;
  }
  .status-dot.active {
    background: var(--amber);
    box-shadow: 0 0 8px var(--amber), 0 0 2px var(--amber);
  }

  /* Section label */
  .section-label {
    font-size: 9px;
    letter-spacing: 3px;
    color: var(--dim);
    text-align: center;
    text-transform: uppercase;
  }

  /* Channel rocker — big UP / DOWN */
  .rocker {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
  }

  .rocker-btn {
    width: 100%;
    height: 64px;
    background: linear-gradient(180deg, var(--btn-top) 0%, var(--btn-face) 100%);
    border: 1px solid var(--border);
    border-bottom: 3px solid var(--btn-shadow);
    border-radius: 10px;
    color: var(--text);
    font-family: 'Bebas Neue', sans-serif;
    font-size: 22px;
    letter-spacing: 2px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    transition: transform 0.07s, border-bottom-width 0.07s, background 0.07s;
    -webkit-tap-highlight-color: transparent;
    touch-action: manipulation;
  }

  .rocker-btn .arrow {
    font-size: 18px;
    color: var(--amber);
  }

  .rocker-btn:active {
    transform: translateY(2px);
    border-bottom-width: 1px;
    background: linear-gradient(180deg, var(--btn-face) 0%, var(--btn-shadow) 100%);
  }

  /* D-pad style grid for secondary controls */
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }

  .btn {
    height: 58px;
    background: linear-gradient(180deg, var(--btn-top) 0%, var(--btn-face) 100%);
    border: 1px solid var(--border);
    border-bottom: 3px solid var(--btn-shadow);
    border-radius: var(--radius);
    color: var(--text);
    font-family: 'Share Tech Mono', monospace;
    font-size: 11px;
    letter-spacing: 1px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    transition: transform 0.07s, border-bottom-width 0.07s, background 0.07s;
    -webkit-tap-highlight-color: transparent;
    touch-action: manipulation;
    text-transform: uppercase;
  }

  .btn .icon {
    font-size: 20px;
    line-height: 1;
  }

  .btn:active {
    transform: translateY(2px);
    border-bottom-width: 1px;
    background: linear-gradient(180deg, var(--btn-face) 0%, var(--btn-shadow) 100%);
  }

  .btn.accent {
    border-color: var(--amber-dim);
    color: var(--amber);
  }
  .btn.accent .icon { color: var(--amber); }
  .btn.accent:active { background: linear-gradient(180deg, #1a1506 0%, #0d0b04 100%); }

  .btn.danger {
    border-color: #4a1510;
    color: #e74c3c;
  }
  .btn.danger .icon { color: #e74c3c; }
  .btn.danger:active { background: linear-gradient(180deg, #1a0605 0%, #0d0302 100%); }

  .btn.wide {
    grid-column: span 2;
  }

  /* Divider */
  .divider {
    height: 1px;
    background: var(--border);
    margin: 0 8px;
  }

  /* Feedback flash on button */
  @keyframes flash {
    0%   { opacity: 1; }
    50%  { opacity: 0.3; }
    100% { opacity: 1; }
  }
  .flashing { animation: flash 0.2s ease; }

</style>
</head>
<body>
<div class="remote">

  <div class="display">
    <span class="display-label">📺 &nbsp; TV REMOTE</span>
    <div class="status-dot" id="dot"></div>
  </div>

  <div class="section-label">channel</div>

  <div class="rocker">
    <button class="rocker-btn" data-cmd="next">
      <span class="arrow">▲</span> CH UP
    </button>
    <button class="rocker-btn" data-cmd="prev">
      <span class="arrow">▼</span> CH DOWN
    </button>
  </div>

  <div class="divider"></div>

  <div class="section-label">playback</div>

  <div class="grid-2">
    <button class="btn accent wide" data-cmd="unpause">
      <span class="icon">⏯</span>
      pause / play
    </button>

    <button class="btn accent" data-cmd="back">
      <span class="icon">↩</span>
      last ch
    </button>

    <button class="btn" data-cmd="path">
      <span class="icon">📂</span>
      show path
    </button>

    <button class="btn danger" data-cmd="quit">
      <span class="icon">⏻</span>
      quit
    </button>
  </div>

</div>

<script>
  const dot = document.getElementById('dot');

  function flash(btn, ok) {
    btn.classList.add('flashing');
    dot.classList.add('active');
    setTimeout(() => {
      btn.classList.remove('flashing');
      dot.classList.remove('active');
    }, 300);
  }

  document.querySelectorAll('[data-cmd]').forEach(btn => {
    btn.addEventListener('click', () => {
      const cmd = btn.dataset.cmd;
      fetch('/cmd/' + cmd)
        .then(r => flash(btn, r.ok))
        .catch(() => flash(btn, false));
    });
  });
</script>
</body>
</html>`;

// ---------------------------------------------------------------------------
// HTTP server
// ---------------------------------------------------------------------------
const server = http.createServer((req, res) => {
  // Proxy command to simulator
  if (req.url.startsWith("/cmd/")) {
    const cmd = req.url.slice(5).split("?")[0];
    sendCommand(cmd, (ok) => {
      res.writeHead(ok ? 200 : 502, { "Content-Type": "text/plain" });
      res.end(ok ? "ok" : "error");
    });
    return;
  }

  // Serve remote UI for all other paths
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(HTML);
});

server.listen(REMOTE_PORT, "0.0.0.0", () => {
  console.log("");
  console.log("  📱  TV Remote");
  console.log("  Simulator port : " + SIM_PORT);
  console.log("  Remote UI      : http://0.0.0.0:" + REMOTE_PORT);
  console.log("");
  console.log("  Open on your phone:");
  console.log("  http://<your-machine-ip>:" + REMOTE_PORT);
  console.log("");
  console.log("  Ctrl-C to stop");
});
