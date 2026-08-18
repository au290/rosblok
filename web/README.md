# Hopper Fleet Web Control

This folder is a separate web control plane. The existing Discord code in the
repository is unchanged.

The web server keeps the same phone-agent protocol as the existing `server.py`:

```text
POST /api/{phone}/poll
X-Key: <KEY>
```

Point each phone's `VPS_URL` at `http://agent.kqing.web.id` (or your own
reverse-proxy URL). Use the current `agent.py` so device rotation settings can
be saved and reported to the dashboard.

## Run

For the Windows 11 VPS, open PowerShell and run:

```powershell
$ErrorActionPreference='Stop'; $p="$env:TEMP\panen-vps.ps1"; Invoke-WebRequest "https://raw.githubusercontent.com/au290/rosblok/main/setup-vps.ps1" -UseBasicParsing -ErrorAction Stop -OutFile $p; & powershell -NoProfile -ExecutionPolicy Bypass -File $p
```

If the GitHub repository is private, use the authenticated bootstrap from the
root `README.md`; an unauthenticated raw GitHub URL returns `404` by design.

The installer uses `$env:PANEN_DIR` when set and otherwise installs to
`$env:USERPROFILE\panen`. Run PowerShell as Administrator to register an
at-startup scheduled task; without elevation it installs a per-user Startup
shortcut instead. Credentials are preserved across reruns in `web/config.txt`
and its local `.credentials` recovery file. Re-running this command is the
one-command VPS update: it downloads the latest web files and restarts the
web task without replacing your local config.

The same installer also downloads `rejoin_listener.py` and creates
`web\rejoin_listener.txt`. Fill `SOURCE_SCRIPT_KEY` and `SOURCE_PASSWORD` in
that file once, then rerun the same command. The installer registers
`HopperFleetRejoinListener` to start at boot and keeps its output in
`web\rejoin_listener.log`. To provide them during the first run without
editing the file, set `PANEN_REJOIN_SCRIPT_KEY` and `PANEN_REJOIN_PASSWORD`
before invoking the installer. The listener posts only aggregate counts to
`http://127.0.0.1:<PORT>` and reads the web `KEY` from `config.txt`.

```powershell
cd web
Copy-Item config.example.txt config.txt
notepad config.txt
python -m pip install -r requirements.txt
python server.py
```

Open `http://<server>:8090/` and log in with `WEB_TOKEN`.

## Seed test data without phones

Keep the web server running, then open a second terminal at the repository root:

```powershell
python web/seeder.py
```

The seeder reads `HOST`, `PORT`, `KEY`, and `PHONES` from `web/config.txt`. It
continuously reports simulated phone health, hopper progress and rotations,
live trade states/items, multiple Adopt Me accounts, pet variants, rarities,
and prices. Dashboard
commands are applied to its in-memory state, so start/stop, navigation, pins,
logs, and rotation saves can be tested from the browser. Stop it with `Ctrl+C`;
its simulated state resets on the next run.

Use phone IDs that are not running a real agent at the same time, since both
would publish reports to the same dashboard rows.

Useful overrides:

```powershell
python web/seeder.py --phones A,B --hoppers 5
python web/seeder.py --url http://127.0.0.1:8090 --key JAWIR
python web/seeder.py --hoppers 1,3,5 --once
```

The dashboard has three sections in the left sidebar:

- **Executive summary** shows the income estimate, total hopper count, total
  pet count, phone health, and visual value/volume graphs.
- **Hopper control** gives every reported hopper its own row. Select rows to
  start or stop them together, or use row actions to control one hopper. The
  phone is routed automatically from the row. The gear action edits that
  hopper's private-server rotation and loop mode.
- **Pet trackstat** lists each reported pet variant with count, full-grown
  progress, rarity, and estimated value.

The executive summary and pet trackstat always combine every reported account
across all phones. Phone filtering is available only in **Hopper control**,
where it narrows the hopper table and command routing remains explicit.

Optional display metadata can be copied from `hoppers.example.json` to
`hoppers.json`. Keys use `<phone>:<hopper>` (for example `A:1`) and may define
`device`, `account`, `package`, and `target` labels. Runtime state still comes
from the phone agent.

The `KEY` must be identical to the phone `KEY`. `WEB_TOKEN` is only for the
browser and should be different. Use HTTPS or put the server behind a reverse
proxy before exposing it to the internet. Browser login is kept for 24 hours in
a signed HttpOnly cookie and survives normal page refreshes and web-server
restarts.

## Move a phone to the web control plane

