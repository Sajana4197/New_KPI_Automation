/* Daily report: tabs, filters, sorting, paging and feedback editing */
(function () {
  "use strict";
  var TX = window.TX, el = TX.el, data = TX.pageData();
  if (!data) return;

  var PER_PAGE = 50;
  var items = data.items.map(function (r) {
    return { id: r[0], kpi: r[1], siteId: r[2], name: r[3], category: r[4], hours: r[5], pending: r[6],
             feedback: r[7], source: r[8], isNew: !!r[9], peak: r[10], edited: !!r[11] };
  });
  var byId = {};
  items.forEach(function (i) { byId[i.id] = i; });

  var state = { kpi: "PD", q: "", cat: "", source: "", pending: "", onlyNew: false,
                sort: "hours", dir: -1, page: 1, selected: new Set() };
  /* The address may name a KPI and a category, e.g. #PD-CBD (used by the dashboard's worst-sites panel). */
  var hashParts = (window.location.hash || "").replace("#", "").toUpperCase().split("-");
  var fromHash = hashParts[0];
  if (/^(CBD|GC|IBS|OS)$/.test(hashParts[1] || "")) state.cat = hashParts[1];
  if (data.kpis.indexOf(fromHash) >= 0) state.kpi = fromHash;
  else {
    var firstWithRows = data.kpis.filter(function (k) { return items.some(function (i) { return i.kpi === k; }); })[0];
    if (firstWithRows) state.kpi = firstWithRows;
  }

  var tbody = document.querySelector("#report-table tbody");
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab[data-kpi]"));
  var catChips = Array.prototype.slice.call(document.querySelectorAll("#f-cat .chip"));
  var sortButtons = Array.prototype.slice.call(document.querySelectorAll(".th-sort"));
  var qInput = document.getElementById("f-q"), sourceSel = document.getElementById("f-source");
  var pendingSel = document.getElementById("f-pending"), newChip = document.getElementById("f-new");
  var countText = document.getElementById("f-count"), pagerText = document.getElementById("pager-text");
  var prevBtn = document.getElementById("pager-prev"), nextBtn = document.getElementById("pager-next");
  var ruleNote = document.getElementById("rule-note");
  var selAll = document.getElementById("sel-all"), bulk = document.getElementById("bulk");
  var suggestions = document.getElementById("fb-suggestions");
  var columns = document.querySelectorAll("#report-table thead th").length;
  var current = [];           // filtered + sorted rows of the open tab

  function peakText(i) {
    if (i.peak === null || i.peak === undefined) return "–";
    return i.kpi === "PD" ? TX.num(i.peak, 2) + " %" : TX.num(i.peak, 1) + " ms";
  }

  function pendingPill(days) {
    if (!days) return el("span", { class: "pill", text: "First day" });
    var tone = days >= 30 ? "pill-crit" : days >= 7 ? "pill-serious" : "pill-warn";
    return el("span", { class: "pill " + tone }, TX.icon("clock"), days + (days === 1 ? " day" : " days"));
  }

  function compare(a, b) {
    var key = state.sort, x = a[key], y = b[key], out;
    if (key === "name" || key === "category" || key === "feedback" || key === "source") {
      out = String(x).localeCompare(String(y), "en", { sensitivity: "base", numeric: true });
    } else {
      out = (x === null ? -Infinity : x) - (y === null ? -Infinity : y);
    }
    out *= state.dir;
    if (!out && key !== "hours") out = b.hours - a.hours;
    return out || a.name.localeCompare(b.name, "en", { sensitivity: "base", numeric: true });
  }

  function apply() {
    var q = state.q.trim().toLowerCase();
    var inTab = items.filter(function (i) { return i.kpi === state.kpi; });
    var counts = {};
    inTab.forEach(function (i) { counts[i.category] = (counts[i.category] || 0) + 1; });
    catChips.forEach(function (chip) {
      var small = chip.querySelector("small");
      if (small) small.textContent = TX.num(counts[chip.dataset.cat] || 0);
      chip.classList.toggle("on", chip.dataset.cat === state.cat);
    });
    current = inTab.filter(function (i) {
      if (q && i.name.toLowerCase().indexOf(q) < 0) return false;
      if (state.cat && i.category !== state.cat) return false;
      if (state.source && i.source !== state.source) return false;
      if (state.onlyNew && !i.isNew) return false;
      if (state.pending === "0" && i.pending !== 0) return false;
      if (state.pending && state.pending !== "0" && i.pending < +state.pending) return false;
      return true;
    }).sort(compare);
    var pages = Math.max(1, Math.ceil(current.length / PER_PAGE));
    state.page = Math.min(Math.max(1, state.page), pages);
    var kpiName = { PD: "Packet Drop", JITTER: "Jitter", RTT: "RTT" }[state.kpi] || "";
    countText.textContent = "";
    if (current.length === inTab.length) {
      countText.append("Showing all ", el("b", { text: TX.num(inTab.length) }), " " + kpiName + (inTab.length === 1 ? " site" : " sites"));
    } else {
      countText.append("Showing ", el("b", { text: TX.num(current.length) }), " of " + TX.num(inTab.length) + " " + kpiName + " sites");
    }
    showActive();
    render(pages, inTab.length);
  }

  /* The filters that are on, as tags that can be removed one at a time. */
  var activeBox = document.getElementById("f-active"), tagBox = document.getElementById("f-tags");
  var CAT_NAMES = { CBD: "Central Business District", GC: "Greater Colombo", IBS: "In-Building", OS: "Outstation" };
  function showActive() {
    var tags = [];
    if (state.q.trim()) tags.push(["Name contains", "“" + state.q.trim() + "”", function () { qInput.value = ""; state.q = ""; }]);
    if (state.cat) tags.push(["Category", state.cat + " · " + (CAT_NAMES[state.cat] || ""), function () { state.cat = ""; }]);
    if (state.source) tags.push(["Source file", sourceSel.options[sourceSel.selectedIndex].text, function () { sourceSel.value = ""; state.source = ""; }]);
    if (state.pending) tags.push(["Days on the report", pendingSel.options[pendingSel.selectedIndex].text, function () { pendingSel.value = ""; state.pending = ""; }]);
    if (state.onlyNew) tags.push(["Show", "New sites only", function () { state.onlyNew = false; syncNew(); }]);
    tagBox.textContent = "";
    tags.forEach(function (t) {
      tagBox.appendChild(el("button", { class: "fx-tag", type: "button", title: "Remove this filter",
        on: { click: function () { t[2](); reset(); } } },
        el("span", { class: "fx-tag-k", text: t[0] + ":" }), " " + t[1], TX.icon("x", "sm")));
    });
    activeBox.classList.toggle("hide", !tags.length);
  }
  function syncNew() {
    newChip.setAttribute("aria-pressed", state.onlyNew ? "true" : "false");
    newChip.setAttribute("aria-checked", state.onlyNew ? "true" : "false");
  }

  function render(pages, tabTotal) {
    tabs.forEach(function (t) {
      var on = t.dataset.kpi === state.kpi;
      t.setAttribute("aria-selected", on ? "true" : "false");
      if (on && ruleNote) {
        ruleNote.textContent = "“Hours above” counts: " + t.dataset.rule.charAt(0).toLowerCase() +
          t.dataset.rule.slice(1) + ", taking the worst path of the site in each hour. Peak is the highest value of the day.";
      }
    });
    sortButtons.forEach(function (b) {
      var on = b.dataset.sort === state.sort;
      b.classList.toggle("on", on);
      var use = b.querySelector("use");
      if (use) use.setAttribute("href", on && state.dir > 0 ? "#i-arrow-up" : "#i-arrow-down");
    });

    var start = (state.page - 1) * PER_PAGE;
    var rows = current.slice(start, start + PER_PAGE);
    tbody.textContent = "";
    if (!rows.length) {
      var message = tabTotal ? "No sites match these filters."
        : "No site reached the " + data.labels[state.kpi] + " threshold for long enough to be listed.";
      tbody.appendChild(el("tr", { class: "empty-row" }, el("td", { colSpan: columns, text: message })));
    }
    rows.forEach(function (i, n) { tbody.appendChild(rowFor(i, start + n + 1)); });

    pagerText.textContent = current.length
      ? TX.num(start + 1) + "–" + TX.num(start + rows.length) + " of " + TX.num(current.length) + " · page " + state.page + " of " + pages
      : "";
    prevBtn.disabled = state.page <= 1;
    nextBtn.disabled = state.page >= pages;
    syncSelection(rows);
  }

  function rowFor(i, n) {
    var tr = el("tr", { data: { id: i.id } });
    if (data.canEdit) {
      tr.appendChild(el("td", { class: "sel" }, el("input", {
        type: "checkbox", checked: state.selected.has(i.id), "aria-label": "Select " + i.name,
        on: { change: function (ev) {
          if (ev.target.checked) state.selected.add(i.id); else state.selected.delete(i.id);
          syncSelection();
        } }
      })));
    }
    tr.appendChild(el("td", { class: "right num muted skip", text: n }));
    var site = i.siteId ? el("a", { class: "site-link", href: "/sites/" + i.siteId, text: i.name })
      : el("span", { class: "site-link", text: i.name });
    tr.appendChild(el("td", { class: "full site", data: { label: "Site" } }, site,
      i.isNew ? " " : null, i.isNew ? el("span", { class: "pill pill-new", title: "First seen in this day's files" }, TX.icon("sparkle"), "New") : null));
    tr.appendChild(el("td", { data: { label: "Category" } }, el("span", { class: "tag", text: i.category })));
    var bar = el("span", { class: "bar " + i.kpi });
    bar.style.width = Math.min(100, i.hours / 24 * 100) + "%";
    tr.appendChild(el("td", { data: { label: "Hours above" } },
      el("div", { class: "hours", title: i.hours + " of the day's hours" }, el("b", { text: i.hours }), el("span", { class: "track" }, bar))));
    tr.appendChild(el("td", { class: "right num nowrap", data: { label: "Peak" }, text: peakText(i) }));
    tr.appendChild(el("td", { data: { label: "Pending" } }, pendingPill(i.pending)));
    tr.appendChild(el("td", { class: "full wrap", data: { label: "Feedback" } }, feedbackCell(i)));
    return tr;
  }

  function feedbackCell(i) {
    var mark = i.edited ? el("span", { class: "fb-edited", title: "Edited in this report" }) : null;
    if (!data.canEdit) return el("span", { class: "fb" }, mark, i.feedback);
    return el("button", { class: "fb", type: "button", title: "Click to edit", on: { click: function (ev) { edit(i, ev.currentTarget); } } },
      mark, el("span", { text: i.feedback }), TX.icon("edit"));
  }

  function edit(i, button) {
    var done = false;
    var input = el("input", { class: "input input-sm fb-input", value: i.feedback, maxLength: 500, "aria-label": "Feedback for " + i.name });
    input.setAttribute("list", "fb-suggestions");
    function finish(saveIt) {
      if (done) return;
      done = true;
      var text = input.value.replace(/\s+/g, " ").trim();
      if (!saveIt || !text || text === i.feedback) { input.replaceWith(feedbackCell(i)); return; }
      input.disabled = true;
      save([i.id], text).then(function () { if (input.isConnected) input.replaceWith(feedbackCell(i)); },
        function () { done = false; input.disabled = false; input.focus(); });
    }
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
      else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", function () { finish(true); });
    button.replaceWith(input);
    input.focus();
    input.select();
  }

  function save(ids, text) {
    return TX.post("/api/feedback", { ids: ids, feedback: text }).then(function (res) {
      res.saved.forEach(function (id) { if (byId[id]) { byId[id].feedback = res.feedback; byId[id].edited = true; } });
      var known = Array.prototype.some.call(suggestions.options, function (o) { return o.value === res.feedback; });
      if (!known) suggestions.appendChild(el("option", { value: res.feedback }));
      TX.toast("good", res.saved.length === 1 ? "Feedback saved." : "Feedback saved for " + TX.num(res.saved.length) + " sites.");
      return res;
    }, function (err) {
      TX.toast("bad", err.message || "Could not save the feedback.");
      throw err;
    });
  }

  /* ---- selecting rows for one shared feedback ---- */
  function syncSelection(rows) {
    if (!data.canEdit || !bulk) return;
    var start = (state.page - 1) * PER_PAGE;
    rows = rows || current.slice(start, start + PER_PAGE);
    var onPage = rows.filter(function (i) { return state.selected.has(i.id); }).length;
    selAll.checked = rows.length > 0 && onPage === rows.length;
    selAll.indeterminate = onPage > 0 && onPage < rows.length;
    var n = state.selected.size;
    bulk.classList.toggle("hide", n === 0);
    document.getElementById("bulk-count").textContent = TX.num(n) + " selected";
    var all = document.getElementById("bulk-all");
    all.textContent = n < current.length ? "Select all " + TX.num(current.length) + " shown" : "";
  }

  if (data.canEdit && bulk) {
    selAll.addEventListener("change", function () {
      var start = (state.page - 1) * PER_PAGE;
      current.slice(start, start + PER_PAGE).forEach(function (i) {
        if (selAll.checked) state.selected.add(i.id); else state.selected.delete(i.id);
      });
      apply();
    });
    document.getElementById("bulk-all").addEventListener("click", function () {
      current.forEach(function (i) { state.selected.add(i.id); });
      apply();
    });
    document.getElementById("bulk-clear").addEventListener("click", function () { state.selected.clear(); apply(); });
    var bulkText = document.getElementById("bulk-text"), bulkApply = document.getElementById("bulk-apply");
    var applyBulk = function () {
      var text = bulkText.value.replace(/\s+/g, " ").trim();
      if (!text) { bulkText.focus(); return; }
      bulkApply.disabled = true;
      save(Array.from(state.selected), text).then(function () {
        state.selected.clear();
        bulkText.value = "";
        apply();
      }, function () {}).then(function () { bulkApply.disabled = false; });
    };
    bulkApply.addEventListener("click", applyBulk);
    bulkText.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); applyBulk(); } });
  }

  /* ---- controls ---- */
  function reset() { state.page = 1; apply(); }
  tabs.forEach(function (t) {
    t.addEventListener("click", function () {
      state.kpi = t.dataset.kpi;
      state.selected.clear();
      history.replaceState(null, "", "#" + state.kpi);
      reset();
    });
  });
  catChips.forEach(function (chip) { chip.addEventListener("click", function () { state.cat = chip.dataset.cat; reset(); }); });
  var typing = null;
  qInput.addEventListener("input", function () {
    clearTimeout(typing);
    typing = setTimeout(function () { state.q = qInput.value; reset(); }, 120);
  });
  sourceSel.addEventListener("change", function () { state.source = sourceSel.value; reset(); });
  pendingSel.addEventListener("change", function () { state.pending = pendingSel.value; reset(); });
  newChip.addEventListener("click", function () {
    state.onlyNew = !state.onlyNew;
    syncNew();
    reset();
  });
  document.getElementById("f-clear").addEventListener("click", function () {
    qInput.value = ""; sourceSel.value = ""; pendingSel.value = "";
    state.q = ""; state.cat = ""; state.source = ""; state.pending = ""; state.onlyNew = false;
    syncNew();
    reset();
  });
  sortButtons.forEach(function (b) {
    b.addEventListener("click", function () {
      var key = b.dataset.sort;
      if (state.sort === key) state.dir = -state.dir;
      else { state.sort = key; state.dir = (key === "name" || key === "category" || key === "feedback" || key === "source") ? 1 : -1; }
      reset();
    });
  });
  prevBtn.addEventListener("click", function () { state.page -= 1; apply(); window.scrollTo({ top: 0, behavior: "smooth" }); });
  nextBtn.addEventListener("click", function () { state.page += 1; apply(); window.scrollTo({ top: 0, behavior: "smooth" }); });
  window.addEventListener("hashchange", function () {
    var parts = (window.location.hash || "").replace("#", "").toUpperCase().split("-"), k = parts[0];
    var cat = /^(CBD|GC|IBS|OS)$/.test(parts[1] || "") ? parts[1] : state.cat;
    if (data.kpis.indexOf(k) >= 0 && (k !== state.kpi || cat !== state.cat)) { state.kpi = k; state.cat = cat; state.selected.clear(); reset(); }
  });

  apply();
})();
