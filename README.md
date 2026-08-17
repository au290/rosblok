# Hopper Fleet

This workspace contains the active web control plane and phone agent.

## Active Files

- `web/` - browser dashboard, HTTP control server, and test seeder
- `agent.py` - phone-side Roblox package launcher and rotation worker
- `monitor_adoptme.lua` - direct inventory reporter for the executor
- `setup.sh` - Termux installer and config repair script
- `setup-vps.ps1` - Windows 11 VPS installer and automatic-start bootstrap
- `setup-vps.sh` - optional Linux VPS installer
- `autoupdate.sh` - phone-side agent updater

## Web Dashboard

```powershell
cd web
Copy-Item config.example.txt config.txt
python -m pip install -r requirements.txt
python server.py
```

Open `http://127.0.0.1:8090/`. Use `web/README.md` for dashboard setup,
phone-agent configuration, rotations, seeding, and deployment details.

## One-line Setup

On the Windows 11 VPS, open PowerShell and run this one line. It installs Python
through `winget` when needed, creates the web virtualenv, generates missing
`KEY` and `WEB_TOKEN` values, and configures automatic startup:

```powershell
$ErrorActionPreference='Stop'; $p="$env:TEMP\panen-vps.ps1"; Invoke-WebRequest "https://raw.githubusercontent.com/au290/rosblok/main/setup-vps.ps1" -UseBasicParsing -ErrorAction Stop -OutFile $p; & powershell -NoProfile -ExecutionPolicy Bypass -File $p
```

On an Android phone running Termux, run the phone installer. It preserves
existing settings and asks only for missing required values:

```bash
pkg upgrade -y && curl -fsSL https://raw.githubusercontent.com/au290/rosblok/main/setup.sh -o setup.sh && head -n1 setup.sh | grep -q '^#!' && bash setup.sh
```

The VPS installer prints the generated phone `KEY` and browser `WEB_TOKEN` at
the end. Put the phone key in the Termux prompts/config; use the browser token
only for dashboard login. Re-running either installer updates the code while
preserving existing configuration and secrets. The Windows installer also keeps
a local credential backup beside `web/config.txt`, so a rerun does not rotate
the key or token if the config file was accidentally removed.

## Phone Agent

Run the setup script in Termux rather than downloading `agent.py` alone:

```bash
pkg upgrade -y && curl -sL https://raw.githubusercontent.com/au290/rosblok/main/setup.sh | bash
```

The phone config is stored at `/storage/emulated/0/Download/config.txt`.
The installer preserves existing values and asks only for missing required
values. The agent discovers installed Roblox packages automatically and keeps
hopper rotations on the device. Swap/Trade addon heartbeat files named
`<username>_winteraddons.json` provide each hopper's account name, drive server
advancement, and trigger same-server recovery without requiring changes to the
addon.

The default web endpoint is `http://agent.kqing.web.id`. Existing installs that
still contain the retired `https://api.kqing.web.id` default are migrated by
`setup.sh`; custom `VPS_URL` values are preserved.

## Archive

Legacy Discord/VPS scripts, old build outputs, research notes, generated data,
and the previous full README are under `archive/`. They are retained for
reference and are not part of the active web/agent workflow.