On the phone, edit `/storage/emulated/0/Download/config.txt`:

```text
VPS_URL=http://agent.kqing.web.id
KEY=the-same-value-as-web-config
PHONE=A
HOPPERS=1,2,3,4,5
PLACE_ID=920587237
WINDOW_MODE=auto
START_ON_BOOT=true
AUTO_DETECT_PACKAGES=true
```

Restart `agent.py`. The phone should appear online in the dashboard. Existing
`link.txt`, `servers.txt`, Delta scripts, and inventory files remain on the
phone. `agent.py` now launches and monitors each Roblox clone directly, so it
does not run `hopper*.lua` or create one tmux window per hopper.

The account column is filled automatically from the `username` stored in each
Roblox package's `/data/data/<package>/shared_prefs/prefs.xml`. If only
`userid_long` is available, the phone resolves it through Roblox's public user
endpoint. The agent reads this package-local file through root, never opens the
WebView Cookies database, and sends only the resulting username. The heartbeat
filename `<username>_winteraddons.json` remains a fallback. Short network
stalls do not mark a phone offline; the dashboard uses a minimum 60-second
heartbeat grace period.

For a complete Termux install, run the repository's `setup.sh` one-liner rather
than curling `agent.py` by itself. Re-running `setup.sh` preserves non-empty
configuration and asks only for required values that are missing or blank.

Package discovery runs `pm list packages`, selects installed package IDs containing
`roblox`, sorts them, and maps them to the configured `HOPPERS` order. No package
name needs to be hardcoded. For a stable explicit mapping, either provide an
ordered list or per-hopper overrides:

```text
PACKAGES=com.roblox.clientv,com.roblox.clientw,com.roblox.clientx
PACKAGE_1=com.roblox.clientv
PACKAGE_2=com.roblox.clientw
```

`PACKAGE_<n>` wins over `PACKAGES`, then auto-detection. If there are fewer
installed Roblox packages than configured hoppers, the remaining rows show
`Not detected` and will not launch until a package is installed or explicitly
mapped. Share URLs that omit a place ID use `PLACE_ID`. Set
`START_ON_BOOT=true` to resume all configured hoppers whenever the agent process
starts; otherwise start them from Hopper control.

## Trade-driven hopping

The phone agent watches the existing Swap/Trade addon heartbeat named
`<username>_winteraddons.json`. No Lua changes or extra reporting script are
required. It checks Delta's shared workspace and package-private executor
workspace locations, using root access for cloned package storage when needed.

The heartbeat must contain `status` and Unix-seconds `ts`. A fresh `completed`
status advances to the next saved server. Fresh `disconnected` or `error`
statuses relaunch the current server. Every other fresh status keeps the
current server running. After Play, the agent first polls the Android package
with `pidof`. It waits for that package to appear, then waits without a timeout
for the first fresh in-package heartbeat. Once a fresh heartbeat has been seen,
a missing file or a heartbeat older than 40 seconds relaunches the current
server and never advances it. If the package process exits at any point, the
same server is relaunched. Automatic retries use a short cooldown to avoid a
relaunch loop. Pinned hoppers always remain on their pinned server.

There is no fixed server timer. The Runtime column starts at the first fresh
heartbeat written by the script after Roblox launches and continues without a
cap. It resets only when the agent launches or rejoins a server; runtime never
causes a hop by itself.

`count`, `items`, and `meta` are validated and shown in the Trade column. This
live trade report is kept only in agent/server memory and is not saved on the
VPS. The filename also supplies a fallback account label when package-local
preferences and optional `hoppers.json` metadata do not provide one.

Defaults can be overridden in the phone's `config.txt` when an executor uses a
different workspace or heartbeat timing:

```text
TRADE_DIRS=/storage/emulated/0/Delta/Workspace
TRADE_STALE_SECONDS=40
```

`WINDOW_MODE=auto` checks Android's freeform-window feature and global setting.
It uses freeform mode `5` when supported and otherwise omits the flag so Android
chooses the window mode. Use `WINDOW_MODE=5` to force the POC behavior, or
`WINDOW_MODE=off` to always use Android's default.

## Device-persistent hopper rotations

Open **Hopper control** and use the gear action on a hopper row. A save is sent
to that row's phone and stored locally in:

```text
/storage/emulated/0/Download/rotations.json
/storage/emulated/0/Download/rotations/h<hopper>.txt
```

