/* Page code for tools/is-the-lift-real.html. The statistics live in
 * liftstats.js (window.LiftStats); this file reads the form, writes the
 * results and keeps the URL in step. */
(function () {
  "use strict";
  var S = window.LiftStats;
  var root = document.querySelector("[data-liftcalc]");
  if (!S || !root) return;

  var $ = function (id) { return document.getElementById(id); };
  var form = $("lc-form");
  var EXAMPLE = { n: 50, base: 22, tuned: 26, tunedOnly: 6, baseOnly: 2, gapPts: 5 };
  var FIELDS = { n: "lc-n", base: "lc-base", tuned: "lc-tuned", tunedOnly: "lc-to", baseOnly: "lc-bo", gapPts: "lc-gap" };
  var VERDICT = {
    lift: ["Real lift", "The whole 95% interval for the change is above zero: the fine-tune is better on rows like these."],
    drop: ["Real drop", "The whole 95% interval for the change is below zero: the fine-tune is worse on rows like these."],
    noise: ["Within noise", "The 95% interval for the change includes zero: this many rows cannot tell a gain from a loss. That is not the same as no effect."],
    few: ["Too few rows", "Under 5 rows no interval means much; BrewSLM gives no verdict here either."]
  };

  function pct(p) { return (100 * p).toFixed(1) + "%"; }
  function pts(x) {
    var v = 100 * x, s = Math.abs(v).toFixed(1);
    return (s === "0.0" ? "" : v < 0 ? "−" : "+") + s;
  }
  function range(ci, unit) { return pts(ci[0]) + " to " + pts(ci[1]) + (unit || ""); }
  function prange(ci) { return pct(ci[0]) + " to " + pct(ci[1]); }
  function pval(p) { return p < 0.001 ? "< 0.001" : p.toFixed(3); }
  function count(n) { return Math.round(n).toLocaleString("en-US"); }

  function readState() {
    var s = {};
    Object.keys(FIELDS).forEach(function (k) {
      var el = $(FIELDS[k]), v = el.value.trim();
      s[k] = v === "" ? undefined : Number(v);
      var bad = v !== "" && !(isFinite(s[k]) && s[k] >= 0);
      if (bad) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid");
    });
    return s;
  }

  function writeState(s) {
    Object.keys(FIELDS).forEach(function (k) {
      $(FIELDS[k]).value = s[k] === undefined || s[k] === null ? "" : s[k];
      $(FIELDS[k]).removeAttribute("aria-invalid");
    });
  }

  function addRow(tbody, label, value, help, ci) {
    var tr = document.createElement("tr");
    var th = document.createElement("th");
    th.scope = "row";
    th.textContent = label;
    if (help) {
      th.appendChild(document.createElement("br"));
      var small = document.createElement("span");
      small.className = "field-help";
      small.textContent = help;
      th.appendChild(small);
    }
    var td = document.createElement("td");
    td.className = "num";
    td.textContent = value;
    if (ci) {
      var sub = document.createElement("span");
      sub.className = "ci";
      sub.textContent = ci;
      td.appendChild(sub);
    }
    tr.appendChild(th);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  function li(list, text) {
    var item = document.createElement("li");
    item.textContent = text;
    list.appendChild(item);
  }

  function render() {
    var s = readState();
    var verdict = $("lc-verdict"), sentence = $("lc-sentence"), tb = $("lc-rows"), lines = $("lc-lines");
    tb.textContent = "";
    lines.textContent = "";
    var hasPair = s.tunedOnly !== undefined || s.baseOnly !== undefined;
    var input = { n: s.n, base: s.base, tuned: s.tuned, gap: (s.gapPts || 5) / 100 };
    if (s.tunedOnly !== undefined && s.baseOnly !== undefined) {
      input.tunedOnly = s.tunedOnly;
      input.baseOnly = s.baseOnly;
    }
    var r = s.n === undefined || s.base === undefined || s.tuned === undefined
      ? { error: "Enter the rows and both counts." } : S.analyze(input);
    if (r.error) {
      verdict.className = "verdict";
      verdict.textContent = "No verdict yet";
      sentence.textContent = r.error;
      sentence.className = "result-sub tool-error";
    } else {
      verdict.className = "verdict verdict--" + r.verdict;
      verdict.textContent = VERDICT[r.verdict][0];
      sentence.className = "result-sub";
      sentence.textContent = VERDICT[r.verdict][1] + (r.paired ? "" :
        " Without the row-by-row counts this uses the wider, unpaired interval.");
      addRow(tb, "Base model", pct(r.pBase), count(r.base) + " of " + count(r.n) + " rows, Wilson interval", prange(r.ciBase));
      addRow(tb, "Fine-tuned", pct(r.pTuned), count(r.tuned) + " of " + count(r.n) + " rows, Wilson interval", prange(r.ciTuned));
      addRow(tb, "Change", pts(r.diff) + " points",
             r.paired ? "paired interval, Newcombe" : "unpaired interval, Newcombe", range(r.ci));
      if (r.paired) {
        addRow(tb, "Exact McNemar p", pval(r.mcnemarP), count(r.tunedOnly) + " rows only the fine-tune got right, " + count(r.baseOnly) + " only the base");
        if (r.tInterval) addRow(tb, "Paired t-interval", range(r.tInterval.ci, " points"), "what BrewSLM's lift check reports");
        addRow(tb, "Rows both right / both wrong", count(r.both) + " / " + count(r.neither), "these add no evidence either way");
      } else {
        addRow(tb, "Change, if the rows were paired", "add the row-by-row counts", "usually a much narrower interval");
      }
      var gapPts = (r.gap * 100).toFixed(r.gap * 100 % 1 ? 1 : 0);
      if (r.paired && r.pairedGap !== null) {
        li(lines, "This test set can reliably show a change of about " + (100 * r.pairedGap).toFixed(1) +
           " points (paired, 95% confidence, 80% power). A " + gapPts + "-point change needs about " +
           count(r.pairedRows) + " rows with this spread of per-row changes.");
      } else if (r.paired) {
        li(lines, "No row changed between the two models, so there is no spread of per-row changes to size from; the unpaired figure below is the bound.");
      }
      li(lines, "Unpaired, it can show about " + (100 * r.unpairedGap).toFixed(1) + " points; a " + gapPts +
         "-point change needs about " + count(r.unpairedRows) + " rows per model.");
      if (hasPair && !r.paired) li(lines, "Enter both row-by-row counts to pair the rows.");
    }
    var q = "?" + S.toQuery(s);
    if (window.location.search !== q && window.history && window.history.replaceState) {
      window.history.replaceState(null, "", window.location.pathname + q + window.location.hash);
    }
  }

  form.addEventListener("input", render);
  form.addEventListener("submit", function (e) { e.preventDefault(); });
  $("lc-reset").addEventListener("click", function () {
    writeState(EXAMPLE);
    render();
    $("lc-status").textContent = "Reset to the example: the 50-row case from the small-test-sets box.";
  });
  $("lc-copy").addEventListener("click", function () {
    var url = window.location.href, status = $("lc-status");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { status.textContent = "Link copied."; },
        function () { status.textContent = "Copy this link: " + url; });
    } else {
      status.textContent = "Copy this link: " + url;
    }
  });

  var fromUrl = S.fromQuery(window.location.search);
  writeState(fromUrl.n !== undefined ? fromUrl : EXAMPLE);
  render();
})();
