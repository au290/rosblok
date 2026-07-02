-- monitor_adoptme.lua — Adopt Me inventory dump (file-only, no server)
-- Place in your executor autoexec. Every INTERVAL seconds it writes
-- inv/<player>.json into the executor workspace; the Discord bot's /inv reads it.

local RS          = game:GetService("ReplicatedStorage")
local HttpService = game:GetService("HttpService")
local Players     = game:GetService("Players")

repeat task.wait() until game:IsLoaded()
task.wait(3)

local INTERVAL = 30
local LP       = Players.LocalPlayer

-- ── Load Adopt Me ClientData ───────────────────────────────────────
local ClientData
do
    local okF, Fsys = pcall(function() return require(RS.Fsys) end)
    if okF and Fsys and Fsys.load then
        local okL, mod = pcall(function() return Fsys.load("ClientData") end)
        if okL then ClientData = mod end
    end
    if not ClientData then
        local okD, mod = pcall(function() return require(RS.ClientModules.Core.ClientData) end)
        if okD then ClientData = mod end
    end
end
if not ClientData or type(ClientData.get_data) ~= "function" then
    warn("[monitor] could not load Adopt Me ClientData — aborting")
    return
end

-- ── Read stats ─────────────────────────────────────────────────────
local function getMe()
    local ok, all = pcall(function() return ClientData.get_data() end)
    if not ok or type(all) ~= "table" then return nil end
    return all[LP.Name]
end

-- shallow, JSON-safe copy of a table (one level, values stringified) — used to
-- dump one raw pet so we can see the real field names for neon/age/rarity.
local function shallow(t)
    local o = {}
    if type(t) == "table" then
        for k, v in pairs(t) do
            o[tostring(k)] = (type(v) == "table") and "{table}" or tostring(v)
        end
    end
    return o
end

local function getStats()
    local me = getMe()
    if not me then return nil end
    local money = tonumber(me.money) or 0

    local petCount, eggCount = 0, 0
    local byType, eggsByType = {}, {}
    local sample
    local pets = me.inventory and me.inventory.pets
    if type(pets) == "table" then
        for _, item in pairs(pets) do
            if type(item) == "table" then
                local props = item.properties or {}
                local kind  = tostring(item.kind or item.id or "?")   -- kind = pet TYPE (groups correctly)
                if kind:match("egg$") or item.category == "egg" then
                    eggCount = eggCount + 1
                    eggsByType[kind] = (eggsByType[kind] or 0) + 1
                else
                    petCount = petCount + 1
                    if not sample then
                        sample = { top = shallow(item), properties = shallow(props) }  -- one raw pet, for calibration
                    end
                    local age  = tonumber(props.age) or 0
                    local neon = props.neon == true
                    local mega = props.mega == true
                    -- fold neon/mega into the key so /pets shows them grouped separately
                    local key = kind
                    if neon then key = key .. " (neon)" end
                    if mega then key = key .. " (mega)" end
                    local t = byType[key]
                    if not t then
                        t = { count = 0, max_age = 0, kind = kind, neon = neon, mega = mega }
                        byType[key] = t
                    end
                    t.count = t.count + 1
                    if age > t.max_age then t.max_age = age end
                end
            end
        end
    end
    return money, { count = petCount, eggs = eggCount, by_type = byType, eggs_by_type = eggsByType, sample = sample }
end

-- ── Dump to file ───────────────────────────────────────────────────
local function dump()
    local money, pets = getStats()
    if not money or not writefile then return end
    pcall(function()
        if makefolder then makefolder("inv") end
        writefile("inv/" .. LP.Name .. ".json", HttpService:JSONEncode({
            player = LP.Name,
            money  = money,
            stats  = { bucks = money, petCount = pets.count, eggCount = pets.eggs },
            pets   = pets,
        }))
        -- one-time: dump a raw pet's fields so we can calibrate neon/age/rarity
        if pets.sample then
            writefile("inv/_sample.json", HttpService:JSONEncode(pets.sample))
        end
    end)
    print(string.format("[monitor] %s | bucks:%d pets:%d eggs:%d", LP.Name, money, pets.count, pets.eggs))
end

dump()
while true do
    task.wait(INTERVAL)
    dump()
end
