// Proof app behaviour: small, additive, and never load-bearing. Every rule is
// enforced on the server; this only makes the phone pleasant.
(function () {
  // The clock on the card: the server measures; this just shows it ticking.
  var clock = document.querySelector("[data-clock]");
  if (clock) {
    var start = Date.now() - parseInt(clock.getAttribute("data-clock"), 10) * 1000;
    var tick = function () {
      var s = Math.floor((Date.now() - start) / 1000);
      var m = Math.floor(s / 60);
      clock.textContent = m ? m + " min " + String(s % 60).padStart(2, "0") + " s" : s + " s";
    };
    tick();
    setInterval(tick, 1000);
  }

  // The reject sheet.
  var sheet = document.getElementById("reject-sheet");
  document.querySelectorAll("[data-open-sheet]").forEach(function (b) {
    b.addEventListener("click", function () {
      var decision = b.getAttribute("data-open-sheet");
      sheet.querySelector("input[type=hidden][name$=decision]").value = decision;
      sheet.querySelector("[data-sheet-title]").textContent = decision === "escalate" ? "Why escalate?" : "Why reject?";
      sheet.classList.add("open");
      sheet.querySelector("input[type=radio]").focus();
      if (sheet.update) sheet.update();
    });
  });
  document.querySelectorAll("[data-close-sheet]").forEach(function (b) {
    b.addEventListener("click", function () { sheet.classList.remove("open"); });
  });
  if (sheet) {
    var submit = sheet.querySelector("[data-sheet-submit]");
    var radios = sheet.querySelectorAll("input[type=radio]");
    var update = function () {
      var picked = sheet.querySelector("input[type=radio]:checked");
      var hidden = sheet.querySelector("input[type=hidden][name$=decision]");
      var verb = hidden && hidden.value === "escalate" ? "Escalate" : "Reject";
      submit.textContent = picked ? verb + ": " + picked.getAttribute("data-text").toLowerCase() : "Pick a reason";
      submit.disabled = !picked; // the server refuses a missing chip too
    };
    sheet.update = update;
    radios.forEach(function (r) { r.addEventListener("change", update); });
    update();
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") sheet.classList.remove("open"); });
  }

  // Approve: the button wakes up only when both steps are done (the server checks
  // again). "No" on the spot check turns the form into a reject with the matching chip.
  var form = document.querySelector("[data-approve-form]");
  if (form) {
    var btn = form.querySelector("[data-approve-submit]");
    var line = form.querySelector("textarea[name=opening_line]");
    var count = form.querySelector("[data-count]");
    var decisionField = form.querySelector("input[type=hidden][name$=decision]");
    var reasonField = form.querySelector("[data-reason]");
    var check = function () {
      var hasLine = line.value.trim().length > 0;
      var spot = form.querySelector("input[name=spot_check_supported]:checked");
      var needSpot = form.getAttribute("data-needs-spot") === "1";
      var saysNo = spot && spot.value === "no";
      btn.disabled = saysNo ? false : !(hasLine && (!needSpot || spot));
      if (count) count.textContent = line.value.length + " / 160";
      decisionField.value = saysNo ? "reject" : "approve";
      reasonField.value = saysNo ? "unsourced_number" : "";
      btn.textContent = saysNo ? "Reject: a number with no source" : "Approve";
      btn.className = saysNo ? "btn secondary" : "btn primary";
    };
    form.addEventListener("input", check);
    form.addEventListener("change", check);
    check();
  }

  // Long scripts fold; one tap opens them.
  document.querySelectorAll("[data-unfold]").forEach(function (b) {
    b.addEventListener("click", function () {
      document.getElementById(b.getAttribute("data-unfold")).classList.add("open");
      b.remove();
    });
  });
})();
