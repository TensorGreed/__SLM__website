#!/usr/bin/env python3
"""Reference values for tools/liftstats.js, from scipy and statsmodels (CPU only).

  python3 _build/lift_reference.py           # compare the JS (via node) with scipy/statsmodels
  python3 _build/lift_reference.py --write   # also rewrite _build/lift_reference.json

The JSON is what _build/test_tools.py checks the JS against when scipy and
statsmodels are not installed (CI); when they are, the test also recomputes it.

Where each reference comes from:
  Wilson interval           statsmodels proportion_confint(method="wilson")
  unpaired difference       statsmodels confint_proportions_2indep(method="newcomb")
  paired difference         Newcombe (1998b) method 10, written out below from
                            statsmodels Wilson limits (no library implements it);
                            with phi = 0 it must equal the unpaired interval,
                            which the "independent" case checks against statsmodels
  exact McNemar             statsmodels mcnemar(exact=True), cross-checked with
                            scipy binomtest(k, b + c, 0.5)
  paired t-interval         scipy ttest_1samp(...).confidence_interval(0.95)
  sizing                    Z = norm.ppf(0.975) + norm.ppf(0.80)
  verdict                   paired: lift / drop only when the paired interval excludes 0
                            and the exact McNemar p < 0.05, else borderline (or noise);
                            unpaired: the interval, but borderline when even
                            |T - B| changed rows all one way give p >= 0.05
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "tool_cases.json"
REFERENCE = HERE / "lift_reference.json"
MIN_ROWS = 5


def reference(case: dict) -> dict:
    import numpy as np
    from scipy import stats
    from statsmodels.stats.contingency_tables import mcnemar
    from statsmodels.stats.proportion import confint_proportions_2indep, proportion_confint

    n, B, T = case["n"], case["base"], case["tuned"]
    gap = case.get("gap", 0.05)
    z_total = stats.norm.ppf(0.975) + stats.norm.ppf(0.80)

    def wilson(k):
        lo, hi = proportion_confint(k, n, alpha=0.05, method="wilson")
        return [float(lo), float(hi)]

    out = {"id": case["id"], "ciBase": wilson(B), "ciTuned": wilson(T)}
    lo, hi = confint_proportions_2indep(T, n, B, n, method="newcomb", compare="diff", alpha=0.05)
    out["ciUnpaired"] = [float(lo), float(hi)]

    paired = "baseOnly" in case
    out.update(ciPaired=None, mcnemarP=None, tInterval=None, sdDiff=None, pairedGap=None, pairedRows=None)
    if paired:
        c, b = case["baseOnly"], case["tunedOnly"]
        a = B - c
        d = n - a - b - c
        p1, p2 = (a + b) / n, (a + c) / n
        (l1, u1), (l2, u2) = wilson(a + b), wilson(a + c)
        den = (a + b) * (c + d) * (a + c) * (b + d)
        phi = (a * d - b * c) / math.sqrt(den) if den > 0 else 0.0
        delta = math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
        eps = math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
        out["ciPaired"] = [max(-1.0, p1 - p2 - delta), min(1.0, p1 - p2 + eps)]
        if phi == 0.0 and den > 0:
            assert all(abs(x - y) < 1e-12 for x, y in zip(out["ciPaired"], out["ciUnpaired"])), case["id"]

        p_sm = float(mcnemar([[a, b], [c, d]], exact=True).pvalue)
        p_sp = float(stats.binomtest(min(b, c), b + c, 0.5).pvalue) if b + c else 1.0
        assert abs(p_sm - p_sp) < 1e-12, (case["id"], p_sm, p_sp)
        out["mcnemarP"] = p_sm

        diffs = np.array([1.0] * b + [-1.0] * c + [0.0] * (n - b - c))
        if n >= 2:
            sd = float(diffs.std(ddof=1))
            if sd > 0:
                ci = stats.ttest_1samp(diffs, 0.0).confidence_interval(0.95)
                out["tInterval"] = [float(ci.low), float(ci.high)]
            else:
                out["tInterval"] = [float(diffs.mean())] * 2
            out["sdDiff"] = sd
            if sd > 0:
                out["pairedGap"] = float(z_total * sd / math.sqrt(n))
                out["pairedRows"] = max(1, math.ceil((z_total * sd / gap) ** 2 - 1e-9))

    def side_var(k):
        p = (k + 2) / (n + 4) if k in (0, n) else k / n
        return p * (1 - p)
    v = side_var(B) + side_var(T)
    out["unpairedGap"] = float(z_total * math.sqrt(v / n))
    out["unpairedRows"] = max(1, math.ceil((z_total * math.sqrt(v) / gap) ** 2 - 1e-9))
    ci = out["ciPaired"] or out["ciUnpaired"]
    side = "lift" if ci[0] > 0 else "drop" if ci[1] < 0 else "noise"
    if paired:
        # Paired: a lift or drop needs the interval AND the exact McNemar test (p < 0.05) to agree.
        p = out["mcnemarP"]
        side = ("borderline" if p < 0.05 else "noise") if side == "noise" else (side if p < 0.05 else "borderline")
    elif side in ("lift", "drop") and stats.binomtest(0, abs(T - B), 0.5).pvalue >= 0.05:
        # Unpaired: even all |T - B| changed rows one way (the most favourable paired counts) give p >= 0.05.
        side = "borderline"
    out["verdict"] = "few" if n < MIN_ROWS else side
    return out


def references() -> list[dict]:
    return [reference(c) for c in json.loads(CASES.read_text(encoding="utf-8"))["lift"]]


def run_js() -> dict:
    res = subprocess.run(["node", str(HERE / "run_tools.cjs")], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


# Absolute tolerance on intervals, gaps and p-values; rows must match exactly.
TOL = 1e-6


def compare(js: list[dict], ref: list[dict]) -> list[str]:
    errs = []
    by_id = {r["id"]: r for r in ref}
    for j in js:
        r = by_id.get(j["id"])
        if r is None:
            errs.append(f"{j['id']}: no reference")
            continue
        for k, want in r.items():
            if k == "id":
                continue
            got = j.get(k)
            if isinstance(want, list) and isinstance(got, list):
                ok = len(want) == len(got) and all(abs(a - b) <= TOL for a, b in zip(got, want))
            elif isinstance(want, float) and isinstance(got, (int, float)):
                ok = abs(got - want) <= TOL * max(1.0, abs(want))
            else:
                ok = got == want
            if not ok:
                errs.append(f"{j['id']}.{k}: JS {got!r}, reference {want!r}")
    return errs


def main() -> int:
    ref = references()
    js = run_js()["lift"]
    errs = compare(js, ref)
    for r in ref:
        print(f"{r['id']:>14}: verdict {r['verdict']:<5} paired {r['ciPaired']} McNemar p {r['mcnemarP']}")
    if "--write" in sys.argv:
        REFERENCE.write_text(json.dumps(ref, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {REFERENCE.relative_to(HERE.parent)}")
    for e in errs:
        print("mismatch:", e)
    print(f"{len(ref)} cases, {len(errs)} mismatch(es)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
