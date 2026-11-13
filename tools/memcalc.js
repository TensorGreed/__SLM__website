/* Fine-tuning memory calculator: the formulas (no page code here).
 *
 * Loaded by tools/fine-tuning-memory-calculator.html as window.MemCalc and by
 * _build/run_tools.cjs under node (module.exports), so the page and the
 * validation tests run the same code. No dependencies.
 *
 * Sources for each term are on the page, under "The formulas". In short:
 *   weights + gradients + optimizer state: the memory table of Academy
 *     Lesson 1.15 (ZeRO, arXiv:1910.02054; QLoRA, arXiv:2305.14314);
 *   LoRA trainable parameters: r * (d_in + d_out) per adapted matrix
 *     (LoRA, arXiv:2106.09685);
 *   activations: bytes autograd saves for the backward pass, counted with
 *     torch.autograd.graph.saved_tensors_hooks on Hugging Face Llama-style
 *     decoder layers (RMSNorm, SDPA attention, SwiGLU MLP; transformers 5.2,
 *     peft 0.21, CPU, 2026-11-13). The per-layer coefficients below fit
 *     those counts exactly; the method is the one in Lesson 1.15;
 *   logits: one fp32 copy saved for the loss, plus two fp32 gradient buffers
 *     while the loss backpropagates;
 *   overhead: a rule of thumb calibrated on one measured run.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.MemCalc = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var GiB = Math.pow(2, 30);
  var GB = 1e9;
  var TARGETS = ["q", "k", "v", "o", "gate", "up", "down"];
  var TIERS_GB = [8, 12, 16, 24, 48, 80, 128];

  // Generic size/shape presets (no vendor names). Parameter counts are the
  // ones these shapes imply (paramsFromArch).
  var PRESETS = {
    "135m": { label: "135M dense", L: 30, h: 576, heads: 9, kvHeads: 3, headDim: 64, ffn: 1536, vocab: 49152, tied: true, bias: false },
    "0.5b": { label: "0.5B dense", L: 24, h: 896, heads: 14, kvHeads: 2, headDim: 64, ffn: 4864, vocab: 151936, tied: true, bias: true },
    "1.5b": { label: "1.5B dense", L: 28, h: 1536, heads: 12, kvHeads: 2, headDim: 128, ffn: 8960, vocab: 151936, tied: true, bias: true },
    "3b": { label: "3B dense", L: 36, h: 2048, heads: 16, kvHeads: 2, headDim: 128, ffn: 11008, vocab: 151936, tied: true, bias: true },
    "7b": { label: "7B dense", L: 28, h: 3584, heads: 28, kvHeads: 4, headDim: 128, ffn: 18944, vocab: 152064, tied: false, bias: true },
    "14b": { label: "14B dense", L: 48, h: 5120, heads: 40, kvHeads: 8, headDim: 128, ffn: 13824, vocab: 152064, tied: false, bias: true },
    "32b": { label: "32B dense", L: 64, h: 5120, heads: 40, kvHeads: 8, headDim: 128, ffn: 27648, vocab: 152064, tied: false, bias: true },
    "30b-a3b": { label: "30B-A3B mixture of experts", L: 48, h: 2048, heads: 32, kvHeads: 4, headDim: 128, ffn: 6144,
                 vocab: 151936, tied: false, bias: false, qkNorm: true, moe: true, experts: 128, topK: 8, expertFfn: 768 }
  };

  var DEFAULTS = {
    preset: "1.5b", method: "lora", prec: "bf16", dq: true, r: 16, targets: ["q", "k", "v", "o"],
    opt: "adamw", seq: 2048, batch: 4, gc: true, hw: "discrete"
  };

  /** Total parameters an architecture implies (embeddings, attention, MLP or experts, norms). */
  function paramsFromArch(a) {
    var q = a.heads * a.headDim, kv = a.kvHeads * a.headDim, h = a.h;
    var attn = 2 * h * q + 2 * h * kv + (a.bias ? q + 2 * kv : 0) + (a.qkNorm ? 2 * a.headDim : 0);
    var mlp = a.moe ? a.experts * 3 * h * a.expertFfn + h * a.experts : 3 * h * a.ffn;
    return a.L * (attn + mlp + 2 * h) + a.vocab * h * (a.tied ? 1 : 2) + h;
  }

  /** Active parameters per token for a mixture of experts (top-k experts only). */
  function activeFromArch(a) {
    if (!a.moe) return paramsFromArch(a);
    var dense = Object.assign({}, a, { moe: false, ffn: a.topK * a.expertFfn });
    return paramsFromArch(dense) + a.L * a.h * a.experts;
  }

  function presetInput(key) {
    var p = PRESETS[key];
    var x = Object.assign({}, DEFAULTS, p, { preset: key });
    delete x.label;
    x.params = paramsFromArch(p);
    x.active = activeFromArch(p);
    return x;
  }

  /** Coerce a raw input into a consistent one; returns {x, notes}. */
  function normalize(raw) {
    var x = Object.assign({}, raw), notes = [];
    x.targets = (x.targets || []).filter(function (t) { return TARGETS.indexOf(t) >= 0; });
    if (x.method === "qlora") {
      if (x.prec !== "nf4") notes.push("QLoRA loads the base in 4-bit NF4, so the precision is NF4.");
      x.prec = "nf4";
    } else if (x.prec === "nf4") {
      x.method = x.method === "full" ? "full" : "qlora";
    }
    if (x.method === "full" && (x.prec === "int8" || x.prec === "nf4")) {
      notes.push("A full fine-tune cannot train 8-bit or 4-bit weights; this uses mixed precision instead.");
      x.prec = "mixed";
    }
    if (x.method !== "full" && x.prec === "mixed") x.prec = "bf16";   // a frozen base needs no fp32 master copy
    if (x.method !== "full" && x.targets.length === 0) {
      notes.push("No LoRA target modules picked; this adds q, k, v and o.");
      x.targets = ["q", "k", "v", "o"];
    }
    x.r = Math.max(1, Math.round(x.r || 1));
    x.seq = Math.max(1, Math.round(x.seq || 1));
    x.batch = Math.max(1, Math.round(x.batch || 1));
    return { x: x, notes: notes };
  }

  /** LoRA trainable parameters: r * (d_in + d_out) per adapted matrix, per layer
   *  (expert MLP matrices once per expert). */
  function loraParams(x) {
    var q = x.heads * x.headDim, kv = x.kvHeads * x.headDim, h = x.h;
    var ff = x.moe ? x.expertFfn : x.ffn, mult = x.moe ? x.experts : 1;
    var dims = { q: [h, q], k: [h, kv], v: [h, kv], o: [q, h], gate: [h, ff], up: [h, ff], down: [ff, h] };
    var per = 0;
    x.targets.forEach(function (t) {
      var m = (t === "gate" || t === "up" || t === "down") ? mult : 1;
      per += m * x.r * (dims[t][0] + dims[t][1]);
    });
    return x.L * per;
  }

  /** Bytes per token one decoder layer keeps for backward (no checkpointing).
   *  e = bytes per activation element (4 in fp32, 2 in bf16/fp16). */
  function layerBytesPerToken(x, e) {
    var h = x.h, q = x.heads * x.headDim, kv = x.kvHeads * x.headDim;
    var ff = x.moe ? x.topK * x.expertFfn : x.ffn;          // MLP width a token passes through
    var upcast = 2 * Math.max(0, 4 - e) * h;                 // the two RMSNorms compute in fp32
    var lse = 4 * x.heads;                                   // attention log-sum-exp, fp32
    var moeExtra = x.moe ? 4 * x.experts + e * h * x.topK : 0;   // router probabilities + per-expert copies
    if (x.method === "full") return e * (6 * h + 2 * q + 2 * kv + 4 * ff) + upcast + lse + moeExtra;
    var base = e * (2 * h + 2 * q + 2 * kv + 3 * ff) + upcast + lse + moeExtra;
    var T = x.targets, has = function (t) { return T.indexOf(t) >= 0; };
    var hIn = x.moe ? x.topK * h : h;                        // input width of the gate/up matrices a token reaches
    var extra = 4 * x.r * T.length;                          // the adapters' rank-r outputs, fp32
    if (e === 4) {
      // fp32 adapters read the same tensors the layer already keeps; each new input is kept once.
      if (has("q") || has("k") || has("v")) extra += 4 * h;
      if (has("gate") || has("up")) extra += 4 * hIn;
      if (has("down")) extra += 4 * ff;
    } else {
      // fp32 adapters on a 16-bit base: each adapted matrix keeps an fp32 copy of its input.
      T.forEach(function (t) {
        extra += 4 * ({ q: h, k: h, v: h, o: q, gate: hIn, up: hIn, down: ff })[t];
      });
    }
    return base + extra;
  }

  function weightBytes(x) {
    var P = x.params;
    switch (x.prec) {
      case "fp32": return 4 * P;
      case "mixed": return 6 * P;                             // fp32 master + 16-bit working copy
      case "bf16": case "fp16": return 2 * P;
      case "int8": case "nf4":
        // embeddings (and an untied output head) and norms stay 16-bit
        var unq = x.vocab * x.h * (x.tied ? 1 : 2) + (2 * x.L + 1) * x.h;
        var bitsPer = x.prec === "int8" ? 8 : (x.dq ? 4.127 : 4.5);
        return Math.max(0, P - unq) * bitsPer / 8 + 2 * unq;
    }
    throw new Error("unknown precision " + x.prec);
  }

  function estimate(raw) {
    var nn = normalize(raw), x = nn.x, notes = nn.notes.slice();
    var P = x.params, n = x.seq * x.batch;
    var e = x.prec === "fp32" ? 4 : 2;
    var r = { input: x, notes: notes, tokens: n };
    var optB = x.opt === "adamw" ? 8 : 2;                   // 8-bit AdamW: two 1-byte moments

    r.weights = weightBytes(x);
    if (x.method === "full") {
      r.trainable = P;
      var pure16 = x.prec === "bf16" || x.prec === "fp16";
      r.gradients = (pure16 ? 2 : 4) * P;
      // PyTorch AdamW keeps its moments in the parameters' dtype: bf16 on pure-16-bit weights.
      r.optimizer = (x.opt === "adamw" && pure16 ? 4 : optB) * P;
      r.adapter = 0;
    } else {
      var T = loraParams(x);
      r.trainable = T;
      r.adapter = 4 * T;                                     // peft keeps adapters in fp32
      r.gradients = 4 * T;
      r.optimizer = optB * T;
    }
    r.parameterStates = r.weights + r.gradients + r.optimizer + r.adapter;
    r.bytesPerParam = r.parameterStates / P;

    var layer = layerBytesPerToken(x, e);
    var kept = x.gc ? e * x.h : layer;                       // with checkpointing only each layer's input is kept
    var fixed = x.method === "full" ? e * 2 * x.h + 4 * x.h : 0;   // final norm + output-head input
    r.layerBytesPerToken = layer;
    r.recompute = x.gc ? n * layer : 0;                      // one layer rebuilt during backward
    r.activations = n * (x.L * kept + fixed) + r.recompute;
    r.logitsSaved = n * x.vocab * 4;                         // fp32 log-probabilities kept for the loss
    r.logits = n * x.vocab * 12;                             // + fp32 grads of log-probs and of logits
    // What Lesson 1.15's saved-tensor count measures: every tensor kept for backward after the forward pass.
    r.savedActivations = n * (x.L * kept + fixed) + r.logitsSaved;

    r.allocated = r.weights + r.gradients + r.optimizer + r.adapter + r.activations + r.logits;
    r.overhead = 0.10 * r.allocated + (x.hw === "unified" ? 7 : 1) * GiB;
    r.peak = r.allocated + r.overhead;

    // Evaluation / inference at the same length, batch 1: weights + KV cache (16-bit).
    var kvDim = x.kvHeads * x.headDim;
    var infWeights = x.method === "full" ? (x.prec === "fp32" ? 4 * P : 2 * P) : r.weights + 2 * (r.trainable || 0);
    r.kvCache = 2 * x.L * kvDim * 2 * x.seq;
    r.inference = (infWeights + r.kvCache) * 1.10 + (x.hw === "unified" ? 7 : 1) * GiB;
    r.inferenceWeights = infWeights;

    // Loading: weights read to CPU first, then copied to the device, can briefly hold two copies.
    r.checkpointFile = 2 * P;                                // a 16-bit checkpoint on disk
    r.loadPeak = 2 * r.checkpointFile;
    r.trainTier = tierFor(r.peak);
    r.inferenceTier = tierFor(r.inference);

    if (x.opt === "paged8" && x.hw === "discrete") notes.push("Paged 8-bit AdamW is the same size, but its state can move to CPU RAM when the GPU runs short.");
    if (x.opt === "paged8" && x.hw === "unified") notes.push("On unified memory paging has nowhere to go: CPU and GPU share one pool, so a paged optimizer saves nothing.");
    if (x.method === "full" && x.prec !== "fp32" && x.prec !== "mixed") notes.push("Pure 16-bit full fine-tuning is not the default; label it if you use it (Lesson 1.15).");
    if (Math.abs(paramsFromArch(x) - P) > 0.1 * P) notes.push("The parameter count differs from what the architecture implies by more than 10%, so treat the activation and adapter figures as rough.");
    return r;
  }

  function tierFor(bytes) {
    for (var i = 0; i < TIERS_GB.length; i++) if (bytes <= TIERS_GB[i] * GB) return TIERS_GB[i];
    return null;
  }

  // ── URL state ────────────────────────────────────────────────────────────
  // Short keys so a shared link stays readable.
  var KEYS = {
    preset: "preset", params: "p", active: "act", L: "l", h: "h", heads: "nh", kvHeads: "kv", headDim: "hd",
    ffn: "ff", vocab: "v", tied: "tied", bias: "bias", qkNorm: "qkn", moe: "moe", experts: "e", topK: "k",
    expertFfn: "eff", method: "m", prec: "prec", dq: "dq", r: "r", targets: "t", opt: "opt", seq: "s",
    batch: "b", gc: "gc", hw: "hw"
  };
  var BOOL = ["tied", "bias", "qkNorm", "moe", "dq", "gc"];
  var TEXT = { preset: 1, method: 1, prec: 1, opt: 1, hw: 1 };
  var CHOICES = { method: ["full", "lora", "qlora"], prec: ["fp32", "bf16", "fp16", "mixed", "int8", "nf4"],
                  opt: ["adamw", "adamw8", "paged8"], hw: ["discrete", "unified"] };

  function toQuery(x) {
    var parts = [];
    Object.keys(KEYS).forEach(function (k) {
      var v = x[k];
      if (v === undefined || v === null) return;
      if (k === "targets") v = v.join(".");
      else if (BOOL.indexOf(k) >= 0) v = v ? 1 : 0;
      parts.push(KEYS[k] + "=" + encodeURIComponent(String(v)));
    });
    return parts.join("&");
  }

  /** Parse a query string over a preset (unknown or bad values are ignored). */
  function fromQuery(qs) {
    var params = {};
    String(qs || "").replace(/^\?/, "").split("&").forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf("=");
      var k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i));
      params[k] = decodeURIComponent(i < 0 ? "" : kv.slice(i + 1).replace(/\+/g, " "));
    });
    var preset = PRESETS[params.preset] ? params.preset : DEFAULTS.preset;
    var x = presetInput(preset);
    if (params.preset === "custom") x.preset = "custom";
    Object.keys(KEYS).forEach(function (k) {
      var raw = params[KEYS[k]];
      if (raw === undefined || k === "preset") return;
      if (k === "targets") x.targets = raw.split(/[.,]/).filter(function (t) { return TARGETS.indexOf(t) >= 0; });
      else if (BOOL.indexOf(k) >= 0) x[k] = raw === "1" || raw === "true";
      else if (TEXT[k]) { if (!CHOICES[k] || CHOICES[k].indexOf(raw) >= 0) x[k] = raw; }
      else {
        var num = Number(raw);
        if (isFinite(num) && num > 0) x[k] = num;
      }
    });
    return x;
  }

  return {
    GiB: GiB, GB: GB, TARGETS: TARGETS, TIERS_GB: TIERS_GB, PRESETS: PRESETS, DEFAULTS: DEFAULTS,
    paramsFromArch: paramsFromArch, activeFromArch: activeFromArch, presetInput: presetInput,
    normalize: normalize, loraParams: loraParams, layerBytesPerToken: layerBytesPerToken,
    weightBytes: weightBytes, estimate: estimate, tierFor: tierFor, toQuery: toQuery, fromQuery: fromQuery
  };
});
