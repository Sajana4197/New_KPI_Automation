/* Dashboard: hourly small multiples, day-by-day trend, sparklines */
(function () {
  "use strict";
  var TX = window.TX, el = TX.el, data = TX.pageData();
  if (!data) return;
  var KPIS = ["PD", "JITTER", "RTT"];
  var NAMES = { PD: "Packet Drop", JITTER: "Jitter", RTT: "RTT" };
  var NS = "http://www.w3.org/2000/svg";

  /* ---- sites above threshold, hour by hour ---- */
  var profile = data.profile || [];
  var hours = profile.map(function (r) { return TX.hourLabel(r.t); });
  KPIS.forEach(function (k) {
    var values = profile.map(function (r) { return r[k]; });
    var peakBox = document.getElementById("peak-" + k);
    var top = -1, at = -1;
    values.forEach(function (v, i) { if (v > top) { top = v; at = i; } });
    if (peakBox) {
      if (top > 0) {
        peakBox.append("Peak ", el("b", { text: TX.num(top) + (top === 1 ? " site" : " sites") }),
          " at " + hours[at] + ", " + TX.dayDate(profile[at].t));
      } else {
        peakBox.textContent = profile.length ? "No site reached the threshold in any hour." : "No hourly data for this day.";
      }
    }
    TX.charts.line(document.getElementById("chart-" + k), {
      labels: hours, area: true,
      series: [{ name: "Sites above threshold", color: TX.colors[k], values: values }],
      title: function (i) { return TX.hourRange(profile[i].t); }
    });
  });

  var twin = document.querySelector("#profile-table .table-wrap");
  if (twin && profile.length) {
    var body = el("tbody");
    profile.forEach(function (r) {
      body.appendChild(el("tr", {}, el("td", { class: "nowrap", text: TX.hourRange(r.t) }),
        el("td", { class: "right num", text: TX.num(r.PD) }), el("td", { class: "right num", text: TX.num(r.JITTER) }),
        el("td", { class: "right num", text: TX.num(r.RTT) })));
    });
    twin.appendChild(el("table", { class: "table" },
      el("thead", {}, el("tr", {}, el("th", { text: "Hour" }), el("th", { class: "right", text: "Packet Drop sites" }),
        el("th", { class: "right", text: "Jitter sites" }), el("th", { class: "right", text: "RTT sites" }))), body));
  }

  /* ---- issues by site category: play the entrance when the card scrolls into view ---- */
  var catCard = document.getElementById("category-card");
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (catCard && !still && "IntersectionObserver" in window) {
    catCard.classList.add("anim-ready");
    var countUp = function (node, delay) {
      var end = +node.dataset.countup;
      if (!(end > 0)) return;
      node.textContent = "0";
      window.setTimeout(function () {
        var t0 = null;
        var tick = function (now) {
          if (t0 === null) t0 = now;
          var k = Math.min((now - t0) / 1000, 1);
          node.textContent = TX.num(Math.round(end * (1 - Math.pow(1 - k, 3))));
          if (k < 1) window.requestAnimationFrame(tick);
        };
        window.requestAnimationFrame(tick);
      }, delay);
    };
    var seen = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        seen.disconnect();
        catCard.classList.add("is-visible");
        catCard.querySelectorAll(".cat-lane").forEach(function (lane, i) {
          lane.querySelectorAll("[data-countup]").forEach(function (b) { countUp(b, 250 + i * 110); });
        });
      });
    }, { threshold: 0.2 });
    seen.observe(catCard);
  }

  /* ---- issues by site category: click a ring to see the worst sites ---- */
  var cats = data.categories || [];
  var drawer = document.getElementById("cat-drawer");
  if (drawer && drawer.showModal && cats.length) {
    var body = document.getElementById("cat-drawer-body");
    var crumb = document.getElementById("cat-drawer-crumb");
    var allLink = document.getElementById("cat-drawer-all");
    var UNITS = { PD: "%", JITTER: "ms", RTT: "ms" };
    var dayHours = Math.max(1, profile.length);
    var opener = null;

    var openFor = function (code, k) {
      var c = cats.filter(function (x) { return x.code === code; })[0];
      if (!c) return;
      var total = cats.reduce(function (s, x) { return s + x[k]; }, 0);
      var list = c.top[k] || [];
      var color = TX.colors[k];
      body.textContent = "";
      crumb.textContent = "Issues by site category · " + TX.dayDate(data.current);

      var dot = el("i", { class: "dot " + k });
      body.appendChild(el("div", { class: "worst-head" },
        el("span", { class: "worst-kpi" }, dot, NAMES[k]),
        el("h2", { text: "Worst " + NAMES[k] + " sites in " + c.code }),
        el("p", { class: "soft", text: c.label + " · " + TX.num(c[k]) + (c[k] === 1 ? " site" : " sites") + " listed · " +
          TX.num(total ? c[k] / total * 100 : 0, 1) + " % of all " + NAMES[k] + " sites" })));

      var box = el("ol", { class: "worst-list" });
      list.forEach(function (s, n) {
        var name = s.site_id ? el("a", { href: "/sites/" + s.site_id, text: s.name, title: s.name }) : el("b", { text: s.name, title: s.name });
        var bar = el("span", { class: "worst-bar" }, el("i"));
        bar.firstChild.style.width = (s.hours / Math.max(dayHours, s.hours) * 100).toFixed(1) + "%";
        bar.firstChild.style.background = color;
        var facts = el("div", { class: "worst-facts" },
          el("span", {}, el("b", { text: String(s.hours) }), " h above threshold"),
          s.peak !== null && s.peak !== undefined ? el("span", {}, "Peak ", el("b", { text: TX.num(s.peak, 2) + " " + UNITS[k] })) : null,
          el("span", { class: s.pending >= 30 ? "pend-long" : s.pending >= 7 ? "pend-mid" : "" },
            s.pending ? "Pending " + s.pending + (s.pending === 1 ? " day" : " days") : "First day on the report"));
        var row = el("li", { class: "worst-row", style: "--i:" + n },
          el("span", { class: "worst-rank", text: String(n + 1) }),
          el("div", { class: "worst-main" },
            el("div", { class: "worst-name" }, name, s.is_new ? el("span", { class: "pill pill-new", text: "New" }) : null),
            bar, facts,
            s.feedback ? el("div", { class: "worst-fb" }, TX.icon("info", "sm"), " " + s.feedback) : null));
        box.appendChild(row);
      });
      body.appendChild(box);
      if (c[k] > list.length) {
        body.appendChild(el("p", { class: "small muted", text: "Showing the " + list.length + " worst of " + TX.num(c[k]) +
          " sites, ranked by hours above the threshold, then by peak value." }));
      } else {
        body.appendChild(el("p", { class: "small muted", text: "Ranked by hours above the threshold, then by peak value." }));
      }
      allLink.href = "/report/" + data.current + "#" + k + "-" + c.code;
      allLink.querySelector("span").textContent = "See all " + TX.num(c[k]) + " in the daily report";
      if (!drawer.open) drawer.showModal();
      body.scrollTop = 0;
    };

    document.querySelectorAll(".cat-kpi[data-kpi]").forEach(function (cell) {
      cell.addEventListener("click", function () { opener = cell; openFor(cell.dataset.cat, cell.dataset.kpi); });
      cell.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); opener = cell; openFor(cell.dataset.cat, cell.dataset.kpi); }
      });
    });
    drawer.addEventListener("click", function (ev) { if (ev.target === drawer) drawer.close(); });   /* click outside */
    drawer.addEventListener("close", function () { if (opener) opener.focus(); });
  }

  /* ---- listed sites, day by day (needs at least two reports) ---- */
  var trend = data.trend || { dates: [] };
  if (trend.dates.length >= 2) {
    var days = trend.dates.map(TX.shortDate);
    KPIS.forEach(function (k) {
      TX.charts.line(document.getElementById("trend-" + k), {
        labels: days, area: true,
        series: [{ name: NAMES[k] + " sites", color: TX.colors[k], values: trend[k] }],
        title: function (i) { return TX.dayDate(trend.dates[i]); }
      });

      var holder = document.querySelector('[data-spark="' + k + '"]');
      if (!holder) return;
      var values = trend[k], w = 72, h = 26, pad = 4;
      var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values), span = hi - lo || 1;
      var pts = values.map(function (v, i) {
        return [pad + i * (w - 2 * pad) / (values.length - 1), h - pad - (v - lo) / span * (h - 2 * pad)];
      });
      var svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 " + w + " " + h);
      svg.setAttribute("aria-hidden", "true");
      var path = document.createElementNS(NS, "polyline");
      path.setAttribute("points", pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" "));
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", TX.colors[k]);
      path.setAttribute("stroke-width", "2");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
      var last = pts[pts.length - 1];
      var dot = document.createElementNS(NS, "circle");
      dot.setAttribute("cx", last[0].toFixed(1));
      dot.setAttribute("cy", last[1].toFixed(1));
      dot.setAttribute("r", "4");
      dot.setAttribute("fill", TX.colors[k]);
      dot.setAttribute("stroke", "#ffffff");
      dot.setAttribute("stroke-width", "2");
      svg.appendChild(path);
      svg.appendChild(dot);
      holder.appendChild(svg);
      holder.title = "Last " + values.length + " reports";
    });
  }
})();
