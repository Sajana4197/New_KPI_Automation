/* Settings: one section at a time, chosen with the tabs (the address keeps it, e.g. #users) */
(function () {
  "use strict";
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".set-tab[data-tab]"));
  var panels = Array.prototype.slice.call(document.querySelectorAll(".set-panel[data-panel]"));
  if (!tabs.length || !panels.length) return;
  var ALIAS = { "add-user": "users", "users-list": "users" };

  function keyFromHash() {
    var k = (window.location.hash || "").replace("#", "");
    k = ALIAS[k] || k;
    return panels.some(function (p) { return p.dataset.panel === k; }) ? k : "rules";
  }
  function show(key) {
    tabs.forEach(function (t) {
      var on = t.dataset.tab === key;
      t.classList.toggle("on", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    panels.forEach(function (p) { p.classList.toggle("on", p.dataset.panel === key); });
  }
  document.body.classList.add("set-tabs-ready");
  show(keyFromHash());
  window.scrollTo(0, 0);       /* the browser jumped to the section before it was shown; start at the top */

  document.addEventListener("click", function (ev) {
    var link = ev.target.closest(".set-tab[data-tab], [data-tab-link]");
    if (!link || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
    ev.preventDefault();
    var key = link.dataset.tab || link.dataset.tabLink;
    history.replaceState(null, "", window.location.pathname + window.location.search + "#" + key);
    show(key);
    if (link.dataset.tabLink) document.getElementById("set-tabs").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  window.addEventListener("hashchange", function () { show(keyFromHash()); });
})();

/* Settings: filter the list of users by name, role and status */
(function () {
  "use strict";
  var bar = document.getElementById("user-filters");
  if (!bar) return;
  var rows = Array.prototype.slice.call(document.querySelectorAll("#users .user-row"));
  var q = document.getElementById("uf-q"), status = document.getElementById("uf-status");
  var chips = Array.prototype.slice.call(document.querySelectorAll("#uf-role .chip"));
  var count = document.getElementById("uf-count"), empty = document.getElementById("uf-empty");
  var role = "";

  function apply() {
    var text = q.value.trim().toLowerCase(), want = status.value, shown = 0;
    rows.forEach(function (row) {
      var ok = (!text || row.dataset.search.indexOf(text) >= 0) &&
               (!role || row.dataset.role === role) &&
               (!want || (" " + row.dataset.status + " ").indexOf(" " + want + " ") >= 0);
      row.classList.toggle("hide", !ok);
      if (ok) shown++;
    });
    chips.forEach(function (c) { c.classList.toggle("on", c.dataset.role === role); });
    count.textContent = shown === rows.length
      ? "Showing all " + rows.length + (rows.length === 1 ? " user" : " users")
      : "Showing " + shown + " of " + rows.length + " users";
    empty.classList.toggle("hide", shown > 0);
  }

  q.addEventListener("input", apply);
  status.addEventListener("change", apply);
  chips.forEach(function (c) { c.addEventListener("click", function () { role = c.dataset.role; apply(); }); });
  document.getElementById("uf-clear").addEventListener("click", function () {
    q.value = ""; status.value = ""; role = ""; apply();
  });
  apply();
})();
