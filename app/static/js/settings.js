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
