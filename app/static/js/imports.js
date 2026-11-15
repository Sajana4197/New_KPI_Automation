/* Imports page: file drop zone and the delete-a-day confirmation */
(function () {
  "use strict";
  var TX = window.TX, el = TX.el;

  var input = document.getElementById("upload-input");
  if (input) {
    var drop = document.getElementById("drop"), list = document.getElementById("drop-files");
    var foot = document.getElementById("upload-foot");
    var show = function () {
      list.textContent = "";
      Array.prototype.forEach.call(input.files, function (f) {
        list.appendChild(el("li", {}, TX.icon("sheet", "sm"), " " + f.name + " ",
          el("span", { class: "muted", text: "(" + TX.num(f.size / 1048576, 1) + " MB)" })));
      });
      foot.classList.toggle("hide", !input.files.length);
    };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (name) {
      drop.addEventListener(name, function (ev) { ev.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      drop.addEventListener(name, function (ev) { ev.preventDefault(); drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (ev) {
      var files = ev.dataTransfer && ev.dataTransfer.files;
      if (!files || !files.length) return;
      var keep = new DataTransfer();
      Array.prototype.forEach.call(files, function (f) { if (/\.(xlsx|xlsm|xls)$/i.test(f.name)) keep.items.add(f); });
      if (!keep.files.length) { TX.toast("warn", "Only Excel files (.xlsx, .xls) can be imported."); return; }
      input.files = keep.files;
      show();
    });
    document.getElementById("upload-clear").addEventListener("click", function () { input.value = ""; show(); });
  }

  var dialog = document.getElementById("delete-dialog");
  if (dialog) {
    var confirm = document.getElementById("delete-confirm"), go = document.getElementById("delete-go");
    var target = "";
    document.addEventListener("click", function (ev) {
      var button = ev.target.closest("[data-delete]");
      if (!button) return;
      target = button.dataset.delete;
      document.getElementById("delete-date").value = target;
      document.getElementById("delete-label").textContent = button.dataset.deleteLabel;
      document.getElementById("delete-rows").textContent = button.dataset.deleteRows;
      document.getElementById("delete-echo").textContent = target;
      confirm.value = "";
      go.disabled = true;
      dialog.showModal();
      confirm.focus();
    });
    confirm.addEventListener("input", function () { go.disabled = confirm.value.trim() !== target; });
  }
})();