The modal stays open after a save and shows the links reported back from the
device. The phone also rebuilds `link.txt` and `servers.txt` for compatibility
with old tools. The direct runtime applies the saved links and one-pass mode
itself. The trade heartbeat decides when to advance. Package health is also checked with `pidof`,
relaunching the current server if the app exits.

The VPS is only a control and display layer. A saved rotation keeps running
from shared device storage while the VPS is offline. After the VPS restarts,
the next agent poll restores the saved links in the web dashboard.

## Direct Adopt Me inventory reporting

`monitor_adoptme.lua` can report the currently logged-in Adopt Me account
straight to this web server. It no longer needs `writefile`, the local `inv/`
folder, or `agent.py` to move inventory data. Install the script in the
executor's autoexec, then set these values near the top of the script:

```lua
local VPS_URL = "https://your-domain.example" -- or http://<server>:8090 on a trusted LAN
local KEY = "the-same-value-as-web-config"
```

Use one monitor per Roblox account. Reports are merged by account name and are
independent of the phone-agent fleet, so no phone ID is required. Accounts that
stop reporting are removed after the inventory grace period. The monitor only
reports inventory; hopper lifecycle commands, board/health data, and the
current StarPets price worker still come from `agent.py`. Run the agent when
those features are needed. If you only need the inventory pages, the monitor
can report without an agent.

To show the HighSpecs balance on Executive Summary, add the API key to the
server's `web/config.txt` (never to frontend code):

```text
HIGHSPEC_API_KEY=hsk_your_key_here
```

The server calls `GET https://api.highspec.gg/api/v1/external/balance`, caches
the result for `HIGHSPEC_BALANCE_TTL` seconds, and displays USD. HighSpecs
points are converted from satang to THB, then divided by 32 THB per USD.
The raw points remain in the card note. Leave the key empty to disable it.

To show the ZeroPoint Face Unlock balance on Executive Summary, add this to
the same server-side config:

```text
ZEROUNLOCK_API_BASE=https://zeropoint.to/api/faceunlock-api
ZEROUNLOCK_API_KEY=ZP_FaceUnlock_your_key_here
ZEROUNLOCK_BALANCE_TTL=60
```

The server calls `GET /balance` with `X-API-Key` and displays the effective
spendable balance in USD, with total and reserved amounts in the card note.
The key is never sent to the browser.

## Rejoin account comparison

`rejoin_listener.py` is an optional, separate listener for a WinterHub/Rejoin
dashboard. It logs in to the source dashboard, counts usernames reported by its
`/api/agents` endpoint, and sends only an aggregate `online/total` value to this
dashboard. Account rows, passwords, and cookies stay on the listener machine.

Set it up once:

```powershell
Copy-Item rejoin_listener.example.txt rejoin_listener.txt
python -m pip install -r requirements.txt
python rejoin_listener.py --once
python rejoin_listener.py
```

Fill `SOURCE_SCRIPT_KEY`, `SOURCE_PASSWORD`, and `ACCOUNT_DB` (or use
`TOTAL_ACCOUNTS=observed`) in `rejoin_listener.txt`. The Windows installer
sets `TARGET_URL` to the local server and `TARGET_KEY_FILE=config.txt`, so it
uses the web server `KEY` without copying it. `SOURCE_CREDENTIALS_FILE` can
point at a local credentials script such as `../resource/nega.py` instead.
For a manual remote target, set `TARGET_KEY` to the web server `KEY`, never the
browser `WEB_TOKEN`. The dashboard shows the result as `online / total` in the
Executive Summary. A listener update older than `REJOIN_STATS_GRACE` seconds
is shown as stale.

`ONLINE_MODE` controls what “online” means: `ingame` (default) requires a
connected agent, a running Roblox package, and `game_state=ingame`; `connected`
counts usernames on connected agents, and `running` requires a running Roblox
package. Use
`reported` to match the simple username collection used by `resource/nega.py`.
The summary line chart keeps the latest 240 listener snapshots in memory.

The direct monitor endpoint is authenticated with the same shared key:

```text
POST /api/monitor/poll
X-Key: <KEY>
```

## API

The browser uses a session cookie after `POST /api/login`.

```text
GET  /healthz
GET  /api/status?phone=all
POST /api/command
POST /api/rejoin/stats
```

The listener payload is aggregate-only:

```json
{"source":"rejoin_listener","online_accounts":243,"total_accounts":600,"mode":"connected"}
```

Example command body:

```json
{"phone":"A","action":"start","hopper":1}
```

Supported actions include device-persistent hopper rotations, hopper lifecycle,
server navigation, pins, assignments, logs, links, scripts, price refresh, and
auto-trade configuration.
