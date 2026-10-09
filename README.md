# ewp-installer

Desktop installer for a Linux panel deployed to your own VPS over SSH.

Two actions:
- **INSTALL** — full install. Wipes the previous install and rebuilds.
- **INSTALL ONLY PANEL** — replaces panel files only; keeps database, config, TLS, nginx.

Both open the panel URL when done.

## Requirements
- Python 3.10+, git, on the installer machine
- SSH access to the VPS
- Ubuntu 22.04 or 24.04 on the VPS
- Domain on Cloudflare (free plan)
- Cloudflare User API token, Zone / DNS / Edit on the domain

## Run
```

pip install -r requirements.txt
python installer.py

```

## Notes
`installer_config.json` holds SSH credentials in plain text and is gitignored. Do not commit it.