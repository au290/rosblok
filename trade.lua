getgenv().VO_CONFIG = {
    -- === HUB / AUTH ===
    HubKey = "nvFmDrY0DNbAbE0069ETSuiMplvK2P4wsg-Ux1UhQok",
    DeviceName = "Cluster-1",

    -- === MAIN FARM (choose one mode) ===
    PotFarm = false,
    EggFarm = false,
    PetFarm = true, -- Enabled because PetFarm.FarmEggs was false in your old config
    KeepEggFarm = false,
    KeepPetFarm = true, -- Added to keep looping your specific event pets
    EggName = {"cracked_egg"}, -- Ported from PetFarm.EggTypes
    PetFarmList = {
        "River Otter",
        "Ruddy Duck",
    }, -- Ported from PetFarm.SelectedPetTypes
    PrioritizePet = "River Otter", -- Set to the highest priority pet from your list

    -- === EVENT ===
    AutoBeeGame = true, -- [DOESNT RENDER MAIN MAP] Complete storm challenge + buy all Storm Condors

    -- === PET PEN ===
    PetPen = true,
    CustomPenEggs = {},
    CustomPenPets = {"Purrowl", "Sunflower Friend","Violet Friend"}, -- Pet names to keep in pen (empty = all)
    PrioritizePetPenTypes = {"Neon"},  -- "Egg", "Normal", "Neon" (empty = all)

    -- === PET RELEASER ===
    PetReleaser = false,
    ReleasePets = {},       -- Whitelist: names to release (empty = all)
    ExcludeReleasePets = {}, -- Blacklist: base names or prefixed like ReleasePets ("Neon Dog", "Normal Cat", "Mega FG X")
    ReleaseTypes = {},      -- "Mega", "Neon", "Normal" (empty = all)
    ReleaseRarities = {},   -- If ReleasePets non-empty: only used for pets NOT named in ReleasePets. If ReleasePets empty: filters all candidates.
    ExcludeRarities = {},   -- Blacklist rarities (pets on ReleasePets by name bypass this)

    -- === AGE PETS ===
    AgePets = true,
    AgePetsNames = {"River Otter"}, -- Pet names to age (empty = all)
    AgePetsTypes = {"Normal"},  -- "Normal", "Neon", "ALL"

    -- === AUTO FUSE ===
    AutoFuse = true,
    AutoFuseBlacklist = {}, -- Pet names to never include in neon/mega fusion

    -- === BUY PETS ===
    BuyPets = false,
    BuyPetName = {"Pet Name", "Pet Name 2"},  -- Loops in order, buys all of first pet then moves to next

    -- === BOXES ===
    BuyBoxes = false,
    BoxName = "Box Name",   -- Name of the box to buy/open
    OpenBoxes = false,

    -- === LURE ===
    BaitName = "Bait Name",

    -- === AUTO TRADE ===
    AutoTrade = true,
    ReceiverUsernames = {
        "Riley2WinCCruz04",
        "Off1cialzkyeKnox11",
        "SuNnYsPeEd10",
        "CHEE22TAhshiNYYSkYE",
        "MADDI3cruzsim",
        "LUC4SPHOENNix18",
        "viTheMaddieBriar1118",
        "luC257AsBrIAR",
        "rAvEnZoNe29",
        "officialEchoCreatez",
    },
    TradeItemList = {
         pets = {
            "Neon FG River Otter",
            "Neon FG Ruddy Duck",
            "Sushi Penguin",
            "Dragonfruit Fox",
            "Dango Penguins",
            "Velocirooster",
            "Silverback Gorilla",
            "Frostbite Bear",
            "Neon FG Purrowl",
            "Neon FG Ancient Dragon",
            "Neon FG Stygian Owl",
            "FG General Sheepdog",
            "Neon FG Chestnut Glyptodon",
            "Mega River Otter",
            "Mega Ruddy Duck",
            "Mega Purrowl",
            "Mega Ancient Dragon",
            "Mega Stygian Owl",
            "Mega Chestnut Glyptodon",
            "Neon FG Sunflower Friend",
            "Neon FG Violet Friend",
        }, -- Pet names to trade (empty = all)
    }, -- Per category: { pets = {"Dog","Neon Cat"}, food = {}, toys = {}, ... } — use "ALL" in a category to allow that whole category (pets still gated by TradePetType for bare names)
    TradePetType = {"ALL"},       -- Only applies to pets: "ALL", "Mega", "Neon", "Regular", "Neon_FG", "Regular_FG" — not used for food/toys/etc.; inline prefixes on pet strings (e.g. "Mega Dog") bypass this

    -- === CASH TRANSFER ===
    CashTransfer = false,
    TransferMethods = {"mannequin"},  -- Current Methods: "mannequin"
    TransferAccount = "",

    -- === DISCORD WEBHOOK ===
    WebhookEnabled = false,
    WebhookURL = "",
    WebhookPets = {},  -- Pet names to send (empty = all)

    ExtraOpti = false
}
loadstring(game:HttpGet("https://raw.githubusercontent.com/voltrex2/VoHub/refs/heads/main/FARM"))()
