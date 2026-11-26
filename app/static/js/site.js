/* Site page: hourly values against the threshold, and hours above threshold day by day */
(function () {
  "use strict";
  var TX = window.TX, data = TX.pageData();
  if (!data) return;
  var KPIS = ["PD", "JITTER", "RTT"];
  var NAMES = { PD: "Packet Drop", JITTER: "Jitter", RTT: "RTT" };
  var LIMITS = { PD: +data.thresholds.pd_pct, JITTER: +data.thresholds.jitter_ms, RTT: +data.thresholds.rtt_ms };
  var UNITS = { PD: "%", JITTER: "ms", RTT: "ms" };

  function value(k, v) { return k === "PD" ? TX.num(v, 2) + " %" : TX.num(v, 1) + " ms"; }

  var hourly = data.hourly || [];
  var hours = hourly.map(function (r) { return TX.hourLabel(r.t); });
  var lines = TX.pathLines(data.byKind), multi = lines.length > 1;
  var legend = document.getElementById("hour-legend");
  if (legend && multi) {
    lines.forEach(function (l) {
      var mark = TX.el("i", { class: "line-key" + (l.dash ? " dashed" : "") });
      mark.style.borderTopColor = l.color;
      legend.appendChild(TX.el("span", {}, mark, l.name));
    });
  }
  KPIS.forEach(function (k) {
    var values = hourly.map(function (r) { return r[k]; });
    var series = multi ? lines.map(function (l) {
      return { name: l.name, color: l.color, dash: l.dash, values: l.values[k] };
    }) : [{ name: NAMES[k], color: TX.colors[k], values: values }];
    TX.charts.line(document.getElementById("chart-" + k), {
      labels: hours, area: !multi,
      series: series,
      threshold: { value: LIMITS[k], label: "Threshold " + LIMITS[k] + " " + UNITS[k] },
      format: function (v) { return value(k, v); },
      title: function (i) { return TX.hourRange(hourly[i].t); },
      note: function (i) {
        var v = values[i];
        return v === null || v === undefined ? "" : v >= LIMITS[k] ? "At or above the threshold" : "Below the threshold";
      }
    });
  });

  var history = data.history || { dates: [] };
  var node = document.getElementById("chart-history");
  if (node && history.dates.length >= 2) {
    TX.charts.line(node, {
      labels: history.dates.map(TX.shortDate), yMax: 24, yInterval: 6,
      series: KPIS.map(function (k) { return { name: NAMES[k], color: TX.colors[k], values: history[k] }; }),
      format: function (v) { return v + (v === 1 ? " hour" : " hours"); },
      title: function (i) { return TX.dayDate(history.dates[i]); }
    });
  }
})();
