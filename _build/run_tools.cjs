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

const UNIT = { GB: 1e9, GiB: MemCalc.GiB, B: 1, count: 1 };
const memcalc = cases.memcalc.map((c) => {
  const r = MemCalc.estimate(Object.assign(MemCalc.presetInput(c.preset), c.set));
  return { id: c.id, value: r[c.field] / UNIT[c.unit] };
});

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

process.stdout.write(JSON.stringify({ memcalc, roundTrip, lift }, null, 1) + "\n");
