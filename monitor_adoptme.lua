-- monitor_adoptme.lua - Adopt Me inventory reporter (direct web POST)
-- Place in your executor autoexec. Every INTERVAL seconds it sends this
-- account's inventory directly to the Hopper Fleet web server.
--
-- Set VPS_URL and KEY before installing. The shared key is visible in this
-- client script by design; use HTTPS when the server is not local.

local RS          = game:GetService("ReplicatedStorage")
local HttpService = game:GetService("HttpService")
local Players     = game:GetService("Players")

repeat task.wait() until game:IsLoaded()
task.wait(3)

local VPS_URL  = "http://agent.kqing.web.id" -- no trailing slash
local KEY      = "CHANGE_ME_SHARED_SECRET" -- must match web/config.txt
local INTERVAL = 30
local FG_AGE   = 5                         -- ages 0..5 (Newborn..Full Grown)
local LP       = Players.LocalPlayer

VPS_URL = VPS_URL:gsub("/+$", "")

local function request_function()
    return (syn and syn.request) or (http and http.request) or http_request or request
end

local function post_report(body)
    local url = VPS_URL .. "/api/monitor/poll"
    local headers = {
        ["Content-Type"] = "application/json",
        ["X-Key"] = KEY,
        ["User-Agent"] = "adoptme-monitor",
    }
    local request_fn = request_function()

    if request_fn then
        local ok, response = pcall(function()
            return request_fn({ Url = url, Method = "POST", Headers = headers, Body = body })
        end)
        if not ok then return false, tostring(response) end
        local status = response and tonumber(response.StatusCode or response.Status)
        if status and (status < 200 or status >= 300) then
            return false, "HTTP " .. tostring(status)
        end
        return true
    end

    -- Fallback for executors that expose HttpService but no request helper.
    local ok, response = pcall(function()
        return HttpService:RequestAsync({ Url = url, Method = "POST", Headers = headers, Body = body })
    end)
    if not ok then return false, tostring(response) end
    if response and response.Success == false then
        return false, "HTTP " .. tostring(response.StatusCode or "request failed")
    end
    return true
end

-- ClientData (bucks + inventory)
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
    warn("[monitor] could not load Adopt Me ClientData - aborting")
    return
end

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
                local props = item.properties or {}
                local kind  = tostring(item.kind or item.id or "?")
                local cat   = tostring(item.category or ""):lower()
                -- Unhatched eggs are tracked separately from hatched pets.
                if kind:match("egg$") or cat == "egg" or cat == "eggs" then
                    eggCount = eggCount + 1
                    eggsByType[kind] = (eggsByType[kind] or 0) + 1
                else
                    petCount = petCount + 1
                    local age  = tonumber(props.age) or 0
                    local neon = props.neon == true
                    local mega = props.mega_neon == true
                    local key  = kind
                    if mega then key = key .. " (mega neon)" elseif neon then key = key .. " (neon)" end
                    local t = byType[key]
                    if not t then
                        t = { count = 0, fg = 0, kind = kind, neon = neon, mega = mega }
                        byType[key] = t
                    end
                    t.count = t.count + 1
                    if age >= FG_AGE then t.fg = t.fg + 1 end
                end
            end
        end
    end
    return money, { count = petCount, eggs = eggCount, by_type = byType, eggs_by_type = eggsByType }
end

local function report()
    local money, pets = getStats()
    if not money then
        warn("[monitor] Adopt Me data is not ready")
        return
    end

    local account = {
        player = LP.Name,
        money  = money,
        stats  = { bucks = money, petCount = pets.count, eggCount = pets.eggs },
        pets   = pets,
    }
    local payload = HttpService:JSONEncode({
        source = "monitor_adoptme",
        inv = { [LP.Name] = account },
    })
    local ok, err = post_report(payload)
    if ok then
        print(string.format("[monitor] %s | bucks:%d pets:%d eggs:%d | sent", LP.Name, money, pets.count, pets.eggs))
    else
        warn("[monitor] report failed: " .. tostring(err))
    end
end

report()
while true do
    task.wait(INTERVAL)
    report()
end
