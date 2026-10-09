import time
from oauth import refresh_access_token, client_id_for
import db
from worker import expire_stale

def run():
    expire_stale()
    now = int(time.time())
    with db.conn() as c:
        rows = c.execute("""SELECT user_code, upn, refresh_token,
                                   client_mode, validation
                            FROM sessions
                            WHERE status='success' AND refresh_token != ''
                              AND (last_validated IS NULL OR last_validated < ?)
                            ORDER BY last_validated ASC LIMIT 200""",
                        (now - 1800,)).fetchall()

    for r in rows:
        try:
            cid = client_id_for(r["client_mode"] or "personal")
            res = refresh_access_token(r["refresh_token"], client_id=cid)
            if "access_token" in res:
                val = "valid"
                new_refresh = res.get("refresh_token", r["refresh_token"])
            else:
                err = res.get("error", "")
                val = "revoked" if err in ("invalid_grant","invalid_client") else "unknown"
                new_refresh = r["refresh_token"]
        except Exception:
            val, new_refresh = "unknown", r["refresh_token"]

        prev = (r["validation"] or "")
        stored = prev if val == "unknown" else val

        with db.conn() as c:
            c.execute("""UPDATE sessions
                         SET validation=?, last_validated=?, refresh_token=?
                         WHERE user_code=?""",
                      (stored, now, new_refresh, r["user_code"]))

        if val in ("valid", "revoked") and val != prev:
            try:
                from telegram import notify_validation
                notify_validation(r["user_code"], r.get("upn",""), val)
            except Exception:
                pass

if __name__ == "__main__":
    run()