// Runs the calculator formula modules (tools/memcalc.js, tools/liftstats.js)
// on the cases in _build/tool_cases.json and prints the results as JSON.
// Used by _build/test_tools.py and _build/lift_reference.py; needs only node.
"use strict";
const path = require("path");
const fs = require("fs");

const root = path.resolve(__dirname, "..");
const MemCalc = require(path.join(root, "tools", "memcalc.js"));
const LiftStats = require(path.join(root, "tools", "liftstats.js"));
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "tool_cases.json"), "utf8"));

const UNIT = { GB: 1e9, GiB: MemCalc.GiB, MB: 1e6, B: 1, count: 1 };
const memcalc = cases.memcalc.map((c) => {
  const x = Object.assign(MemCalc.presetInput(c.preset), c.arch || {}, c.set);
  if (c.arch) { x.preset = "custom"; x.params = MemCalc.paramsFromArch(x); }   // a small counted model, not a preset
  const r = MemCalc.estimate(x);
  return { id: c.id, value: r[c.field] / UNIT[c.unit] };
});

// Review M1: a paired verdict of lift or drop must never come with an exact McNemar p >= 0.05.
let scanned = 0;
const verdictViolations = [];
for (let n = 5; n <= 80; n++) {
  for (let b = 0; b <= 14; b++) for (let c = 0; c <= 14 && b + c <= n; c++) {
    const rest = n - b - c;
    for (const both of new Set([0, Math.floor(rest / 2), rest])) {
      const r = LiftStats.analyze({ n, base: both + c, tuned: both + b, tunedOnly: b, baseOnly: c });
      scanned++;
      if ((r.verdict === "lift" || r.verdict === "drop") && !(r.mcnemarP < 0.05)) verdictViolations.push([n, both, b, c]);
      if (r.verdict === "lift" && !(r.ci[0] > 0)) verdictViolations.push([n, both, b, c, "ci"]);
      if (r.verdict === "drop" && !(r.ci[1] < 0)) verdictViolations.push([n, both, b, c, "ci"]);
    }
  }
}

// The unpaired path too: a lift or drop needs some paired split (all |T - B| changed rows one way) to reach p < 0.05.
for (let n = 1; n <= 150; n++) {
  for (let B = 0; B <= n; B++) for (let T = 0; T <= n; T++) {
    const r = LiftStats.analyze({ n, base: B, tuned: T });
    scanned++;
    if ((r.verdict === "lift" || r.verdict === "drop") && !(LiftStats.mcnemarExact(Math.abs(T - B), 0) < 0.05)) verdictViolations.push([n, B, T, "unpaired"]);
    if (r.verdict === "lift" && !(r.ci[0] > 0)) verdictViolations.push([n, B, T, "unpaired ci"]);
    if (r.verdict === "drop" && !(r.ci[1] < 0)) verdictViolations.push([n, B, T, "unpaired ci"]);
  }
}

// Malformed or hostile query strings must parse without throwing and keep numbers finite.
const hostile = ["?p=%E0%A4%A", "?n=%E0%A4%A", "?%E0%A4%A=1", "?preset=constructor", "?preset=__proto__",
                 "?preset=toString&s=%", "?seq=abc&b=-1", "?n=50.6&base=22&tuned=26", "?constructor=1&__proto__=2"];
const urlSafety = hostile.map((q) => {
  let ok = true, why = "";
  try {
    const x = MemCalc.fromQuery(q), r = MemCalc.estimate(x);
    if (!isFinite(r.peak)) { ok = false; why = "memcalc peak " + r.peak; }
    const l = LiftStats.fromQuery(q);
    if (!Array.isArray(l.bad)) { ok = false; why = "lift parse"; }
  } catch (e) { ok = false; why = String(e); }
  return { q, ok, why };
});
const legacySeq = MemCalc.fromQuery("?preset=1.5b&s=777").seq === 777 && MemCalc.fromQuery("?seq=555&s=777").seq === 555;

// URL state must round-trip for every preset.
const roundTrip = Object.keys(MemCalc.PRESETS).map((k) => {
  const x = MemCalc.presetInput(k);
  const back = MemCalc.fromQuery(MemCalc.toQuery(x));
  const same = Object.keys(x).every((f) => JSON.stringify(x[f]) === JSON.stringify(back[f]));
  return { preset: k, same };
});

const lift = cases.lift.map((c) => {
  const r = LiftStats.analyze(c);
  return {
    id: c.id, error: r.error || null, ciBase: r.ciBase, ciTuned: r.ciTuned, ciUnpaired: r.ciUnpaired,
    ciPaired: r.ciPaired || null, mcnemarP: r.paired ? r.mcnemarP : null,
    tInterval: r.tInterval ? r.tInterval.ci : null, sdDiff: r.paired ? r.sdDiff : null,
    pairedGap: r.paired ? r.pairedGap : null, pairedRows: r.paired ? r.pairedRows : null,
    unpairedGap: r.unpairedGap, unpairedRows: r.unpairedRows, verdict: r.verdict,
  };
});

process.stdout.write(JSON.stringify({ memcalc, roundTrip, lift, scanned, verdictViolations, urlSafety, legacySeq }, null, 1) + "\n");
