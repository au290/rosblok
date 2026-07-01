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

local function getStats()
    local me = getMe()
    if not me then return nil end
    local money = tonumber(me.money) or 0

    local petCount, eggCount = 0, 0
    local byType, eggsByType = {}, {}
    local pets = me.inventory and me.inventory.pets
    if type(pets) == "table" then
        for _, item in pairs(pets) do
            if type(item) == "table" then
                local id = tostring(item.id or item.kind or "?")
                if id:match("egg$") then
                    eggCount = eggCount + 1
                    eggsByType[id] = (eggsByType[id] or 0) + 1
                else
                    petCount = petCount + 1
                    local age = tonumber((item.properties or {}).age) or 0
                    local t = byType[id]
                    if not t then t = { count = 0, max_age = 0 }; byType[id] = t end
                    t.count = t.count + 1
                    if age > t.max_age then t.max_age = age end
                end
            end
        end
    end
    return money, { count = petCount, eggs = eggCount, by_type = byType, eggs_by_type = eggsByType }
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
    end)
    print(string.format("[monitor] %s | bucks:%d pets:%d eggs:%d", LP.Name, money, pets.count, pets.eggs))
end

dump()
while true do
    task.wait(INTERVAL)
    dump()
end
