-- debug_adoptme_pets.lua
-- Read-only inventory dump for finding Adopt Me's real pet display names.
-- This script never sends data to the VPS and never reads cookies.

local RS = game:GetService("ReplicatedStorage")
local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")

repeat task.wait() until game:IsLoaded()
task.wait(3)

local LP = Players.LocalPlayer
local ClientData

do
    local okF, Fsys = pcall(function() return require(RS.Fsys) end)
    if okF and Fsys and Fsys.load then
        local okL, mod = pcall(function() return Fsys.load("ClientData") end)
        if okL then ClientData = mod end
    end
    if not ClientData then
        local okD, mod = pcall(function()
            return require(RS.ClientModules.Core.ClientData)
        end)
        if okD then ClientData = mod end
    end
end

if not ClientData or type(ClientData.get_data) ~= "function" then
    warn("[pet-debug] ClientData is not ready")
    return
end

local function firstString(value, keys)
    if type(value) ~= "table" then return nil end
    for _, key in ipairs(keys) do
        local candidate = value[key]
        if type(candidate) == "string" and candidate ~= "" then
            return candidate
        end
    end
    return nil
end

local function shallowFields(value)
    if type(value) ~= "table" then return "" end
    local fields = {}
    for _, key in ipairs({ "name", "display_name", "displayName", "localized_name", "label", "title" }) do
        local text = value[key]
        if type(text) == "string" and text ~= "" then
            fields[#fields + 1] = key .. "=" .. text
        end
    end
    return table.concat(fields, ", ")
end

local function displayNameFromKind(kind)
    local name = tostring(kind or "?")
    -- Event/version prefixes are part of the internal ID, not the display name.
    name = name:gsub("^.-%d%d%d%d_", "")
    name = name:gsub("_+", " ")
    name = name:gsub("(%a)([%w']*)", function(first, rest)
        return string.upper(first) .. string.lower(rest)
    end)
    return name
end

local function findDefinition(root, wanted, depth, seen)
    if type(root) ~= "table" or depth > 5 then return nil end
    seen = seen or {}
    if seen[root] then return nil end
    seen[root] = true

    for key, value in pairs(root) do
        if tostring(key) == wanted and type(value) == "table" then
            local name = firstString(value, { "name", "display_name", "displayName", "localized_name", "label", "title" })
            if name then return name end
        end
        if type(value) == "table" then
            local found = findDefinition(value, wanted, depth + 1, seen)
            if found then return found end
        end
    end
    return nil
end

local function loadPetDefinition(kind)
    local moduleNames = {
        Pets = true,
        PetData = true,
        PetDefinitions = true,
        PetDatabase = true,
    }
    local roots = { RS, RS:FindFirstChild("ClientModules") }
    for _, root in ipairs(roots) do
        if root then
            for _, descendant in ipairs(root:GetDescendants()) do
                if descendant:IsA("ModuleScript") and moduleNames[descendant.Name] then
                    local ok, data = pcall(require, descendant)
                    if ok then
                        local found = findDefinition(data, kind, 0)
                        if found then return found, descendant:GetFullName() end
                    end
                end
            end
        end
    end
    return nil
end

local ok, all = pcall(function() return ClientData.get_data() end)
local account = ok and type(all) == "table" and all[LP.Name] or nil
local pets = account and account.inventory and account.inventory.pets
if type(pets) ~= "table" then
    warn("[pet-debug] inventory.pets is not available yet")
    return
end

local rows, seenKinds = {}, {}
for _, item in pairs(pets) do
    if type(item) == "table" then
        local kind = tostring(item.kind or item.id or "?")
        if not seenKinds[kind] then
            seenKinds[kind] = true
            local direct = firstString(item, { "name", "display_name", "displayName", "localized_name", "label", "title" })
            local props = type(item.properties) == "table" and item.properties or {}
            local propertyName = firstString(props, { "name", "display_name", "displayName", "localized_name", "label", "title" })
            local definition, modulePath = loadPetDefinition(kind)
            local displayName = direct or propertyName or definition or displayNameFromKind(kind)
            local row = {
                kind = kind,
                id = item.id,
                display_name = displayName,
                direct_name = direct,
                property_name = propertyName,
                definition_name = definition,
                definition_module = modulePath,
                raw = item,
            }
            rows[#rows + 1] = row
            print(string.format(
                "[pet-debug] %s | kind=%s | direct=%s | property=%s | definition=%s",
                displayName,
                kind,
                direct or "-",
                propertyName or "-",
                definition or "-"
            ))
            local fields = shallowFields(item)
            if fields ~= "" then print("  fields: " .. fields) end
            if modulePath then print("  source: " .. modulePath) end
        end
    end
end

local output = HttpService:JSONEncode({
    account = LP.Name,
    generated_at = os.time(),
    pets = rows,
})

if writefile then
    pcall(function() writefile("adoptme_pet_debug.json", output) end)
    print("[pet-debug] wrote adoptme_pet_debug.json")
end
if setclipboard then
    pcall(function() setclipboard(output) end)
    print("[pet-debug] copied JSON dump to clipboard")
end
print(string.format("[pet-debug] found %d unique pet kinds", #rows))
