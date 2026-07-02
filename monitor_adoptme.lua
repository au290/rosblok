-- monitor_adoptme.lua — Adopt Me inventory dump (file-only, no server)
-- Place in your executor autoexec. Every INTERVAL seconds it writes
-- inv/<player>.json into the executor workspace; the Discord bot's /inv reads it.

local RS          = game:GetService("ReplicatedStorage")
local HttpService = game:GetService("HttpService")
local Players     = game:GetService("Players")

repeat task.wait() until game:IsLoaded()
task.wait(3)

local INTERVAL = 30
local FG_AGE   = 5                       -- ages 0..5 (Newborn..Full Grown)
local LP       = Players.LocalPlayer

-- ── Module loader (Fsys.load, or require from RS.ClientDB / ClientModules) ──
local Fsys
pcall(function() Fsys = require(RS.Fsys) end)

local function loadMod(name)
    if Fsys and Fsys.load then
        local ok, m = pcall(function() return Fsys.load(name) end)
        if ok and m ~= nil then return m end
    end
    local cdb = RS:FindFirstChild("ClientDB")
    if cdb and cdb:FindFirstChild(name) then
        local ok, m = pcall(function() return require(cdb[name]) end)
        if ok then return m end
    end
    return nil
end

-- ── ClientData (bucks + inventory) ──
local ClientData = loadMod("ClientData")
if not ClientData then
    local ok, m = pcall(function() return require(RS.ClientModules.Core.ClientData) end)
    if ok then ClientData = m end
end
if not ClientData or type(ClientData.get_data) ~= "function" then
    warn("[monitor] could not load Adopt Me ClientData — aborting")
    return
end

-- ── PetAvatarItemDB: pet id -> { name, rarity, ... }  (the master pet database) ──
local IDB = loadMod("PetAvatarItemDB")

local entryCache = {}
local function itemEntry(kind)
    local c = entryCache[kind]
    if c ~= nil then return c or nil end
    local e
    if type(IDB) == "table" then
        if type(IDB.items_by_kind) == "table" then e = IDB.items_by_kind[kind] end          -- direct (safe) first
        if e == nil and type(IDB.items) == "table" then e = IDB.items[kind] end
        if e == nil and type(IDB.get_entry_by_id) == "function" then
            local ok, r = pcall(IDB.get_entry_by_id, kind); if ok then e = r end
        end
    end
    entryCache[kind] = e or false
    return e
end

local function pick(v)                     -- name/rarity may be a string or {name=..}/{id=..}
    if type(v) == "string" then return v end
    if type(v) == "table" then return v.name or v.id or v.display_name end
    return nil
end
local function rarityOf(kind)
    local e = itemEntry(kind)
    if type(e) == "table" then return pick(e.rarity or e.Rarity or e.rarity_name or e.pet_rarity) end
end
local function nameOf(kind)
    local e = itemEntry(kind)
    if type(e) == "table" then return pick(e.name or e.display_name or e.displayName or e.title) end
end

-- shallow, JSON-safe copy (one level) — for the one-time calibration sample
local function shallow(t)
    local o = {}
    if type(t) == "table" then
        for k, v in pairs(t) do
            o[tostring(k)] = (type(v) == "table") and "{table}" or tostring(v)
        end
    end
    return o
end

-- ── Read stats ──
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
    local sample
    local pets = me.inventory and me.inventory.pets
    if type(pets) == "table" then
        for _, item in pairs(pets) do
            if type(item) == "table" then
                local props = item.properties or {}
                local kind  = tostring(item.kind or item.id or "?")
                local cat   = tostring(item.category or ""):lower()
                -- unhatched egg: kind ends in "egg" (cracked_egg…); hatched pets end in the pet name
                if kind:match("egg$") or cat == "egg" or cat == "eggs" then
                    eggCount = eggCount + 1
                    eggsByType[kind] = (eggsByType[kind] or 0) + 1
                else
                    petCount = petCount + 1
                    if not sample then     -- dump one pet + its DB entry so we can confirm fields
                        sample = {
                            top          = shallow(item),
                            properties   = shallow(props),
                            kind         = kind,
                            itemdb_entry = shallow(itemEntry(kind)),
                            resolved     = { name = nameOf(kind), rarity = rarityOf(kind) },
                        }
                    end
                    local age  = tonumber(props.age) or 0
                    local neon = props.neon == true
                    local mega = props.mega_neon == true
                    local key  = kind
                    if mega then key = key .. " (mega neon)" elseif neon then key = key .. " (neon)" end
                    local t = byType[key]
                    if not t then
                        t = { count = 0, fg = 0, kind = kind, neon = neon, mega = mega,
                              rarity = rarityOf(kind), name = nameOf(kind) }
                        byType[key] = t
                    end
                    t.count = t.count + 1
                    if age >= FG_AGE then t.fg = t.fg + 1 end   -- full grown
                end
            end
        end
    end
    return money, { count = petCount, eggs = eggCount, by_type = byType, eggs_by_type = eggsByType, sample = sample }
end

-- ── Dump to file ──
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
