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

  /* ---- issues by site category ---- */
  var cats = data.categories || [];
  KPIS.forEach(function (k) {
    TX.charts.bars(document.getElementById("cat-" + k), {
      labels: cats.map(function (c) { return c.code; }),
      values: cats.map(function (c) { return c[k]; }),
      color: TX.colors[k], name: NAMES[k] + " sites",
      title: function (i) { return cats[i].code + " · " + cats[i].label; },
      note: function (i) {
        var c = cats[i], pct = c.total ? c[k] / c.total * 100 : 0;
        return TX.num(pct, 1) + " % of the " + TX.num(c.total) + " sites in this category";
      }
    });
  });

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
