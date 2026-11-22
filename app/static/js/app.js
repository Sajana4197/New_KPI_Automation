/* TX KPI Monitor - shared page behaviour */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  function icon(name, cls) {
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS(NS, "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  /* el("td", {class: "num", text: "12"}, child, child...) - text always goes in as text, never as HTML */
  function el(tag, props) {
    var node = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (key) {
      var value = props[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else if (key === "on") Object.keys(value).forEach(function (ev) { node.addEventListener(ev, value[ev]); });
      else if (key === "data") Object.keys(value).forEach(function (k) { node.dataset[k] = value[k]; });
      else if (key in node && key !== "list" && key !== "form") node[key] = value;
      else node.setAttribute(key, value);
    });
    for (var i = 2; i < arguments.length; i++) {
      var child = arguments[i];
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === "string" || typeof child === "number"
        ? document.createTextNode(String(child)) : child);
    }
    return node;
  }

  var TOAST_ICONS = { good: "check-circle", bad: "x-circle", warn: "alert", info: "info" };
  function toast(kind, message, opts) {
    opts = opts || {};
    var box = document.getElementById("toasts");
    if (!box) return null;
    var node = el("div", { class: "toast " + kind, role: "status" }, icon(TOAST_ICONS[kind] || "info"),
      el("div", {}, message, opts.link ? " " : null, opts.link ? el("a", { href: opts.link.href, text: opts.link.text }) : null));
    box.appendChild(node);
    if (!opts.sticky) setTimeout(function () { node.remove(); }, opts.ms || 4200);
    return node;
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "Accept": "application/json" },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.error || "The server could not do that (" + res.status + ").");
        return data;
      });
    });
  }

  function pageData() {
    var node = document.getElementById("page-data");
    try { return node ? JSON.parse(node.textContent) : null; } catch (e) { return null; }
  }

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  /* The server sends plain local timestamps ("2026-09-09T08:00:00"); read them without any time-zone shift. */
  function parts(iso) {
    var m = /^(\d{4})-(\d\d)-(\d\d)(?:T(\d\d):(\d\d))?/.exec(iso || "");
    if (!m) return null;
    var date = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return { y: +m[1], mo: +m[2], d: +m[3], h: m[4] === undefined ? null : +m[4], dow: date.getUTCDay() };
  }
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function shortDate(iso) { var p = parts(iso); return p ? p.d + " " + MONTHS[p.mo - 1] : ""; }
  function dayDate(iso) { var p = parts(iso); return p ? DAYS[p.dow] + " " + p.d + " " + MONTHS[p.mo - 1] : ""; }
  function hourLabel(iso) { var p = parts(iso); return p && p.h !== null ? two(p.h) + ":00" : ""; }
  function hourRange(iso) {
    var p = parts(iso);
    if (!p || p.h === null) return "";
    return dayDate(iso) + ", " + two(p.h) + ":00–" + two((p.h + 1) % 24) + ":00";
  }
  function num(n, digits) {
    if (n === null || n === undefined) return "–";
    return Number(n).toLocaleString("en-GB", { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0 });
  }

  window.TX = {
    csrf: csrf, icon: icon, el: el, toast: toast, post: post, pageData: pageData, num: num,
    shortDate: shortDate, dayDate: dayDate, hourLabel: hourLabel, hourRange: hourRange,
    colors: { PD: "#2a78d6", JITTER: "#eb6834", RTT: "#1baf7a" },
    ink: { text: "#0f1b2d", text2: "#4a5a70", muted: "#66758a", line: "#e3e8f0", grid: "#eef2f7", surface: "#ffffff" }
  };

  document.addEventListener("click", function (ev) {
    var close = ev.target.closest("[data-dismiss]");
    if (close) { close.closest(".banner").remove(); return; }
    var opener = ev.target.closest("[data-open]");
    if (opener) {
      var dialog = document.getElementById(opener.dataset.open);
      if (dialog && dialog.showModal) dialog.showModal();
      return;
    }
    if (ev.target.matches && ev.target.matches("dialog.modal[open]")) { ev.target.close(); return; }   /* click outside a dialog */
    var toggler = ev.target.closest("[data-toggle]");          /* show or hide a panel, e.g. a user's edit form */
    if (toggler) {
      var panel = document.getElementById(toggler.dataset.toggle);
      if (panel) {
        panel.hidden = !panel.hidden;
        toggler.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
        toggler.classList.toggle("on", !panel.hidden);
        if (!panel.hidden) { var first = panel.querySelector("input:not([type=hidden]), select"); if (first) first.focus(); }
      }
      return;
    }
    var closer = ev.target.closest("[data-close]");
    if (closer) { var dlg = closer.closest("dialog"); if (dlg) dlg.close(); }
  });

  /* Collapse or expand the side menu. The choice is kept in a cookie so the server draws it right away. */
  var shell = document.getElementById("shell");
  var sideToggle = document.getElementById("side-toggle");
  if (shell && sideToggle) {
    sideToggle.addEventListener("click", function () {
      var closed = shell.classList.toggle("is-collapsed");
      var label = closed ? "Expand the menu" : "Collapse the menu";
      sideToggle.setAttribute("aria-expanded", closed ? "false" : "true");
      sideToggle.setAttribute("aria-label", label);
      sideToggle.title = label;
      document.cookie = "tx_side=" + (closed ? "c" : "o") + "; path=/; max-age=31536000; SameSite=Lax";
      setTimeout(function () { window.dispatchEvent(new Event("resize")); }, 240);   /* charts refit */
    });
  }

  document.addEventListener("change", function (ev) {
    if (ev.target.matches("[data-autosubmit]") && ev.target.form) ev.target.form.submit();
  });

  /* Forms that take a moment: stop double clicks and say what is happening. */
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) { ev.preventDefault(); return; }
    var button = form.querySelector("button[data-busy]");
    if (button && !button.disabled) {
      setTimeout(function () {
        button.disabled = true;
        var label = button.querySelector("span");
        if (label) label.textContent = button.dataset.busy;
      }, 0);
    }
  });

  /* Headline numbers count up once when the page opens. */
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  Array.prototype.forEach.call(document.querySelectorAll("[data-count]"), function (node) {
    var end = +node.dataset.count, t0 = null;
    if (still || !isFinite(end) || end <= 0) return;
    var step = function (now) {
      if (t0 === null) t0 = now;
      var k = Math.min((now - t0) / 900, 1);
      node.textContent = Math.round(end * (1 - Math.pow(1 - k, 3))).toLocaleString("en-US");
      if (k < 1) window.requestAnimationFrame(step);
    };
    node.textContent = "0";
    window.requestAnimationFrame(step);
  });

  /* Password boxes: an eye button shows or hides the text, and a meter rates a new password. */
  document.addEventListener("click", function (ev) {
    var eye = ev.target.closest("[data-pw-toggle]");
    if (!eye) return;
    var input = eye.parentNode.querySelector("input");
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    eye.classList.toggle("on", show);
    eye.setAttribute("aria-label", show ? "Hide password" : "Show password");
    eye.title = show ? "Hide password" : "Show password";
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-pw-meter]"), function (input) {
    var meter = document.getElementById(input.dataset.pwMeter);
    if (!meter) return;
    var note = meter.querySelector("small"), first = note.textContent;
    var LABELS = ["Too short", "Weak", "Fair", "Good", "Strong"];
    input.addEventListener("input", function () {
      var v = input.value, score = 0;
      if (v.length >= 8) {
        score = 1;
        if (v.length >= 12) score++;
        if (/[a-z]/.test(v) && /[A-Z]/.test(v)) score++;
        if (/\d/.test(v) && /[^A-Za-z0-9]/.test(v)) score++;
        else if (/\d/.test(v) || /[^A-Za-z0-9]/.test(v)) score += 0.5;
        score = Math.min(4, Math.floor(score));
        if (score < 1) score = 1;
      }
      meter.dataset.score = v ? String(score) : "";
      note.textContent = v ? LABELS[score] + (score < 3 ? " · longer, with capitals, numbers and symbols, is stronger" : "") : first;
    });
  });

  /* Pages that show imported data ask the server every few seconds whether anything new arrived. */
  var watch = document.querySelector("[data-watch]");
  if (watch) {
    var mode = watch.dataset.watch;
    var seen = watch.dataset.seq ? +watch.dataset.seq : null;
    var seenServer = watch.dataset.server ? +watch.dataset.server : null;
    var live = document.getElementById("watch-state");
    var told = false;
    var tick = function () {
      if (document.hidden) return;
      fetch("/api/status", { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (res) { if (!res.ok) throw new Error(String(res.status)); return res.json(); })
        .then(function (s) {
          if (live) {
            live.className = "live" + (s.busy || s.waiting ? " busy" : "");
            live.textContent = s.busy ? "Importing a file…" : s.waiting
              ? s.waiting + " file" + (s.waiting === 1 ? "" : "s") + " arriving…" : "Watching the inbox folder";
          }
          if (mode === "reload" && seenServer !== null && s.server && s.server.seq !== seenServer && !s.server.busy) {
            var chosen = document.querySelector("input[type=file]");
            if (!document.querySelector("dialog[open]") && !(chosen && chosen.files.length)) { window.location.reload(); return; }
          }
          if (seen === null) { seen = s.seq; return; }
          if (s.seq !== seen && !s.busy && !s.waiting) {
            var picked = document.querySelector("input[type=file]");
            var inUse = document.querySelector("dialog[open]") || (picked && picked.files.length);
            if (mode === "reload" && !inUse) { window.location.reload(); return; }
            if (!told) {
              told = true;
              toast("info", "New KPI data was imported.", { sticky: true, link: { href: window.location.pathname, text: "Refresh" } });
            }
          }
        })
        .catch(function () {
          if (live) { live.className = "live off"; live.textContent = "Not connected to the server"; }
        });
    };
    setInterval(tick, 5000);
    tick();
  }
})();
