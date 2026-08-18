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
through `winget` when needed, downloads the latest web dashboard and Rejoin
listener, updates the virtualenv, preserves existing secrets, and configures
automatic startup:

```powershell
$ErrorActionPreference='Stop'; $p="$env:TEMP\panen-vps.ps1"; Invoke-WebRequest "https://raw.githubusercontent.com/au290/rosblok/main/setup-vps.ps1" -UseBasicParsing -ErrorAction Stop -OutFile $p; & powershell -NoProfile -ExecutionPolicy Bypass -File $p
```

Because this repository is private, the anonymous command above returns
`404`. Use this authenticated bootstrap instead. Set `GITHUB_TOKEN` to a
fine-grained GitHub token with read-only **Contents** access to this
repository, or enter it when prompted:

```powershell
$token=$env:GITHUB_TOKEN; if (-not $token) { $token=Read-Host 'GitHub read-only token' }; $h=@{Authorization="Bearer $token";Accept='application/vnd.github.raw';'User-Agent'='panen-bootstrap'}; $ErrorActionPreference='Stop'; $p="$env:TEMP\panen-vps.ps1"; Invoke-WebRequest "https://api.github.com/repos/au290/rosblok/contents/setup-vps.ps1?ref=main" -Headers $h -UseBasicParsing -OutFile $p; $env:PANEN_GITHUB_TOKEN=$token; & powershell -NoProfile -ExecutionPolicy Bypass -File $p
```

Run the same line whenever you want to update the VPS. It restarts the web
task and, when Rejoin credentials are present, the Rejoin listener task too.
The listener config is kept at `web\rejoin_listener.txt` and is ignored by Git.
On the first install, fill `SOURCE_SCRIPT_KEY` and `SOURCE_PASSWORD` there,
then run the same update line once more. The listener will then start at VPS
boot and write its log to `web\rejoin_listener.log`.

For a non-interactive first install, pass the credentials as process-scoped
PowerShell environment variables. They are written only to the VPS config and are not
part of the repository:

```powershell
$env:PANEN_REJOIN_SCRIPT_KEY='your-script-key'; $env:PANEN_REJOIN_PASSWORD='your-password'; $ErrorActionPreference='Stop'; $p="$env:TEMP\panen-vps.ps1"; Invoke-WebRequest "https://raw.githubusercontent.com/au290/rosblok/main/setup-vps.ps1" -UseBasicParsing -ErrorAction Stop -OutFile $p; & powershell -NoProfile -ExecutionPolicy Bypass -File $p
```

The listener sends only aggregate online/total counts to the local web server;
source usernames, passwords, and cookies stay on the VPS.

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
hopper rotations on the device. It reads each package's local
`shared_prefs/prefs.xml` to identify the logged-in Roblox username without
opening or transmitting its Cookies database. Swap/Trade addon heartbeat files
named `<username>_winteraddons.json` drive server advancement and trigger
same-server recovery without requiring changes to the addon.

The default web endpoint is `http://agent.kqing.web.id`. Existing installs that
still contain the retired `https://api.kqing.web.id` default are migrated by
`setup.sh`; custom `VPS_URL` values are preserved.

## Archive

Legacy Discord/VPS scripts, old build outputs, research notes, generated data,
and the previous full README are under `archive/`. They are retained for
reference and are not part of the active web/agent workflow.
