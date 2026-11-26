/* Sites page: hover labels on the hour strips, and the slide-in panel with one site's details */
(function () {
  "use strict";
  var TX = window.TX, el = TX.el, data = TX.pageData() || {};
  var KPIS = ["PD", "JITTER", "RTT"];
  var NAMES = { PD: "Packet Drop", JITTER: "Jitter", RTT: "RTT" };
  var UNITS = { PD: "%", JITTER: "ms", RTT: "ms" };

  function value(k, v) {
    if (v === null || v === undefined) return "–";
    return k === "PD" ? TX.num(v, 2) + " %" : TX.num(v, 1) + " ms";
  }
  function hoursText(n) { return n + (n === 1 ? " hour" : " hours"); }

  /* ---- hover label on a strip block ------------------------------------------------------ */
  var list = document.getElementById("site-list");
  var tip = document.getElementById("strip-tip");
  function hideTip() { if (tip) tip.classList.add("hide"); }
  if (list && tip) {
    list.addEventListener("mousemove", function (ev) {
      var block = ev.target.closest(".strip > i");
      if (!block) { hideTip(); return; }
      var strip = block.parentNode, row = strip.closest(".site-row");
      var k = strip.dataset.k, kind = strip.dataset.kind, i = Array.prototype.indexOf.call(strip.children, block);
      var site = ((data.sites || {})[row.dataset.site] || {})[kind];
      var v = site ? site[k][i] : null, limit = +data.limits[k];
      var state = v === null || v === undefined ? "No measurement in this hour"
        : v >= limit ? "At or above the threshold of " + limit + " " + UNITS[k] : "Below the threshold of " + limit + " " + UNITS[k];
      var dot = el("i", { class: "dot " + k });
      tip.replaceChildren(el("div", { class: "tip" },
        el("div", { class: "tip-title", text: TX.hourRange(data.hours[i]) + (kind ? " · " + kind + " path" : "") }),
        el("div", { class: "tip-row" }, el("span", {}, dot, NAMES[k]), el("b", { text: value(k, v) })),
        el("div", { class: "tip-note", text: state })));
      tip.classList.remove("hide");
      var w = tip.offsetWidth, h = tip.offsetHeight;
      var x = Math.min(Math.max(8, ev.clientX - w / 2), window.innerWidth - w - 8);
      var y = ev.clientY - h - 14;
      if (y < 8) y = ev.clientY + 18;
      tip.style.left = x + "px";
      tip.style.top = y + "px";
    });
    list.addEventListener("mouseleave", hideTip);
    window.addEventListener("scroll", hideTip, { passive: true });
  }

  /* ---- slide-in panel ----------------------------------------------------------------------- */
  var drawer = document.getElementById("site-drawer");
  var body = document.getElementById("drawer-body");
  if (!drawer || !body || !drawer.showModal) return;
  var ids = data.ids || [];
  var current = null, charts = [], request = 0;
  var prev = document.getElementById("drawer-prev"), next = document.getElementById("drawer-next");
  var pos = document.getElementById("drawer-pos"), openLink = document.getElementById("drawer-open");

  function clearCharts() {
    charts.forEach(function (c) { TX.charts.dispose(c); });
    charts = [];
  }

  function statusPill(d) {
    if (!d.daily) return el("span", { class: "pill" }, TX.icon("clock"), " No data");
    var worst = Math.max(d.daily.PD.hours, d.daily.JITTER.hours, d.daily.RTT.hours);
    if (worst >= d.min_hours) return el("span", { class: "pill pill-crit" }, TX.icon("alert"), " Listed");
    if (worst > 0) return el("span", { class: "pill pill-warn" }, TX.icon("info"), " Near the limit");
    return el("span", { class: "pill pill-good" }, TX.icon("check"), " Healthy");
  }

  function pendingPill(n) {
    if (!n) return el("span", { class: "pill" }, "First day");
    var tone = n >= 30 ? "pill-crit" : n >= 7 ? "pill-serious" : "pill-warn";
    return el("span", { class: "pill " + tone }, TX.icon("clock"), " " + n + (n === 1 ? " day" : " days") + " pending");
  }

  function section(title, note) {
    return el("div", { class: "drawer-sec" }, el("h3", { text: title }), note ? el("p", { class: "small muted", text: note }) : null);
  }

  function render(d) {
    clearCharts();
    var head = el("div", { class: "drawer-head" },
      el("h2", { text: d.name }),
      el("div", { class: "site-chips" },
        statusPill(d),
        el("span", { class: "tag", text: d.category + (d.category_label ? " · " + d.category_label : "") }),
        el("span", { class: "tag", text: d.source }),
        d.ext_id ? el("span", { class: "tag", text: "ID " + d.ext_id }) : null,
        (d.kinds || []).length ? el("span", { class: "tag", text: d.kinds.join(" + ") + " paths" }) : null,
        d.name_count > 1 ? el("span", { class: "tag", text: "renamed" }) : null),
      el("p", { class: "small muted", text: d.date
        ? "Showing " + TX.dayDate(d.date) + " · first seen " + TX.shortDate(d.first_seen) + " · last seen " + TX.shortDate(d.last_seen)
        : "First seen " + TX.shortDate(d.first_seen) + " · last seen " + TX.shortDate(d.last_seen) }));
    body.replaceChildren(head);

    if (!d.daily) {
      body.appendChild(el("p", { class: "soft", text: "No measurements are stored for this site." }));
      return;
    }

    var stats = el("div", { class: "drawer-stats" });
    KPIS.forEach(function (k) {
      var s = d.daily[k], listed = s.hours >= d.min_hours, kinds = d.kind_rows || [];
      var mini = kinds.length ? el("div", { class: "kind-mini" }) : null;
      kinds.forEach(function (r) {
        var h = r[k].hours;
        mini.appendChild(el("span", { class: h >= d.min_hours ? "is-listed" : "", title: r.kind + " path: peak " + value(k, r[k].peak) },
          el("span", { class: "tag path-tag path-" + r.kind.toLowerCase(), text: r.kind }), el("b", { text: h + " h" })));
      });
      stats.appendChild(el("div", { class: "drawer-stat" + (listed ? " is-listed" : "") },
        el("span", { class: "tile-label" }, el("i", { class: "dot " + k }), NAMES[k]),
        mini || el("b", { text: String(s.hours) }),
        el("small", { text: (mini ? "hours" : (s.hours === 1 ? "hour" : "hours")) + " at or above " + d.limits[k] + " " + UNITS[k] }),
        el("small", { class: "muted", text: "peak " + value(k, s.peak) })));
    });
    body.appendChild(stats);
    body.appendChild(el("p", { class: "small muted", text: hoursText(d.daily.samples) + " measured on " + d.daily.paths +
      (d.daily.paths === 1 ? " path" : " paths") + ". Each path type is listed at " + hoursText(d.min_hours) + " or more." }));

    if ((d.kind_rows || []).length) {
      var kbox = section("Path types", "Each path type is checked on its own. Hours at or above the threshold, and the peak.");
      var kt = el("table", { class: "table drawer-table" },
        el("thead", {}, el("tr", {}, el("th", { text: "Path type" }),
          el("th", { class: "right", text: "Packet drop" }), el("th", { class: "right", text: "Jitter" }), el("th", { class: "right", text: "RTT" }))));
      var kb = el("tbody");
      d.kind_rows.forEach(function (r) {
        var tr = el("tr", {}, el("td", {}, el("span", { class: "tag path-tag path-" + r.kind.toLowerCase(), text: r.kind })));
        KPIS.forEach(function (k) {
          var h = r[k].hours, over = h >= d.min_hours;
          tr.appendChild(el("td", { class: "right num nowrap" + (over ? " over" : "") },
            el("b", { text: hoursText(h) }), el("br"), el("small", { class: "muted", text: value(k, r[k].peak) })));
        });
        kb.appendChild(tr);
      });
      kt.appendChild(kb);
      kbox.appendChild(el("div", { class: "table-wrap" }, kt));
      body.appendChild(kbox);
    }

    if (d.listed.length) {
      var box = section("On the report", "");
      d.listed.forEach(function (r) {
        box.appendChild(el("div", { class: "list-row" },
          el("div", { class: "list-main" },
            el("b", { class: "kpi-name" }, el("i", { class: "dot " + r.kpi }), NAMES[r.kpi] + (r.path ? " · " + r.path + " path" : "")),
            el("small", { text: hoursText(r.hours) + " above threshold" + (r.feedback ? " · TXP remark: " + r.feedback : "") })),
          pendingPill(r.pending)));
      });
      body.appendChild(box);
    }

    var lines = TX.pathLines(d.by_kind), multi = lines.length > 1;
    var chartsBox = section("Hour by hour", "");
    if (multi) {
      var key = el("div", { class: "legend path-legend" });
      lines.forEach(function (l) {
        var mark = el("i", { class: "line-key" + (l.dash ? " dashed" : "") });
        mark.style.borderTopColor = l.color;
        key.appendChild(el("span", {}, mark, l.name));
      });
      chartsBox.appendChild(key);
    }
    var labels = d.hourly.map(function (r) { return TX.hourLabel(r.t); });
    var mounts = [];
    KPIS.forEach(function (k) {
      var node = el("div", { class: "chart", role: "img", "aria-label": NAMES[k] + " in each hour of the day" });
      chartsBox.appendChild(el("div", { class: "multiple" },
        el("div", { class: "multiple-head" }, el("h3", {}, el("i", { class: "dot " + k }), " " + NAMES[k]),
          el("span", { class: "multiple-note", text: UNITS[k] })), node));
      mounts.push([k, node]);
    });
    body.appendChild(chartsBox);

    if (d.paths.length) {
      var paths = section("Paths", d.path_total > d.paths.length
        ? "The " + d.paths.length + " worst of " + d.path_total + " paths. Peak value of each on this day."
        : "Peak value of each path on this day.");
      var table = el("table", { class: "table drawer-table" },
        el("thead", {}, el("tr", {}, el("th", { text: "Path" }), el("th", { text: "Type" }),
          el("th", { class: "right", text: "Packet drop" }), el("th", { class: "right", text: "Jitter" }), el("th", { class: "right", text: "RTT" }))));
      var tb = el("tbody");
      d.paths.forEach(function (p) {
        var tr = el("tr", {}, el("td", { class: "wrap mono", text: p.label }),
          el("td", {}, p.kind ? el("span", { class: "tag path-tag path-" + p.kind.toLowerCase(), text: p.kind }) : null));
        KPIS.forEach(function (k) {
          var over = p[k] !== null && p[k] !== undefined && p[k] >= d.limits[k];
          tr.appendChild(el("td", { class: "right num nowrap" + (over ? " over" : ""), text: value(k, p[k]) }));
        });
        tb.appendChild(tr);
      });
      table.appendChild(tb);
      paths.appendChild(el("div", { class: "table-wrap" }, table));
      body.appendChild(paths);
    }

    /* charts need their boxes on screen before they can size themselves */
    mounts.forEach(function (m) {
      var k = m[0], values = d.hourly.map(function (r) { return r[k]; }), limit = +d.limits[k];
      var series = multi ? lines.map(function (l) {
        return { name: l.name, color: l.color, dash: l.dash, values: l.values[k] };
      }) : [{ name: NAMES[k], color: TX.colors[k], values: values }];
      var chart = TX.charts.line(m[1], {
        labels: labels, area: !multi,
        series: series,
        threshold: { value: limit, label: "Threshold " + limit + " " + UNITS[k] },
        format: function (v) { return value(k, v); },
        title: function (i) { return TX.hourRange(d.hourly[i].t); },
        note: function (i) {
          var v = values[i];
          return v === null || v === undefined ? "" : v >= limit ? "At or above the threshold" : "Below the threshold";
        }
      });
      if (chart) charts.push(chart);
    });
  }

  function markRow(id) {
    document.querySelectorAll(".site-row.is-open").forEach(function (r) { r.classList.remove("is-open"); });
    var row = id === null ? null : document.querySelector('.site-row[data-site="' + id + '"]');
    if (row) row.classList.add("is-open");
  }

  function show(id) {
    current = id;
    var at = ids.indexOf(id), mine = ++request;
    prev.disabled = at <= 0;
    next.disabled = at < 0 || at >= ids.length - 1;
    pos.textContent = at >= 0 ? (at + 1) + " of " + ids.length + " on this page" : "";
    openLink.href = "/sites/" + id;
    markRow(id);
    clearCharts();
    body.replaceChildren(el("div", { class: "drawer-wait" }, el("span", { class: "spinner" }), "Loading the site…"));
    if (!drawer.open) drawer.showModal();
    body.scrollTop = 0;
    fetch("/api/sites/" + id, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (res) { if (!res.ok) throw new Error(String(res.status)); return res.json(); })
      .then(function (d) { if (mine === request) render(d); })
      .catch(function () {
        if (mine !== request) return;
        body.replaceChildren(el("div", { class: "drawer-wait" }, "This site could not be loaded. ",
          el("a", { href: "/sites/" + id, text: "Open its page instead" })));
      });
  }

  function step(by) {
    var at = ids.indexOf(current) + by;
    if (at >= 0 && at < ids.length) show(ids[at]);
  }

  if (list) {
    list.addEventListener("click", function (ev) {
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button) return;      /* new tab or window: let the link work */
      var row = ev.target.closest(".site-row");
      if (!row) return;
      var link = ev.target.closest("a");
      if (link && !link.hasAttribute("data-drawer")) return;
      ev.preventDefault();
      hideTip();
      show(+row.dataset.site);
    });
  }
  prev.addEventListener("click", function () { step(-1); });
  next.addEventListener("click", function () { step(1); });
  drawer.addEventListener("click", function (ev) { if (ev.target === drawer) drawer.close(); });   /* click outside */
  drawer.addEventListener("keydown", function (ev) {
    if (ev.target.closest("input, textarea, select")) return;
    if (ev.key === "ArrowLeft") step(-1);
    if (ev.key === "ArrowRight") step(1);
  });
  drawer.addEventListener("close", function () { request++; clearCharts(); markRow(null); current = null; });
})();
