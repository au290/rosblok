# Hopper Fleet

This workspace contains the active web control plane and phone agent.

## Active Files

- `web/` - browser dashboard, HTTP control server, and test seeder
- `agent.py` - phone-side Roblox package launcher and rotation worker
- `monitor_adoptme.lua` - direct inventory reporter for the executor
- `setup.sh` - Termux installer and config repair script
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

## Phone Agent

Run the setup script in Termux rather than downloading `agent.py` alone:

```bash
pkg upgrade -y && curl -sL https://raw.githubusercontent.com/au290/rosblok/main/setup.sh | bash
```

The phone config is stored at `/storage/emulated/0/Download/config.txt`.
The installer preserves existing values and asks only for missing required
values. The agent discovers installed Roblox packages automatically and keeps
hopper rotations on the device. Swap/Trade addon heartbeat files named
`<username>_winteraddons.json` drive server advancement and same-server
recovery without requiring changes to the addon.

The default web endpoint is `http://agent.kqing.web.id`. Existing installs that
still contain the retired `https://api.kqing.web.id` default are migrated by
`setup.sh`; custom `VPS_URL` values are preserved.

## Archive

Legacy Discord/VPS scripts, old build outputs, research notes, generated data,
and the previous full README are under `archive/`. They are retained for
reference and are not part of the active web/agent workflow.
