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
  hopper's private-server rotation, progress window, and loop mode.
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
proxy before exposing it to the internet.

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
current server running. A missing file after launch grace, or a heartbeat older
than 40 seconds, relaunches the current server and never advances it. Pinned
hoppers always remain on their pinned server.

`count`, `items`, and `meta` are validated and shown in the Trade column. This
live trade report is kept only in agent/server memory and is not saved on the
VPS. The filename also supplies the account label when no optional
`hoppers.json` label exists.

Defaults can be overridden in the phone's `config.txt` when an executor uses a
different workspace or heartbeat timing:

```text
TRADE_DIRS=/storage/emulated/0/Delta/Workspace
TRADE_STALE_SECONDS=40
TRADE_LAUNCH_GRACE=45
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
itself. The progress window controls the visual timer; the trade heartbeat
decides when to advance. Package health is also checked with `pidof`,
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
local PHONE = "A"
```

Use one monitor per Roblox account. Monitors using the same `PHONE` are merged
by account name, and accounts that stop reporting are removed after the
inventory grace period. The monitor only reports inventory; hopper lifecycle
commands, board/health data, and the current StarPets price worker still come
from `agent.py`. Run the agent when those features are needed. If you only need
the inventory pages, the monitor can report without an agent.

The direct monitor endpoint is the same authenticated poll endpoint:

```text
POST /api/{phone}/poll
X-Key: <KEY>
```

## API

The browser uses a session cookie after `POST /api/login`.

```text
GET  /healthz
GET  /api/status?phone=all
POST /api/command
```

Example command body:

```json
{"phone":"A","action":"start","hopper":1}
```

Supported actions include device-persistent hopper rotations, hopper lifecycle,
server navigation, pins, assignments, logs, links, scripts, price refresh, and
auto-trade configuration.
