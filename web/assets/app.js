(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const loginView = $("login-view");
  const appView = $("app-view");
  const pageMeta = {
    summary: { kicker: "EXECUTIVE SUMMARY", title: "Fleet at a glance", subtitle: "A live view of value, hopper capacity, and pet inventory." },
    hoppers: { kicker: "OPERATE", title: "Hopper control", subtitle: "Run each hopper independently across every connected phone." },
    pets: { kicker: "INVENTORY", title: "Pet trackstat", subtitle: "Track count, growth, rarity, and estimated value for every pet." },
  };
  let refreshTimer = null;
  let busy = false;
  let currentPage = "summary";
  let currentHoppers = [];
  let rotationHopperId = null;
  const selectedHoppers = new Set();

  function escapeHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    let data = {};
    try { data = await response.json(); } catch (_) { /* keep empty response */ }
    if (response.status === 401) { showLogin(); throw new Error(data.error || "Authentication required"); }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  function showLogin(message = "") {
    loginView.hidden = false;
    appView.hidden = true;
    $("login-error").textContent = message;
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  }

  function showApp() {
    loginView.hidden = true;
    appView.hidden = false;
    setPage(currentPage);
    if (!refreshTimer) refreshTimer = setInterval(refresh, 3000);
  }

  function setPage(page) {
    if (!pageMeta[page]) page = "summary";
    currentPage = page;
    document.querySelectorAll("[data-page-view]").forEach((view) => { view.hidden = view.dataset.pageView !== page; });
    document.querySelectorAll("[data-page]").forEach((button) => { button.classList.toggle("active", button.dataset.page === page); });
    const meta = pageMeta[page];
    $("page-kicker").textContent = meta.kicker;
    $("page-title").textContent = meta.title;
    $("page-subtitle").textContent = meta.subtitle;
    closeSidebar();
  }

  function openSidebar() { appView.classList.add("sidebar-open"); $("sidebar-backdrop").hidden = false; }
  function closeSidebar() { appView.classList.remove("sidebar-open"); $("sidebar-backdrop").hidden = true; }
  function commandPhone() { return $("command-phone").value || "all"; }
  function number(id) { return Number.parseInt($(id).value, 10); }
  function money(value) { return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value || 0)); }
  function integer(value) { return new Intl.NumberFormat().format(Number(value || 0)); }
  function statusClass(rarity) { const value = String(rarity || "").toLowerCase(); return ["legendary", "ultra", "rare", "uncommon"].find((item) => value.includes(item)) || ""; }

  function renderPhoneOptions(phones) {
    const currentCommand = $("command-phone").value || "all";
    const options = phones.map((phone) => `<option value="${escapeHtml(phone)}">Phone ${escapeHtml(phone)}</option>`).join("");
    $("command-phone").innerHTML = `<option value="all">All phones</option>${options}`;
    $("command-phone").value = ["all", ...phones].includes(currentCommand) ? currentCommand : "all";
    const hopperFilter = $("hopper-phone-filter");
    const currentHopperFilter = hopperFilter.value || "all";
    hopperFilter.innerHTML = `<option value="all">All phones</option>${options}`;
    hopperFilter.value = ["all", ...phones].includes(currentHopperFilter) ? currentHopperFilter : "all";
  }

  function renderPhones(phones, targetId = "phone-cards") {
    const target = $(targetId);
    if (!target) return;
    target.innerHTML = phones.map((phone) => {
      const board = phone.board || "No report yet.";
      const age = phone.last_seen_seconds == null ? "never" : `${phone.last_seen_seconds}s ago`;
      return `<article class="phone-card ${phone.online ? "" : "offline"}"><div class="phone-card-head"><h3>Phone ${escapeHtml(phone.phone)}</h3><span class="status ${phone.online ? "online" : ""}">${phone.online ? "Online" : "Offline"}</span></div><p class="phone-footer">${escapeHtml(phone.footer || "No device health report")} &middot; last seen ${escapeHtml(age)}</p><pre class="phone-board">${escapeHtml(board)}</pre></article>`;
    }).join("") || `<p class="empty">No phones configured.</p>`;
  }

  function hopperSearchMatch(hopper) {
    const query = $("hopper-search").value.trim().toLowerCase();
    const phone = $("hopper-phone-filter").value;
    const device = $("hopper-device-filter").value;
    if (phone !== "all" && hopper.phone !== phone) return false;
    if (device !== "all" && hopper.device !== device) return false;
    if (!query) return true;
    const tradeItems = ((hopper.trade || {}).items || []).map((item) => item.name).join(" ");
    return [hopper.device, hopper.account, hopper.package, hopper.target, hopper.phone, tradeItems, `hopper ${hopper.hopper}`].some((value) => String(value || "").toLowerCase().includes(query));
  }

  function renderHopperDeviceOptions(hoppers) {
    const select = $("hopper-device-filter");
    const current = select.value || "all";
    const devices = [...new Set(hoppers.map((hopper) => hopper.device))].sort();
    select.innerHTML = `<option value="all">All devices</option>${devices.map((device) => `<option value="${escapeHtml(device)}">${escapeHtml(device)}</option>`).join("")}`;
    select.value = ["all", ...devices].includes(current) ? current : "all";
  }

  function renderHoppers(hoppers) {
    currentHoppers = hoppers || [];
    renderHopperDeviceOptions(currentHoppers);
    const visible = currentHoppers.filter(hopperSearchMatch);
    [...selectedHoppers].forEach((id) => { if (!currentHoppers.some((hopper) => hopper.id === id)) selectedHoppers.delete(id); });
    $("hopper-count").textContent = `${integer(currentHoppers.length)} hopper${currentHoppers.length === 1 ? "" : "s"}`;
    $("hopper-selection-count").textContent = `${integer(selectedHoppers.size)} selected`;
    const selectAll = $("hopper-select-all");
    selectAll.checked = visible.length > 0 && visible.every((hopper) => selectedHoppers.has(hopper.id));
    selectAll.indeterminate = visible.some((hopper) => selectedHoppers.has(hopper.id)) && !selectAll.checked;
    if (!visible.length) { $("hopper-body").innerHTML = `<tr><td colspan="8" class="empty">No matching hoppers</td></tr>`; return; }
    $("hopper-body").innerHTML = visible.map((hopper) => {
      const fraction = hopper.total > 0 ? Math.min(100, Math.round((hopper.elapsed / hopper.total) * 100)) : 0;
      const status = hopper.status === "held" ? "Paused" : hopper.status[0].toUpperCase() + hopper.status.slice(1);
      const progress = hopper.total > 0 ? `${integer(hopper.elapsed)}s / ${integer(hopper.total)}s` : "-";
      const trade = hopper.trade || {};
      const tradeStatus = String(trade.status || "no script");
      const stale = tradeStatus !== "stopped" && !trade.fresh && tradeStatus !== "no script";
      const tradeLabel = tradeStatus === "no script"
        ? (Number(trade.grace || 0) > 0 ? `Starting / ${integer(trade.grace)}s grace` : "Running / no script")
        : stale
          ? `Stale / ${integer(trade.age)}s`
          : tradeStatus === "disconnected" || tradeStatus === "error"
            ? "Rejoining"
            : tradeStatus[0].toUpperCase() + tradeStatus.slice(1);
      const tradeLabelHtml = escapeHtml(tradeLabel).replaceAll(" / ", " &middot; ");
      const tradeClass = stale || ["error", "disconnected", "no script"].includes(tradeStatus) ? "trade-warning" : tradeStatus === "completed" ? "trade-complete" : "trade-live";
      const items = Array.isArray(trade.items) ? trade.items : [];
      const itemText = items.slice(0, 2).map((item) => `${item.qty ?? 0}x ${item.name}`).join(", ");
      const itemTitle = items.map((item) => `${item.qty ?? 0}x ${item.name}`).join(", ");
      const meta = trade.meta || {};
      const categories = meta.categories && typeof meta.categories === "object" ? Object.entries(meta.categories).slice(0, 3).map(([name, count]) => `${name}: ${integer(count)}`).join(", ") : "";
      const details = [trade.count != null ? `${integer(trade.count)} trades` : "", itemText, meta.players ? `${meta.players} players` : "", meta.runtime != null ? `${integer(meta.runtime)}s runtime` : "", categories].filter(Boolean).join(" / ") || "Waiting for heartbeat";
      return `<tr><td class="check-column"><input class="hopper-check" data-hopper-id="${escapeHtml(hopper.id)}" type="checkbox" ${selectedHoppers.has(hopper.id) ? "checked" : ""} aria-label="Select hopper ${escapeHtml(hopper.hopper)}"></td><td class="device-cell"><span class="device-name ${hopper.online ? "online" : ""}">${escapeHtml(hopper.device)}</span><span class="cell-secondary">${escapeHtml(hopper.phone)} &middot; hopper ${escapeHtml(hopper.hopper)}</span></td><td class="account-cell"><span class="cell-primary">${escapeHtml(hopper.account)}</span><span class="cell-secondary">${escapeHtml(hopper.package)}</span></td><td class="target-cell"><span class="cell-primary">${escapeHtml(hopper.target)}</span><span class="cell-secondary">${escapeHtml(hopper.server ? "private server" : "rotation")}</span></td><td class="progress-cell"><span class="progress-label">${escapeHtml(progress)}</span><div class="progress-track"><span style="width:${fraction}%"></span></div></td><td class="trade-cell" title="${escapeHtml(itemTitle)}"><span class="cell-primary trade-state ${tradeClass}">${tradeLabelHtml}</span><span class="cell-secondary">${escapeHtml(details)}</span></td><td><span class="status-label ${escapeHtml(hopper.status)}">${escapeHtml(status)}</span></td><td><div class="row-actions"><button class="configure" data-hopper-action="rotation" data-hopper-id="${escapeHtml(hopper.id)}" title="Edit rotation" aria-label="Edit rotation">&#9881;</button><button class="play" data-hopper-action="start" data-hopper-id="${escapeHtml(hopper.id)}" title="Start hopper" aria-label="Start hopper">&#9654;</button><button class="stop" data-hopper-action="stop" data-hopper-id="${escapeHtml(hopper.id)}" title="Stop hopper" aria-label="Stop hopper">&#9632;</button><button class="restart" data-hopper-action="restart" data-hopper-id="${escapeHtml(hopper.id)}" title="Restart hopper" aria-label="Restart hopper">&#8635;</button></div></td></tr>`;
    }).join("");
  }

  function renderSavedRotation(hopper) {
    const rotation = hopper && hopper.rotation ? hopper.rotation : {};
    const links = Array.isArray(rotation.links) ? rotation.links : [];
    $("saved-rotation-count").textContent = `${integer(links.length)} link${links.length === 1 ? "" : "s"}`;
    $("saved-rotation-links").innerHTML = links.length
      ? links.map((link, index) => `<li><span>RF${index + 1}</span><code>${escapeHtml(link)}</code></li>`).join("")
      : `<li class="empty">No links reported.</li>`;
  }

  function openRotation(hopper) {
    rotationHopperId = hopper.id;
    const rotation = hopper.rotation || {};
    const links = Array.isArray(rotation.links) ? rotation.links : [];
    $("rotation-title").textContent = `Hopper ${hopper.hopper} rotation`;
    $("rotation-subtitle").textContent = `Phone ${hopper.phone} · ${hopper.device}`;
    $("rotation-links").value = links.join("\n");
    $("rotation-loop").checked = rotation.loop !== false;
    $("rotation-cooldown").value = Number(rotation.cooldown || 240);
    $("rotation-result").textContent = "";
    renderSavedRotation(hopper);
    $("rotation-dialog").showModal();
  }

  function closeRotation() {
    rotationHopperId = null;
    $("rotation-dialog").close();
  }

  async function saveRotation(event) {
    event.preventDefault();
    if (busy || !rotationHopperId) return;
    const hopper = hopperById(rotationHopperId);
    if (!hopper) return;
    const links = $("rotation-links").value.split(/\r?\n/).map((link) => link.trim()).filter(Boolean);
    const payload = {
      phone: hopper.phone,
      action: "rotation_set",
      hopper: hopper.hopper,
      links,
      loop: $("rotation-loop").checked,
      cooldown: number("rotation-cooldown"),
    };
    busy = true;
    $("rotation-save").disabled = true;
    $("rotation-result").textContent = "Saving to device...";
    try {
      const response = await api("/api/command", { method: "POST", body: JSON.stringify(payload) });
      $("rotation-result").textContent = response.result || "Rotation saved.";
      $("command-result").textContent = response.result || "Rotation saved.";
      $("output").textContent = response.result || "Rotation saved.";
      busy = false;
      await refresh();
      const saved = hopperById(rotationHopperId);
      if (saved) {
        const savedRotation = saved.rotation || {};
        $("rotation-links").value = Array.isArray(savedRotation.links) ? savedRotation.links.join("\n") : "";
        $("rotation-loop").checked = savedRotation.loop !== false;
        $("rotation-cooldown").value = Number(savedRotation.cooldown || 240);
        renderSavedRotation(saved);
      }
    } catch (error) {
      $("rotation-result").textContent = error.message;
    } finally {
      busy = false;
      $("rotation-save").disabled = false;
    }
  }

  function renderSummary(summary, hoppers) {
    $("metric-income").textContent = money(summary.value && summary.value.usd);
    $("metric-hoppers").textContent = integer(hoppers.length);
    $("metric-pets").textContent = integer(summary.pets);
    $("metric-online").textContent = `${integer(summary.online)}/${integer(summary.phones)}`;
    $("metric-bucks").textContent = integer(summary.bucks);
    $("metric-eggs").textContent = integer(summary.eggs);
  }

  function renderCharts(pets) {
    const priced = (pets || []).filter((pet) => Number(pet.value_usd) > 0).sort((a, b) => Number(b.value_usd) - Number(a.value_usd)).slice(0, 5);
    const incomeBars = $("income-bars");
    if (!priced.length) incomeBars.innerHTML = `<p class="empty">Waiting for priced inventory</p>`;
    else {
      const max = Math.max(...priced.map((pet) => Number(pet.value_usd)));
      incomeBars.innerHTML = priced.map((pet) => `<div class="bar-row"><span class="bar-label">${escapeHtml(pet.name)}${pet.variant === "default" ? "" : ` (${escapeHtml(pet.variant.replace("_", " "))})`}</span><div class="bar-track"><span style="width:${Math.max(2, Math.round((pet.value_usd / max) * 100))}%"></span></div><span class="bar-value">${money(pet.value_usd)}</span></div>`).join("");
    }
    const volume = (pets || []).slice(0, 7);
    const mix = $("pet-mix-chart");
    $("pet-mix-total").textContent = `${integer((pets || []).reduce((sum, pet) => sum + Number(pet.count || 0), 0))} pets`;
    if (!volume.length) mix.innerHTML = `<p class="empty">Waiting for inventory</p>`;
    else {
      const max = Math.max(...volume.map((pet) => Number(pet.count || 0)), 1);
      mix.innerHTML = volume.map((pet) => `<div class="mix-column"><span style="height:${Math.max(3, Math.round((pet.count / max) * 100))}%"></span><label title="${escapeHtml(pet.name)}">${escapeHtml(pet.name)}</label></div>`).join("");
    }
  }

  function renderInventory(rows) {
    const body = $("inventory-body");
    if (!rows || !rows.length) { body.innerHTML = `<tr><td colspan="4" class="empty">No inventory report yet</td></tr>`; return; }
    body.innerHTML = rows.map((row) => { const stats = row.stats || {}; return `<tr><td>${escapeHtml(row.player || "?")}</td><td>${integer(stats.bucks ?? row.money)}</td><td>${integer(stats.petCount)}</td><td>${integer(stats.eggCount)}</td></tr>`; }).join("");
  }

  function renderPets(pets) {
    const rows = pets || [];
    const total = rows.reduce((sum, pet) => sum + Number(pet.count || 0), 0);
    const fullGrown = rows.reduce((sum, pet) => sum + Number(pet.full_grown || 0), 0);
    const priced = rows.filter((pet) => pet.priced || Number(pet.value_usd) > 0).length;
    const rarityRank = { common: 1, uncommon: 2, rare: 3, ultra: 4, legendary: 5 };
    const rarest = rows.reduce((best, pet) => rarityRank[statusClass(pet.rarity)] > rarityRank[best] ? statusClass(pet.rarity) : best, "common");
    $("pet-total-stat").textContent = `${integer(total)} pets`;
    $("pet-types-stat").textContent = `${integer(rows.length)} types`;
    $("pet-types-card").textContent = integer(rows.length);
    $("pet-full-grown").textContent = integer(fullGrown);
    $("pet-priced-types").textContent = integer(priced);
    $("pet-rare-tier").textContent = rarest === "ultra" ? "Ultra-rare" : rarest[0].toUpperCase() + rarest.slice(1);
    const distribution = $("pet-distribution-bars");
    const top = rows.slice(0, 6);
    if (!top.length) distribution.innerHTML = `<p class="empty">Waiting for inventory</p>`;
    else {
      const max = Math.max(...top.map((pet) => Number(pet.count || 0)), 1);
      distribution.innerHTML = top.map((pet) => `<div class="bar-row"><span class="bar-label">${escapeHtml(pet.name)}${pet.variant === "default" ? "" : ` (${escapeHtml(pet.variant.replace("_", " "))})`}</span><div class="bar-track"><span style="width:${Math.max(2, Math.round((pet.count / max) * 100))}%;background:${statusClass(pet.rarity) === "legendary" ? "var(--warning)" : statusClass(pet.rarity) === "ultra" ? "var(--violet)" : "var(--blue)"}"></span></div><span class="bar-value">${integer(pet.count)}</span></div>`).join("");
    }
    const body = $("pet-track-body");
    if (!rows.length) { body.innerHTML = `<tr><td colspan="6" class="empty">No pets reported yet</td></tr>`; return; }
    const maxCount = Math.max(...rows.map((pet) => Number(pet.count || 0)), 1);
    body.innerHTML = rows.map((pet) => {
      const variant = pet.variant === "default" ? "" : pet.variant.replace("_", " ");
      const rarity = pet.rarity || "Unrated";
      const cls = statusClass(rarity);
      const count = Number(pet.count || 0);
      const grown = Number(pet.full_grown || 0);
      return `<tr><td><span class="pet-name">${escapeHtml(pet.name)}</span><span class="pet-variant">${escapeHtml(variant || "standard")}</span></td><td><span class="rarity-chip ${cls}">${escapeHtml(rarity)}</span></td><td>${integer(count)}</td><td>${integer(grown)}</td><td><div class="pet-progress"><div class="progress-track"><span style="width:${Math.min(100, Math.round((count / maxCount) * 100))}%"></span></div><small>${count ? Math.round((grown / count) * 100) : 0}% FG</small></div></td><td>${pet.priced || Number(pet.value_usd) > 0 ? money(pet.value_usd) : "-"}</td></tr>`;
    }).join("");
  }

  async function refresh() {
    if (busy || appView.hidden) return;
    try {
      const data = await api("/api/status?phone=all");
      renderPhoneOptions(data.available_phones || data.phones.map((phone) => phone.phone));
      renderSummary(data.summary, data.hoppers || []);
      renderCharts(data.pets || []);
      renderHoppers(data.hoppers || []);
      if ($("rotation-dialog").open && rotationHopperId) {
        const hopper = hopperById(rotationHopperId);
        if (hopper) renderSavedRotation(hopper);
      }
      renderPhones(data.phones || []);
      renderPhones(data.phones || [], "hopper-phone-cards");
      renderInventory(data.inventory);
      renderPets(data.pets);
      const updated = `Updated ${new Date().toLocaleTimeString()}`;
      $("last-updated").textContent = updated;
      $("sidebar-updated").textContent = updated.replace("Updated ", "");
    } catch (error) {
      if (!appView.hidden) { $("last-updated").textContent = error.message; $("sidebar-updated").textContent = "Sync error"; }
    }
  }

  async function sendCommand(action, extra = {}) {
    if (busy) return;
    busy = true;
    $("command-result").textContent = "Sending...";
    try {
      const response = await api("/api/command", { method: "POST", body: JSON.stringify({ phone: commandPhone(), action, ...extra }) });
      $("command-result").textContent = response.result || "Command accepted.";
      $("output").textContent = response.result || "Command accepted.";
      await refresh();
    } catch (error) { $("command-result").textContent = error.message; $("output").textContent = error.message; }
    finally { busy = false; }
  }

  async function sendHopperCommands(action, hoppers) {
    if (busy || !hoppers.length) return;
    busy = true;
    $("command-result").textContent = `Sending ${action} to ${hoppers.length} hopper${hoppers.length === 1 ? "" : "s"}...`;
    try {
      const responses = await Promise.all(hoppers.map((hopper) => api("/api/command", { method: "POST", body: JSON.stringify({ phone: hopper.phone, action, hopper: hopper.hopper }) })));
      const output = responses.map((response) => response.result).join("\n");
      $("command-result").textContent = output || "Command accepted."; $("output").textContent = output || "Command accepted."; selectedHoppers.clear(); await refresh();
    } catch (error) { $("command-result").textContent = error.message; $("output").textContent = error.message; }
    finally { busy = false; }
  }

  function hopperById(id) { return currentHoppers.find((hopper) => hopper.id === id); }
  function actionPayload(action) {
    if (["start", "stop", "restart", "goto", "goto_pin", "pin", "unpin", "logs", "assign", "servers", "script_get", "script_add", "script_del"].includes(action)) {
      const payload = { hopper: number("hopper-number") };
      if (["goto", "goto_pin"].includes(action)) payload.server = number("server-number");
      if (action === "pin") payload.url = $("pin-url").value.trim();
      if (action === "logs") payload.lines = number("log-lines");
      if (action === "assign") { payload.first = number("assignment-first"); payload.last = number("assignment-last"); }
      if (["script_get", "script_add", "script_del"].includes(action)) { payload.name = $("script-name").value.trim(); if (action === "script_add") payload.url = $("script-url").value.trim(); }
      return payload;
    }
    if (["all_goto", "link_add"].includes(action)) return { url: $(action === "link_add" ? "link-url" : "pin-url").value.trim() };
    if (action === "autotrade") { const options = { items: $("trade-items").value.trim(), usernames: $("trade-usernames").value.trim() }; document.querySelectorAll(".trade-category:checked").forEach((input) => { options[input.value] = true; }); return options; }
    return {};
  }

  $("login-form").addEventListener("submit", async (event) => { event.preventDefault(); $("login-error").textContent = ""; try { await api("/api/login", { method: "POST", body: JSON.stringify({ token: $("login-token").value }) }); $("login-token").value = ""; showApp(); await refresh(); } catch (error) { $("login-error").textContent = error.message; } });
  $("logout-button").addEventListener("click", async () => { try { await api("/api/logout", { method: "POST" }); } catch (_) { /* already logged out */ } showLogin(); });
  $("refresh-button").addEventListener("click", refresh);
  $("sidebar-toggle").addEventListener("click", openSidebar);
  $("sidebar-backdrop").addEventListener("click", closeSidebar);
  document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => setPage(button.dataset.page)));
  $("hopper-search").addEventListener("input", () => renderHoppers(currentHoppers));
  $("hopper-phone-filter").addEventListener("change", () => renderHoppers(currentHoppers));
  $("hopper-device-filter").addEventListener("change", () => renderHoppers(currentHoppers));
  $("hopper-select-all").addEventListener("change", (event) => { currentHoppers.filter(hopperSearchMatch).forEach((hopper) => { if (event.target.checked) selectedHoppers.add(hopper.id); else selectedHoppers.delete(hopper.id); }); renderHoppers(currentHoppers); });
  $("hopper-body").addEventListener("change", (event) => { if (!event.target.classList.contains("hopper-check")) return; if (event.target.checked) selectedHoppers.add(event.target.dataset.hopperId); else selectedHoppers.delete(event.target.dataset.hopperId); renderHoppers(currentHoppers); });
  $("hopper-body").addEventListener("click", (event) => { const button = event.target.closest("[data-hopper-action]"); if (!button) return; const hopper = hopperById(button.dataset.hopperId); if (!hopper) return; if (button.dataset.hopperAction === "rotation") openRotation(hopper); else sendHopperCommands(button.dataset.hopperAction, [hopper]); });
  $("hopper-bulk-start").addEventListener("click", () => sendHopperCommands("start", currentHoppers.filter((hopper) => selectedHoppers.has(hopper.id))));
  $("hopper-bulk-stop").addEventListener("click", () => sendHopperCommands("stop", currentHoppers.filter((hopper) => selectedHoppers.has(hopper.id))));
  $("rotation-form").addEventListener("submit", saveRotation);
  $("rotation-close").addEventListener("click", closeRotation);
  $("rotation-cancel").addEventListener("click", closeRotation);
  $("rotation-dialog").addEventListener("cancel", () => { rotationHopperId = null; });
  document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => sendCommand(button.dataset.action, actionPayload(button.dataset.action))));
  showLogin();
})();
