/* TX KPI Monitor - chart helpers on top of ECharts */
(function () {
  "use strict";
  var TX = window.TX, ink = TX.ink, el = TX.el;
  var FONT = getComputedStyle(document.body).fontFamily;
  var charts = [];

  function axes(labels) {
    return {
      animationDuration: 300,
      textStyle: { fontFamily: FONT },
      grid: { left: 6, right: 16, top: 16, bottom: 2, containLabel: true },
      xAxis: {
        type: "category", data: labels, boundaryGap: false,
        axisLine: { lineStyle: { color: ink.line } }, axisTick: { show: false },
        axisLabel: { color: ink.muted, fontSize: 11, hideOverlap: true, margin: 10 }
      },
      yAxis: {
        type: "value", minInterval: 1, splitNumber: 4,
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: ink.muted, fontSize: 11 },
        splitLine: { lineStyle: { color: ink.grid, type: "solid" } }
      },
      tooltip: {
        trigger: "axis", confine: true, backgroundColor: "#ffffff", borderColor: ink.line, borderWidth: 1,
        padding: [10, 12], textStyle: { color: ink.text },
        extraCssText: "border-radius:12px;box-shadow:0 14px 34px -14px rgba(15,27,45,.4);",
        axisPointer: { type: "line", lineStyle: { color: "#9fb0c6", width: 1, type: "solid" } }
      }
    };
  }

  function lineSeries(s, area) {
    var count = s.values.filter(function (v) { return v !== null && v !== undefined; }).length;
    return {
      type: "line", name: s.name, data: s.values, connectNulls: false,
      showSymbol: count <= 1, symbol: "circle", symbolSize: 8,
      lineStyle: { width: 2, color: s.color },
      itemStyle: { color: s.color, borderColor: ink.surface, borderWidth: 2 },
      areaStyle: area ? { color: s.color, opacity: 0.09 } : undefined,
      emphasis: { focus: "none", scale: 1.3, lineStyle: { width: 2 } }
    };
  }

  function tip(title, rows, note) {
    var box = el("div", { class: "tip" }, el("div", { class: "tip-title", text: title }));
    rows.forEach(function (r) {
      var dot = el("i", { class: "dot" });
      dot.style.background = r.color;
      box.appendChild(el("div", { class: "tip-row" }, el("span", {}, dot, r.name), el("b", { text: r.value })));
    });
    if (note) box.appendChild(el("div", { class: "tip-note", text: note }));
    return box;
  }

  function mount(node, option) {
    if (!node || !window.echarts) return null;
    var chart = window.echarts.init(node, null, { renderer: "canvas" });
    chart.setOption(option);
    charts.push(chart);
    return chart;
  }

  /* A round axis maximum at or above `top`, with four or five even steps below it. */
  function niceScale(top) {
    var raw = top / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag;
    var step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
    return { step: +step.toPrecision(12), max: +(Math.ceil(top / step - 1e-9) * step).toPrecision(12) };
  }

  /* One or more lines on a single axis.
     opts: labels, series [{name, color, values}], title(i), format(v), area, threshold {value, label}, yMax, note(i) */
  function line(node, opts) {
    var option = axes(opts.labels);
    var format = opts.format || function (v) { return TX.num(v); };
    option.series = opts.series.map(function (s) { return lineSeries(s, opts.area); });
    if (opts.yMax) { option.yAxis.max = opts.yMax; option.yAxis.interval = opts.yInterval; }
    if (opts.axisFormat) option.yAxis.axisLabel.formatter = opts.axisFormat;
    if (opts.threshold) {
      var limit = opts.threshold.value;
      var top = limit * 1.3;
      opts.series.forEach(function (s) {
        s.values.forEach(function (v) { if (typeof v === "number" && v > top) top = v; });
      });
      var scale = niceScale(top);
      option.yAxis.minInterval = 0;
      option.yAxis.max = scale.max;
      option.yAxis.interval = scale.step;
      option.series[0].markLine = {
        silent: true, symbol: "none", animation: false,
        lineStyle: { color: ink.text2, type: [5, 4], width: 1.5 },
        label: { formatter: opts.threshold.label, position: "insideEndTop", color: ink.text2, fontSize: 11, fontWeight: 600 },
        data: [{ yAxis: limit }]
      };
    }
    option.tooltip.formatter = function (params) {
      var i = params[0].dataIndex;
      var rows = opts.series.map(function (s) {
        var v = s.values[i];
        return { name: s.name, color: s.color, value: v === null || v === undefined ? "no data" : format(v) };
      });
      return tip(opts.title ? opts.title(i) : opts.labels[i], rows, opts.note ? opts.note(i) : "");
    };
    return mount(node, option);
  }

  /* Horizontal bars with the value written beside each bar.
     opts: labels, values, color, name, title(i), note(i) */
  function bars(node, opts) {
    var top = Math.max.apply(null, opts.values.concat([1]));
    return mount(node, {
      animationDuration: 500,
      textStyle: { fontFamily: FONT },
      grid: { left: 4, right: 58, top: 6, bottom: 6, containLabel: true },
      xAxis: { type: "value", min: 0, max: top, show: false },
      yAxis: {
        type: "category", data: opts.labels, inverse: true,
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: ink.text2, fontSize: 12, fontWeight: 650, margin: 10 }
      },
      tooltip: {
        trigger: "axis", confine: true, backgroundColor: "#ffffff", borderColor: ink.line, borderWidth: 1,
        padding: [10, 12], textStyle: { color: ink.text },
        extraCssText: "border-radius:12px;box-shadow:0 14px 34px -14px rgba(15,27,45,.4);",
        axisPointer: { type: "shadow", shadowStyle: { color: "rgba(15,27,45,.04)" } },
        formatter: function (params) {
          var i = params[0].dataIndex;
          return tip(opts.title ? opts.title(i) : opts.labels[i],
            [{ name: opts.name, color: opts.color, value: TX.num(opts.values[i]) }], opts.note ? opts.note(i) : "");
        }
      },
      series: [{
        type: "bar", name: opts.name, data: opts.values, barWidth: 16,
        itemStyle: { color: opts.color, borderRadius: [0, 4, 4, 0] },
        showBackground: true, backgroundStyle: { color: ink.grid, borderRadius: [0, 4, 4, 0] },
        label: {
          show: true, position: "right", distance: 8, color: ink.text, fontSize: 13, fontWeight: 700,
          formatter: function (p) { return TX.num(p.value); }
        },
        emphasis: { disabled: true }
      }]
    });
  }

  var timer = null;
  window.addEventListener("resize", function () {
    clearTimeout(timer);
    timer = setTimeout(function () { charts.forEach(function (c) { c.resize(); }); }, 120);
  });

  /* Remove a chart that is no longer on the page (the slide-in panel rebuilds its charts for every site). */
  function dispose(chart) {
    if (!chart) return;
    var at = charts.indexOf(chart);
    if (at >= 0) charts.splice(at, 1);
    chart.dispose();
  }

  TX.charts = { line: line, bars: bars, dispose: dispose };
})();
