/* "Is the lift real?": the statistics (no page code here).
 *
 * Loaded by tools/is-the-lift-real.html as window.LiftStats and by
 * _build/run_tools.cjs under node, so the page and the tests run the same
 * code. No dependencies. Validated against scipy / statsmodels by
 * _build/lift_reference.py (cases in _build/tool_cases.json).
 *
 *   Wilson score interval: Wilson (1927); Newcombe (1998a), method 3.
 *   Difference, unpaired: Newcombe's hybrid score interval, Newcombe (1998a)
 *     "Interval estimation for the difference between independent
 *     proportions", Stat Med 17:873, method 10.
 *   Difference, paired: Newcombe (1998b) "Improved confidence intervals for
 *     the difference between binomial proportions based on paired data",
 *     Stat Med 17:2635, method 10 (no continuity correction).
 *   McNemar exact test: the two-sided binomial test of the discordant rows
 *     against 1/2 (McNemar 1947; exact form as in statsmodels mcnemar).
 *   Verdict: paired, "lift" / "drop" needs the interval to exclude 0 AND the
 *     exact McNemar p < 0.05, else "borderline"; unpaired, the interval alone.
 *   Paired t-interval: what BrewSLM's lift check reports
 *     (paired_comparison_stats.py on public main).
 *   Sizing: normal approximation at 95% confidence and 80% power,
 *     Z = 1.959964 + 0.841621 (BrewSLM eval_sizing_service.py).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.LiftStats = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var Z95 = 1.959963984540054;
  var Z_ALPHA = 1.959964, Z_BETA = 0.841621, Z_TOTAL = Z_ALPHA + Z_BETA;
  var MIN_ROWS = 5;

  function wilson(k, n, z) {
    z = z || Z95;
    if (n <= 0) return [NaN, NaN];
    var p = k / n, z2 = z * z, den = 1 + z2 / n;
    var centre = (p + z2 / (2 * n)) / den;
    var half = (z / den) * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n));
    return [Math.max(0, centre - half), Math.min(1, centre + half)];
  }

  /** Newcombe hybrid score CI for p1 - p2, independent samples. */
  function newcombeUnpaired(k1, n1, k2, n2) {
    var p1 = k1 / n1, p2 = k2 / n2, w1 = wilson(k1, n1), w2 = wilson(k2, n2), d = p1 - p2;
    return [d - Math.sqrt(Math.pow(p1 - w1[0], 2) + Math.pow(w2[1] - p2, 2)),
            d + Math.sqrt(Math.pow(w1[1] - p1, 2) + Math.pow(p2 - w2[0], 2))];
  }

  /** Newcombe (1998b) method 10 CI for p1 - p2 on paired data.
   *  a: both right, b: only model 1 right, c: only model 2 right, d: both wrong. */
  function newcombePaired(a, b, c, d) {
    var n = a + b + c + d;
    var p1 = (a + b) / n, p2 = (a + c) / n, diff = p1 - p2;
    var w1 = wilson(a + b, n), w2 = wilson(a + c, n);
    var den = (a + b) * (c + d) * (a + c) * (b + d);
    var phi = den > 0 ? (a * d - b * c) / Math.sqrt(den) : 0;
    var dl1 = p1 - w1[0], du1 = w1[1] - p1, dl2 = p2 - w2[0], du2 = w2[1] - p2;
    var lo = diff - Math.sqrt(Math.max(0, dl1 * dl1 - 2 * phi * dl1 * du2 + du2 * du2));
    var hi = diff + Math.sqrt(Math.max(0, du1 * du1 - 2 * phi * du1 * dl2 + dl2 * dl2));
    return [Math.max(-1, lo), Math.min(1, hi)];
  }

  // log Gamma (Lanczos, g = 7), for exact binomial tails on large n.
  var LG = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
            -176.61503916999185, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6,
            1.5056327351493116e-7];
  function lgamma(x) {
    if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
    x -= 1;
    var s = LG[0], t = x + 7.5;
    for (var i = 1; i < 9; i++) s += LG[i] / (x + i);
    return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(s);
  }

  /** Exact McNemar: two-sided binomial test of b vs c discordant rows at 1/2. */
  function mcnemarExact(b, c) {
    var m = b + c;
    if (m === 0) return 1;
    var k = Math.min(b, c), logs = [];
    for (var i = 0; i <= k; i++) logs.push(lgamma(m + 1) - lgamma(i + 1) - lgamma(m - i + 1) - m * Math.LN2);
    var mx = Math.max.apply(null, logs), s = 0;
    logs.forEach(function (l) { s += Math.exp(l - mx); });
    return Math.min(1, 2 * Math.exp(mx) * s);
  }

  // Regularized incomplete beta (continued fraction, Numerical Recipes betacf).
  function betacf(a, b, x) {
    var qab = a + b, qap = a + 1, qam = a - 1, c = 1, d = 1 - qab * x / qap;
    if (Math.abs(d) < 1e-300) d = 1e-300;
    d = 1 / d;
    var h = d;
    for (var m = 1; m <= 300; m++) {
      var m2 = 2 * m, aa = m * (b - m) * x / ((qam + m2) * (a + m2));
      d = 1 + aa * d; if (Math.abs(d) < 1e-300) d = 1e-300;
      c = 1 + aa / c; if (Math.abs(c) < 1e-300) c = 1e-300;
      d = 1 / d; h *= d * c;
      aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
      d = 1 + aa * d; if (Math.abs(d) < 1e-300) d = 1e-300;
      c = 1 + aa / c; if (Math.abs(c) < 1e-300) c = 1e-300;
      d = 1 / d;
      var del = d * c;
      h *= del;
      if (Math.abs(del - 1) < 1e-15) break;
    }
    return h;
  }
  function ibeta(a, b, x) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    var bt = Math.exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * Math.log(x) + b * Math.log(1 - x));
    return x < (a + 1) / (a + b + 2) ? bt * betacf(a, b, x) / a : 1 - bt * betacf(b, a, 1 - x) / b;
  }
  function tCdf(t, df) {
    var p = 0.5 * ibeta(df / 2, 0.5, df / (df + t * t));
    return t >= 0 ? 1 - p : p;
  }
  /** Two-sided 95% critical value of Student's t (bisection on the CDF). */
  function tCrit(df, conf) {
    var target = 1 - (1 - (conf || 0.95)) / 2, lo = 0, hi = 1000;
    for (var i = 0; i < 200; i++) {
      var mid = (lo + hi) / 2;
      if (tCdf(mid, df) < target) lo = mid; else hi = mid;
    }
    return (lo + hi) / 2;
  }

  /** Paired t-interval on the per-row change (+1, -1 or 0): BrewSLM's lift check. */
  function pairedT(better, worse, n) {
    if (n < 2) return null;
    var mean = (better - worse) / n;
    var ss = better * Math.pow(1 - mean, 2) + worse * Math.pow(-1 - mean, 2) + (n - better - worse) * mean * mean;
    var sd = Math.sqrt(ss / (n - 1));
    var half = tCrit(n - 1) * sd / Math.sqrt(n);
    return { mean: mean, sd: sd, ci: [mean - half, mean + half] };
  }

  function rowsFor(sd, gap) {
    return Math.max(1, Math.ceil(Math.pow(Z_TOTAL * sd / Math.max(1e-6, gap), 2) - 1e-9));
  }

  var ALPHA = 0.05;

  /** The verdict. Unpaired: the interval alone. Paired: "lift" or "drop" only when the interval
   *  excludes 0 AND the exact McNemar p is below 0.05; when the two disagree, "borderline".
   *  (The paired Newcombe interval is liberal with very few discordant rows: 4 better / 0 worse
   *  gives an interval above zero while the exact p is 0.125.) */
  function verdictOf(ci, p) {
    var side = ci[0] > 0 ? "lift" : ci[1] < 0 ? "drop" : "noise";
    if (p === undefined || p === null) return side;
    if (side === "noise") return p < ALPHA ? "borderline" : "noise";
    return p < ALPHA ? side : "borderline";
  }

  /**
   * Analyse base vs tuned on the same n rows.
   * input: {n, base, tuned, baseOnly?, tunedOnly?, gap?}  (counts; gap as a fraction, default 0.05)
   * Returns {error} when the counts are inconsistent.
   */
  function analyze(input) {
    var whole = function (v) { return v === undefined || v === null || v === "" || Number(v) === Math.floor(Number(v)); };
    if (!whole(input.n) || !whole(input.base) || !whole(input.tuned) || !whole(input.baseOnly) || !whole(input.tunedOnly)) {
      return { error: "Counts must be whole numbers of rows.", field: "count" };
    }
    var n = Number(input.n), B = Number(input.base), T = Number(input.tuned);
    if (input.gap !== undefined && input.gap !== null && !(input.gap > 0)) {
      return { error: "The smallest change worth detecting must be above 0 points.", field: "gap" };
    }
    var gap = input.gap > 0 ? input.gap : 0.05;
    if (!(n >= 1)) return { error: "Enter the number of rows (at least 1).", field: "n" };
    if (!(B >= 0 && B <= n)) return { error: "Correct counts must be between 0 and the number of rows.", field: "base" };
    if (!(T >= 0 && T <= n)) return { error: "Correct counts must be between 0 and the number of rows.", field: "tuned" };
    var r = { n: n, base: B, tuned: T, gap: gap };
    r.pBase = B / n; r.pTuned = T / n; r.diff = r.pTuned - r.pBase;
    r.ciBase = wilson(B, n); r.ciTuned = wilson(T, n);
    r.ciUnpaired = newcombeUnpaired(T, n, B, n);
    var hasPaired = input.baseOnly !== undefined && input.baseOnly !== null && input.baseOnly !== "" &&
                    input.tunedOnly !== undefined && input.tunedOnly !== null && input.tunedOnly !== "";
    r.paired = false;
    if (hasPaired) {
      var c = Number(input.baseOnly), b = Number(input.tunedOnly);
      var both = B - c;
      if (!(b >= 0 && c >= 0) || both !== T - b || both < 0 || both + b + c > n) {
        return { error: "The paired counts don't fit the totals: base right = both right + base-only right, " +
                        "tuned right = both right + tuned-only right, and the four groups add up to the rows.", field: "paired" };
      }
      r.paired = true;
      r.both = both; r.tunedOnly = b; r.baseOnly = c; r.neither = n - both - b - c;
      r.ciPaired = newcombePaired(both, b, c, r.neither);
      r.mcnemarP = mcnemarExact(b, c);
      r.tInterval = pairedT(b, c, n);
      var sd = r.tInterval ? r.tInterval.sd : 0;
      r.sdDiff = r.tInterval ? sd : null;
      r.pairedGap = sd > 0 ? Z_TOTAL * sd / Math.sqrt(n) : null;
      r.pairedRows = sd > 0 ? rowsFor(sd, gap) : null;
    }
    // Per-row variance of each score; a side that is all right or all wrong shows no spread, which is
    // no evidence of a tiny one, so it uses the Agresti-Coull rate (k + 2) / (n + 4) as BrewSLM does.
    var sideVar = function (k) {
      var p = k / n;
      if (k === 0 || k === n) p = (k + 2) / (n + 4);
      return p * (1 - p);
    };
    var v = sideVar(B) + sideVar(T);
    r.unpairedVariance = v;
    r.unpairedGap = Z_TOTAL * Math.sqrt(v / n);
    r.unpairedRows = rowsFor(Math.sqrt(v), gap);
    r.ci = r.paired ? r.ciPaired : r.ciUnpaired;
    r.verdict = n < MIN_ROWS ? "few" : verdictOf(r.ci, r.paired ? r.mcnemarP : null);
    r.intervalSide = verdictOf(r.ci, null);
    return r;
  }

  // URL state: n, base, tuned, base-only, tuned-only, gap in points.
  function toQuery(s) {
    var out = [];
    [["n", s.n], ["base", s.base], ["tuned", s.tuned], ["bo", s.baseOnly], ["to", s.tunedOnly], ["gap", s.gapPts]]
      .forEach(function (kv) {
        if (kv[1] !== undefined && kv[1] !== null && kv[1] !== "" && !(typeof kv[1] === "number" && isNaN(kv[1]))) out.push(kv[0] + "=" + encodeURIComponent(kv[1]));
      });
    return out.join("&");
  }
  /** decodeURIComponent that returns null instead of throwing on a malformed escape. */
  function decode(s) {
    try { return decodeURIComponent(s.replace(/\+/g, " ")); } catch (e) { return null; }
  }
  /** Parse a query string. Keys present but not a number come back in `bad`, so the page can say so
   *  instead of silently loading the example. */
  function fromQuery(qs) {
    var p = Object.create(null);
    String(qs || "").replace(/^\?/, "").split("&").forEach(function (kv) {
      var i = kv.indexOf("=");
      if (i <= 0) return;
      var k = decode(kv.slice(0, i)), v = decode(kv.slice(i + 1));
      if (k !== null) p[k] = v;
    });
    var bad = [];
    var num = function (key) {
      if (!(key in p)) return undefined;
      var v = p[key], x = Number(v);
      if (v !== null && v !== "" && isFinite(x) && x >= 0) return x;
      bad.push(key);
      return undefined;
    };
    var out = { n: num("n"), base: num("base"), tuned: num("tuned"), baseOnly: num("bo"), tunedOnly: num("to"), gapPts: num("gap") };
    out.bad = bad;
    out.any = ["n", "base", "tuned", "bo", "to", "gap"].some(function (k) { return k in p; });
    return out;
  }

  return {
    Z95: Z95, Z_TOTAL: Z_TOTAL, MIN_ROWS: MIN_ROWS, ALPHA: ALPHA, verdictOf: verdictOf, wilson: wilson, newcombeUnpaired: newcombeUnpaired,
    newcombePaired: newcombePaired, mcnemarExact: mcnemarExact, tCrit: tCrit, pairedT: pairedT,
    rowsFor: rowsFor, analyze: analyze, toQuery: toQuery, fromQuery: fromQuery
  };
});
