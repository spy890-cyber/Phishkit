import base64, json, requests
import config

SCOPE     = config.SCOPE
AUTH_URL  = config.AUTH_URL
TOKEN_URL = config.TOKEN_URL

def _client_id():
    m = getattr(config, "MODE", "personal")
    if m == "work":
        return getattr(config, "CLIENT_ID_WORK", config.CLIENT_ID)
    if m == "custom":
        cid = getattr(config, "CLIENT_ID_CUSTOM", "")
        return cid or getattr(config, "CLIENT_ID_PERSONAL", config.CLIENT_ID)
    return getattr(config, "CLIENT_ID_PERSONAL", config.CLIENT_ID)

def client_id_for(mode: str) -> str:
    if mode == "work":
        return getattr(config, "CLIENT_ID_WORK", config.CLIENT_ID)
    if mode == "custom":
        cid = getattr(config, "CLIENT_ID_CUSTOM", "")
        return cid or getattr(config, "CLIENT_ID_PERSONAL", config.CLIENT_ID)
    return getattr(config, "CLIENT_ID_PERSONAL", config.CLIENT_ID)

def request_device_code() -> dict:
    r = requests.post(AUTH_URL,
                      data={"client_id": _client_id(), "scope": SCOPE},
                      timeout=15)
    r.raise_for_status()
    return r.json()

def decode_jwt_claims(jwt_token: str) -> dict:
    try:
        p = jwt_token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}

def extract_upn(access_token: str) -> str:
    c = decode_jwt_claims(access_token)
    return c.get("upn") or c.get("preferred_username") or c.get("unique_name") or ""

def poll_once(device_code: str) -> dict:
    r = requests.post(TOKEN_URL, data={
        "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
        "client_id":   _client_id(),
        "device_code": device_code,
    }, timeout=15)
    return r.json()

def refresh_access_token(refresh_token: str, client_id: str = None) -> dict:
    r = requests.post(TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "client_id":     client_id or _client_id(),
        "refresh_token": refresh_token,
        "scope":         SCOPE,
    }, timeout=15)
    return r.json()