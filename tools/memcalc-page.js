/* Page code for tools/fine-tuning-memory-calculator.html. The formulas live in
 * memcalc.js (window.MemCalc); this file only reads the form, writes the
 * results and keeps the URL in step. No inline styles: bars use width-bucket
 * classes from tools.css. */
(function () {
  "use strict";
  var M = window.MemCalc;
  var root = document.querySelector("[data-memcalc]");
  if (!M || !root) return;

  var $ = function (id) { return document.getElementById(id); };
  var form = $("mc-form");
  var NUM = ["L", "h", "heads", "kvHeads", "headDim", "ffn", "vocab", "experts", "topK", "expertFfn", "r", "seq", "batch"];
  var ARCH = ["L", "h", "heads", "kvHeads", "headDim", "ffn", "vocab", "experts", "topK", "expertFfn", "tied", "moe"];
  var state = M.fromQuery(window.location.search);
  (function () {   // a shared link may carry a combination the form would not allow (QLoRA in fp32): show what is computed
    var n = M.normalize(state).x;
    state.prec = n.prec;
    state.method = n.method;
    state.targets = n.targets;
  })();
  var shown = {};   // the text last written to the billions fields, to tell a user edit from a round-off

  function fmtGiB(b) {
    var g = b / M.GiB;
    return g >= 100 ? g.toFixed(0) : g >= 10 ? g.toFixed(1) : g >= 1 ? g.toFixed(2) : g.toFixed(3);
  }
  function fmtGB(b) {
    var g = b / M.GB;
    return g >= 10 ? g.toFixed(0) : g.toFixed(1);
  }
  function fmtBillions(p) {
    var b = p / 1e9;
    return String(Number(b >= 10 ? b.toFixed(2) : b >= 1 ? b.toFixed(3) : b.toFixed(4)));
  }
  function fmtCount(n) { return Math.round(n).toLocaleString("en-US"); }

  function writeForm() {
    $("mc-preset").value = M.hasPreset(state.preset) || state.preset === "custom" ? state.preset : "custom";
    shown.params = fmtBillions(state.params);
    $("mc-params").value = shown.params;
    NUM.forEach(function (k) {
      var el = form.elements[k];
      if (el && state[k] !== undefined) { el.value = state[k]; el.removeAttribute("aria-invalid"); }
    });
    ["tied", "moe", "dq", "kbit", "gc"].forEach(function (k) { form.elements[k].checked = !!state[k]; });
    form.querySelectorAll('input[name="method"]').forEach(function (el) { el.checked = el.value === state.method; });
    form.querySelectorAll('input[name="hw"]').forEach(function (el) { el.checked = el.value === state.hw; });
    $("mc-prec").value = state.prec;
    $("mc-opt").value = state.opt;
    form.querySelectorAll('input[name="targets"]').forEach(function (el) { el.checked = state.targets.indexOf(el.value) >= 0; });
    syncVisibility();
  }

  function syncVisibility() {
    var lora = state.method !== "full";
    $("mc-r-field").hidden = !lora;
    $("mc-targets").hidden = !lora;
    $("mc-targets-help").hidden = !lora || !state.moe;
    $("mc-dq-row").hidden = state.method !== "qlora";
    $("mc-kbit-row").hidden = !(state.prec === "nf4" || state.prec === "int8");
    $("mc-moe-fields").hidden = !state.moe;
    state.active = M.activeFromArch(state);
    $("mc-active").textContent = state.moe ? fmtBillions(state.active) : "same as total (dense)";
    var allowed = { full: ["bf16", "fp16", "fp32", "mixed"], lora: ["bf16", "fp16", "fp32", "int8"], qlora: ["nf4"] }[state.method];
    Array.prototype.forEach.call($("mc-prec").options, function (o) { o.disabled = allowed.indexOf(o.value) < 0; });
    var implied = M.paramsFromArch(state);
    $("mc-arch-note").textContent = "This architecture implies " + fmtBillions(implied) + "B parameters" +
      (state.moe ? ", " + fmtBillions(M.activeFromArch(state)) + "B active per token." : ".");
  }

  function readNumber(el) {
    var v = Number(el.value);
    var ok = el.value !== "" && isFinite(v) && v > 0;
    if (ok) el.removeAttribute("aria-invalid"); else el.setAttribute("aria-invalid", "true");
    return ok ? v : null;
  }

  function readForm(changed) {
    if (changed === "preset") {
      var key = $("mc-preset").value;
      if (key !== "custom") {
        var keep = { method: state.method, prec: state.prec, dq: state.dq, r: state.r, targets: state.targets,
                     opt: state.opt, seq: state.seq, batch: state.batch, gc: state.gc, hw: state.hw };
        state = Object.assign(M.presetInput(key), keep);
      } else state.preset = "custom";
      writeForm();
      return;
    }
    NUM.forEach(function (k) {
      var el = form.elements[k];
      if (!el) return;
      var v = readNumber(el);
      if (v !== null) state[k] = Math.round(v);
    });
    ["params"].forEach(function (k) {
      var el = $("mc-" + k);
      if (el.value === shown[k]) return;              // unchanged: keep the exact count
      var v = readNumber(el);
      if (v !== null) { state[k] = Math.round(v * 1e9); shown[k] = el.value; }
    });
    ["tied", "moe", "dq", "kbit", "gc"].forEach(function (k) { state[k] = form.elements[k].checked; });
    var m = form.querySelector('input[name="method"]:checked');
    if (m) state.method = m.value;
    var hw = form.querySelector('input[name="hw"]:checked');
    if (hw) state.hw = hw.value;
    state.prec = $("mc-prec").value;
    state.opt = $("mc-opt").value;
    state.targets = Array.prototype.filter.call(form.querySelectorAll('input[name="targets"]'), function (el) { return el.checked; })
      .map(function (el) { return el.value; });
    if (ARCH.indexOf(changed) >= 0 || changed === "params") {
      state.preset = "custom";
      $("mc-preset").value = "custom";
    }
    // Apply the method/precision rules (QLoRA means NF4, full means no 8/4-bit) and show the result.
    var n = M.normalize(state).x;
    state.prec = n.prec;
    state.method = n.method;
    state.targets = n.targets;
    form.querySelectorAll('input[name="targets"]').forEach(function (el) { el.checked = state.targets.indexOf(el.value) >= 0; });
    $("mc-prec").value = state.prec;
    form.querySelectorAll('input[name="method"]').forEach(function (el) { el.checked = el.value === state.method; });
    syncVisibility();
  }

  function bucket(share) {
    return Math.max(0, Math.min(100, Math.round(share * 50) * 2));
  }

  function row(tbody, label, bytes, total, cls, help) {
    var tr = document.createElement("tr");
    if (cls) tr.className = cls;
    var th = document.createElement("th");
    th.scope = "row";
    th.textContent = label;
    if (help) {
      var small = document.createElement("span");
      small.className = "field-help";
      small.textContent = " " + help;
      th.appendChild(document.createElement("br"));
      th.appendChild(small);
    }
    if (total) {
      var bar = document.createElement("span");
      bar.className = "bar";
      bar.setAttribute("aria-hidden", "true");
      var fill = document.createElement("span");
      fill.className = "bar__fill bar__fill--w-" + bucket(bytes / total);
      bar.appendChild(fill);
      th.appendChild(bar);
    }
    var td = document.createElement("td");
    td.className = "num";
    td.textContent = fmtGiB(bytes);
    if (total) td.setAttribute("aria-label", fmtGiB(bytes) + " GiB, " + Math.round(100 * bytes / total) + "% of the peak");
    tr.appendChild(th);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  function li(list, text) {
    var item = document.createElement("li");
    item.textContent = text;
    list.appendChild(item);
  }

  /** The levers not already pulled, in Lesson 1.16's order. */
  function levers(x) {
    var out = ["shorten the rows", "lower the micro-batch"];
    if (!x.gc) out.push("turn on gradient checkpointing");
    if (x.method === "full") out.push("use LoRA or QLoRA");
    else if (x.method === "lora" && x.prec !== "int8" && !x.moe) out.push("use QLoRA");
    return out.slice(0, -1).join(", ") + " or " + out[out.length - 1] +
      (x.moe && (x.prec === "nf4" || x.prec === "int8") ? ". The experts are counted unquantized here; see the note below" : "");
  }

  function render() {
    var r = M.estimate(state), x = r.input;
    var pool = x.hw === "unified" ? "unified-memory machine" : "GPU";
    $("mc-peak").textContent = fmtGiB(r.peak) + " GiB (" + fmtGB(r.peak) + " GB)";
    $("mc-fit").textContent = r.trainTier
      ? "Fits a " + r.trainTier + " GB " + pool + " (the smallest of " + M.TIERS_GB.join(", ") + " GB that holds it)."
      : "More than 128 GB: " + levers(x) + ".";

    var tb = $("mc-rows");
    tb.textContent = "";
    var lora = x.method !== "full";
    var quant = x.prec === "nf4" || x.prec === "int8";
    row(tb, "Weights", r.weights, r.peak, null, lora ? (quant ? (x.kbit ? "frozen base; unquantized parts in fp32" : "frozen base; unquantized parts 16-bit") +
        (x.moe ? ", experts unquantized" : "") : "frozen base") : (x.prec === "mixed" ? "fp32 master + 16-bit copy" : null));
    row(tb, "Gradients", r.gradients, r.peak, null, lora ? "adapter only, fp32" : null);
    row(tb, "Optimizer state", r.optimizer, r.peak, null, lora ? "adapter only" : null);
    if (lora) row(tb, "Adapter weights", r.adapter, r.peak, null, fmtCount(r.trainable) + " parameters, fp32");
    row(tb, "Activations", r.activations, r.peak, null, (x.gc ? "layer inputs + one layer rebuilt in backward" : "kept for backward") +
        (r.activationBytes === 4 && x.prec !== "fp32" ? ", fp32 after the kbit prep" : ""));
    row(tb, "Logits and loss (fp32)", r.logits, r.peak, null, "batch × tokens × vocabulary");
    row(tb, "Overhead", r.overhead, r.peak, null, x.hw === "unified" ? "CUDA context, allocator, process and page cache" : "CUDA context and allocator");
    row(tb, "Training peak", r.peak, 0, "total");

    var lines = $("mc-lines");
    lines.textContent = "";
    li(lines, "Allocated by PyTorch at the peak: " + fmtGiB(r.allocated) + " GiB (what torch.cuda.max_memory_allocated() reports).");
    if (x.moe) li(lines, "Active per token: " + fmtBillions(r.active) + "B of " + fmtBillions(x.params) + "B (top " + x.topK + " of " +
       x.experts + " experts). The activations above follow the routed experts; the weights count all of them.");
    li(lines, "Trainable parameters: " + fmtCount(r.trainable) + (lora ? " (" + (100 * r.trainable / x.params).toFixed(2) + "% of the model)." : " (all of them)."));
    li(lines, "Evaluation at " + fmtCount(x.seq) + " tokens, batch 1: " + fmtGiB(r.inference) + " GiB (weights " +
       fmtGiB(r.inferenceWeights) + " + KV cache " + fmtGiB(r.kvCache) + " + overhead)" +
       (r.inferenceTier ? ", fits a " + r.inferenceTier + " GB " + pool + "." : ", more than 128 GB."));
    if (x.hw === "unified") {
      li(lines, "Loading: weights read to the CPU and then copied to the GPU briefly hold two copies, " + fmtGiB(r.loadPeak) +
         " GiB, plus up to " + fmtGiB(r.checkpointFile) + " GiB of page cache. Load straight to the device to avoid it.");
    } else {
      li(lines, "CPU RAM to load the checkpoint: about " + fmtGiB(r.checkpointFile) + " GiB.");
    }

    var notes = $("mc-notes");
    notes.textContent = "";
    r.notes.forEach(function (t) { li(notes, t); });

    // Screen readers hear one short line, not the rebuilt table.
    var summary = "Training peak " + fmtGiB(r.peak) + " GiB. " + (r.trainTier ? "Fits a " + r.trainTier + " GB " + pool + "." : "More than 128 GB.");
    if ($("mc-summary").textContent !== summary) $("mc-summary").textContent = summary;

    var q = "?" + M.toQuery(state);
    if (window.location.search !== q && window.history && window.history.replaceState) {
      window.history.replaceState(null, "", window.location.pathname + q + window.location.hash);
    }
  }

  function update(e) {
    var name = e && e.target ? e.target.name : null;
    readForm(name);
    render();
  }

  form.addEventListener("input", function (e) { if (e.target.type === "number") update(e); });
  form.addEventListener("change", update);
  form.addEventListener("submit", function (e) { e.preventDefault(); });

  $("mc-all-linear").addEventListener("click", function () {
    form.querySelectorAll('input[name="targets"]').forEach(function (el) { el.checked = true; });
    update(null);
  });
  $("mc-arch-toggle").addEventListener("click", function () {
    var box = $("mc-arch"), open = box.hidden;
    box.hidden = !open;
    this.setAttribute("aria-expanded", open ? "true" : "false");
    this.textContent = open ? "Hide the architecture" : "Show the architecture";
  });
  $("mc-reset").addEventListener("click", function () {
    state = M.presetInput(M.DEFAULTS.preset);
    writeForm();
    render();
    $("mc-status").textContent = "Reset to the defaults.";
  });
  $("mc-copy").addEventListener("click", function () {
    var url = window.location.href, status = $("mc-status");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { status.textContent = "Link copied."; },
        function () { status.textContent = "Copy this link: " + url; });
    } else {
      status.textContent = "Copy this link: " + url;
    }
  });

  writeForm();
  render();
})();
