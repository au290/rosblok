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

-- ── Pet rarity source (module name varies by build; discover once, cache) ──
local raritySrc = {}   -- name -> module table that may map pet kind -> rarity
do
    local Fsys
    pcall(function() Fsys = require(RS.Fsys) end)
    local function keep(mod) return type(mod) == "table" or type(mod) == "function" end
    if Fsys and Fsys.load then
        for _, n in ipairs({ "PetConstants", "Pets", "PetData", "PetInfo", "PetTextInfo",
                             "PetRegistry", "PetProducts", "PetDisplayInfo", "PetAvatarItemDB" }) do
            local ok, mod = pcall(function() return Fsys.load(n) end)
            if ok and keep(mod) then raritySrc[n] = mod end
        end
    end
    -- also scan the tree for pet-ish ModuleScripts (covers builds with other names)
    for _, d in ipairs(RS:GetDescendants()) do
        if d:IsA("ModuleScript") and d.Name:lower():match("pet") and not raritySrc[d.Name] then
            local ok, mod = pcall(require, d)
            if ok and keep(mod) then raritySrc[d.Name] = mod end
        end
    end
end

-- PetAvatarItemDB is the master DB: pet id -> { name, rarity, ... }. Resolve an entry
-- via its accessors (get_entry_by_id / items_by_kind / items), not top-level indexing.
local IDB = raritySrc["PetAvatarItemDB"]
local entryCache = {}
local function itemEntry(kind)
    local c = entryCache[kind]
    if c ~= nil then return c or nil end
    local e
    if type(IDB) == "table" then
        if type(IDB.get_entry_by_id) == "function" then
            local ok, r = pcall(IDB.get_entry_by_id, kind); if ok and r ~= nil then e = r end
            if e == nil then local ok2, r2 = pcall(function() return IDB:get_entry_by_id(kind) end); if ok2 and r2 ~= nil then e = r2 end end
        end
        if e == nil and type(IDB.items_by_kind) == "table" then e = IDB.items_by_kind[kind] end
        if e == nil and type(IDB.items) == "table" then e = IDB.items[kind] end
    end
    entryCache[kind] = e or false
    return e
end

local function pickStr(v)                 -- rarity/name may be nested {name=..}/{id=..}
    if type(v) == "string" then return v end
    if type(v) == "table" then return v.name or v.id or v.display_name or v.rarity end
    return nil
end
local function rarityOf(kind)
    local e = itemEntry(kind)
    if type(e) == "table" then
        local r = pickStr(e.rarity or e.Rarity or e.rarityName or e.rarity_name or e.pet_rarity)
        if r then return tostring(r) end
    end
    return nil
end
local function nameOf(kind)
    local e = itemEntry(kind)
    if type(e) == "table" then
        local nm = pickStr(e.name or e.display_name or e.displayName or e.title)
        if nm then return tostring(nm) end
    end
    return nil
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

local FG_AGE = 5   -- Adopt Me ages 0..5 (Newborn,Junior,Pre-Teen,Teen,Post-Teen,Full Grown)

local function keylist(set)
    local a = {}
    for k in pairs(set) do a[#a + 1] = k end
    table.sort(a)
    return a
end

local function firstkeys(t, n)   -- up to n top-level keys of a table (for the rarity probe)
    local a = {}
    if type(t) == "table" then
        for k in pairs(t) do a[#a + 1] = tostring(k); if #a >= n then break end end
    end
    return a
end

local function dumpval(v)        -- table -> shallow copy; anything else -> string (so a bare name/rarity shows)
    if type(v) == "table" then return shallow(v) end
    return tostring(v)
end

local function getStats()
    local me = getMe()
    if not me then return nil end
    local money = tonumber(me.money) or 0

    local petCount, eggCount = 0, 0
    local byType, eggsByType = {}, {}
    local sample, topKeys, propKeys = nil, {}, {}
    local pets = me.inventory and me.inventory.pets
    if type(pets) == "table" then
        for _, item in pairs(pets) do
            if type(item) == "table" then
                local props = item.properties or {}
                local kind  = tostring(item.kind or item.id or "?")   -- kind = pet TYPE (groups correctly)
                for k in pairs(item)  do topKeys[tostring(k)]  = true end   -- collect the field vocabulary
                for k in pairs(props) do propKeys[tostring(k)] = true end   -- (reveals a neon field if any pet has one)
                local cat = tostring(item.category or ""):lower()
                -- unhatched egg: kind ends in "egg" (cracked_egg, royal_egg…); hatched pets
                -- end in the pet name (basic_egg_2022_alicorn) so they DON'T match
                if kind:match("egg$") or cat == "egg" or cat == "eggs" then
                    eggCount = eggCount + 1
                    eggsByType[kind] = (eggsByType[kind] or 0) + 1
                else
                    petCount = petCount + 1
                    if not sample then
                        local stripped = kind:gsub("^.-%d%d%d%d_", "")   -- drop egg/event + year
                        sample = { top = shallow(item), properties = shallow(props),
                                   kind = kind, stripped = stripped }
                        -- deep-probe promising modules: dump shape + lookups by full AND bare name
                        local DEEP = { Pets = 1, PetDisplayInfo = 1, PetAvatarItemDB = 1,
                                       PetAvatarCategoriesDB = 1, PetProducts = 1, PetAppearance = 1,
                                       PetName = 1, PetColorHelper = 1 }
                        local dp = {}
                        for n, mod in pairs(raritySrc) do
                            if DEEP[n] then
                                local d = { type = type(mod) }
                                if type(mod) == "table" then
                                    local cnt = 0
                                    for _ in pairs(mod) do cnt = cnt + 1 end
                                    d.key_count   = cnt
                                    d.sample_keys = firstkeys(mod, 20)
                                    if mod[kind]     ~= nil then d.by_kind     = dumpval(mod[kind])     end
                                    if mod[stripped] ~= nil then d.by_stripped = dumpval(mod[stripped]) end
                                else
                                    local ok1, r1 = pcall(mod, kind)
                                    local ok2, r2 = pcall(mod, stripped)
                                    d.call_kind     = ok1 and dumpval(r1) or ("err: " .. tostring(r1))
                                    d.call_stripped = ok2 and dumpval(r2) or ("err: " .. tostring(r2))
                                end
                                dp[n] = d
                            end
                        end
                        sample.rarity_probe = dp
                        -- the master entry (name + rarity live here) + PetDisplayInfo.get result
                        sample.itemdb_entry    = dumpval(itemEntry(kind))
                        sample.itemdb_stripped = dumpval(itemEntry(stripped))
                        local pdi = raritySrc["PetDisplayInfo"]
                        if type(pdi) == "table" and type(pdi.get) == "function" then
                            local ok, r = pcall(pdi.get, kind)
                            if not (ok and r ~= nil) then ok, r = pcall(function() return pdi:get(kind) end) end
                            sample.displayinfo = ok and dumpval(r) or ("err: " .. tostring(r))
                        end
                        sample.resolved = { name = nameOf(kind), rarity = rarityOf(kind) }
                    end
                    local age  = tonumber(props.age) or 0
                    local neon = props.neon == true
                    local mega = props.mega_neon == true
                    -- fold neon/mega into the key so /pets shows them grouped separately
                    local key = kind
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
    if sample then
        sample.top_keys        = keylist(topKeys)     -- so we can spot neon/rarity fields next dump
        sample.prop_keys       = keylist(propKeys)
        sample.rarity_modules  = keylist(raritySrc)   -- which pet modules we actually found
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
