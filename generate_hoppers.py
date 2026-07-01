"""
generate_hoppers.py
Master server list lives in link.txt. Assignment lives in ONE file, servers.txt:

    1: 1-20      <- hopper 1 runs link.txt lines 1-20
    2: 21-40
    ...

This script writes a default servers.txt (chunks of 20) and the hopperN.lua files.
Each hopper re-reads link.txt + servers.txt every cycle, so editing either is live.
Each also obeys cmd/hN.txt per tick:
    goto<k> / <k>   -> jump to RF<k> in this hopper's slice
    skip            -> hop now
    pause / resume  -> hold on current server
    pin <URL>       -> launch URL and HOLD there (used by /all_goto)
    stop            -> exit
"""

from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LINK_FILE  = SCRIPT_DIR / "link.txt"
MAP_FILE   = SCRIPT_DIR / "servers.txt"
CHUNK_SIZE = 20


def get_pkg(n: int) -> str:
    return "com.roblox.clien" + "vwxyz"[(n - 1) % 5]


HOPPER_TEMPLATE = '''-- ==========================================
-- ROBLOX PRIVATE SERVER HOPPER {n}   (Package: {pkg})
-- Pool: link.txt   Assignment: servers.txt   Control: cmd/h{n}.txt
-- ==========================================

local PKG_ID   = "{pkg}"
local DELAY    = 240
local HOPPER_N = {n}
local CMD_FILE = "cmd/h" .. HOPPER_N .. ".txt"

local function log(msg)
    local line = "[" .. os.date("%H:%M:%S") .. "] [" .. PKG_ID .. "] " .. msg
    print(line)
    local f = io.open("hopper" .. HOPPER_N .. ".log", "a")
    if f then f:write(line .. "\\n"); f:close() end
end

local function read_lines(path)
    local t = {{}}
    local f = io.open(path, "r")
    if f then
        for line in f:lines() do
            line = line:gsub("%s+$", "")
            if #line > 0 and not line:match("^#") then t[#t + 1] = line end
        end
        f:close()
    end
    return t
end

local function my_range()
    local f = io.open("servers.txt", "r")
    if f then
        for line in f:lines() do
            local h, a, b = line:match("^%s*(%d+)%s*:%s*(%d+)%s*%-%s*(%d+)")
            if h and tonumber(h) == HOPPER_N then f:close(); return tonumber(a), tonumber(b) end
        end
        f:close()
    end
    return 1, 0
end

local function load_servers()
    local all = read_lines("link.txt")
    local from, to = my_range()
    if to > #all then to = #all end
    local t = {{}}
    for i = from, to do t[#t + 1] = all[i] end
    return t
end

local function read_cmd()
    local f = io.open(CMD_FILE, "r")
    if not f then return "" end
    local c = f:read("*l") or ""
    f:close()
    return (c:gsub("^%s+", ""):gsub("%s+$", ""))   -- trim only (keep internal spaces for "pin <url>")
end
local function clear_cmd() os.remove(CMD_FILE) end

local function launch(link, name)
    os.execute("su -c 'am force-stop " .. PKG_ID .. "' 2>/dev/null")
    os.execute("sleep 2")
    log("Launching: " .. name)
    os.execute('su -c "am start -a android.intent.action.VIEW -p ' .. PKG_ID .. ' -d \\'' .. link .. '\\'"')
end

local idx = 1
while true do
    local pin = read_cmd():match("^pin%s+(.+)$")
    if pin then
        -- /all_goto : everyone lands on this link and HOLDS until /continue
        launch(pin, "PIN")
        log("Pinned to event link -- holding until continue")
        while true do
            os.execute("sleep 3")
            if not read_cmd():match("^pin%s+") then break end
            print("  [" .. PKG_ID .. "] PINNED -- holding")
        end
        clear_cmd()
        log("Released -- resuming rotation")
    else
        local servers = load_servers()
        if #servers == 0 then
            log("no servers assigned (check servers.txt / link.txt)")
            os.execute("sleep 5")
        else
            if idx > #servers then idx = 1 end
            local name = "RF" .. idx
            launch(servers[idx], name)

            local start_time = os.time()
            while true do
                os.execute("sleep 3")
                local c = read_cmd()
                if c ~= "" then
                    if c:match("^pin%s+") then break end          -- pin requested -> handle at top
                    local jump = c:match("^goto%s*(%d+)$") or c:match("^(%d+)$")
                    if jump then
                        clear_cmd(); idx = tonumber(jump); log("Jump -> RF" .. idx); break
                    elseif c == "skip" then
                        clear_cmd(); idx = (idx % #servers) + 1; log("Skip"); break
                    elseif c == "stop" then
                        clear_cmd(); log("Stop"); os.exit(0)
                    elseif c == "pause" then
                        log("Paused")
                        repeat os.execute("sleep 2") until read_cmd() ~= "pause"
                        clear_cmd(); start_time = os.time(); log("Resumed")
                    end
                end
                if os.time() - start_time >= DELAY then
                    idx = (idx % #servers) + 1
                    break
                else
                    print("  [" .. PKG_ID .. "] " .. name .. " -- " .. (os.time() - start_time) .. "s / " .. DELAY .. "s")
                end
            end
        end
    end
end
'''


def main():
    total = len([l for l in LINK_FILE.read_text(encoding="utf-8").splitlines() if l.strip()])
    n_hoppers = max(1, (total + CHUNK_SIZE - 1) // CHUNK_SIZE)

    map_lines = ["# hopper : firstLink-lastLink  (line numbers in link.txt, 1-based)"]
    for i in range(1, n_hoppers + 1):
        first = (i - 1) * CHUNK_SIZE + 1
        last  = min(i * CHUNK_SIZE, total)
        map_lines.append(f"{i}: {first}-{last}")
    MAP_FILE.write_text("\n".join(map_lines) + "\n", encoding="utf-8")

    for i in range(1, n_hoppers + 1):
        (SCRIPT_DIR / f"hopper{i}.lua").write_text(
            HOPPER_TEMPLATE.format(n=i, pkg=get_pkg(i)), encoding="utf-8")

    print(f"{total} links -> {n_hoppers} hopper(s)")
    print(f"Wrote servers.txt + hopper1..{n_hoppers}.lua")
    print("Edit servers.txt to reassign ranges. Restart hoppers to switch over.")


if __name__ == "__main__":
    main()
