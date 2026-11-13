"""Validation tests for the two calculators under tools/.

They run the page's own formula modules (tools/memcalc.js, tools/liftstats.js)
under node via _build/run_tools.cjs, so what is tested is what visitors run:

  * memory calculator: each case in _build/tool_cases.json against its measured
    value (Lesson 1.15's SmolLM2-135M counts, the measured 30B-A3B run, small
    fused-expert and 4-bit models counted on a CPU, and the memory table's bytes
    per parameter, kept apart as "derived") within the stated tolerance, and the
    calculator page shows the same rounded estimates in its validation table;
    hostile URLs (bad %-escapes, prototype keys) must not break either tool;
  * lift calculator: every case against _build/lift_reference.json (scipy /
    statsmodels values written by _build/lift_reference.py); when scipy and
    statsmodels are installed the reference is recomputed too; a scan checks that a
    paired lift or drop always comes with an exact McNemar p below 0.05.

Skipped (not failed) when node is missing. Run with:

  python3 -m unittest discover -s _build -p 'test_*.py'
"""
from __future__ import annotations

import html
import importlib.util
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CASES = json.loads((HERE / "tool_cases.json").read_text(encoding="utf-8"))
NODE = shutil.which("node")

_spec = importlib.util.spec_from_file_location("lift_reference", HERE / "lift_reference.py")
REF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REF)


def run_js() -> dict:
    out = subprocess.run([NODE, str(HERE / "run_tools.cjs")], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class MemoryCalculator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = run_js()
        cls.by_id = {r["id"]: r["value"] for r in cls.js["memcalc"]}

    def test_cases_within_tolerance(self):
        for c in CASES["memcalc"]:
            got, want = self.by_id[c["id"]], c["measured"]
            with self.subTest(case=c["id"]):
                if c["tolerance"] == 0:
                    self.assertEqual(got, want)
                else:
                    self.assertLessEqual(abs(got - want) / want, c["tolerance"],
                                         f"{c['id']}: estimate {got:.4g} {c['unit']} vs measured {want}")

    def test_page_shows_the_estimates(self):
        page = html.unescape((ROOT / "tools" / "fine-tuning-memory-calculator.html").read_text(encoding="utf-8"))
        for c in CASES["memcalc"]:
            with self.subTest(case=c["id"]):
                v = self.by_id[c["id"]]
                num, unit = c["show"].rsplit(" ", 1) if " " in c["show"] else (c["show"], "")
                decimals = len(num.split(".")[1]) if "." in num else 0
                shown = f"{v:,.{decimals}f}"
                self.assertEqual(shown, num, f"{c['id']}: the formulas give {shown}, the case says {num}")
                self.assertIn(f'data-case="{c["id"]}"', page)
                cell = re.search(rf'data-case="{re.escape(c["id"])}"[^>]*>(.*?)</td>', page, re.S)
                self.assertIsNotNone(cell, c["id"])
                self.assertIn(c["show"], " ".join(re.sub(r"<[^>]+>", "", cell.group(1)).split()))

    def test_url_state_round_trips(self):
        for r in self.js["roundTrip"]:
            self.assertTrue(r["same"], r["preset"])

    def test_hostile_urls_do_not_break(self):
        """Malformed %-escapes and prototype keys (?preset=constructor) parse to finite numbers."""
        for r in self.js["urlSafety"]:
            self.assertTrue(r["ok"], f"{r['q']}: {r['why']}")
        self.assertTrue(self.js["legacySeq"], "old links with s= must still set the tokens per row; seq= wins")

    def test_derived_rows_are_not_called_measurements(self):
        """Rows that are the memory table's arithmetic sit under the consistency heading, not in the measured tables."""
        page = (ROOT / "tools" / "fine-tuning-memory-calculator.html").read_text(encoding="utf-8")
        split = page.index('id="consistency"')
        for c in CASES["memcalc"]:
            with self.subTest(case=c["id"]):
                at = page.index(f'data-case="{c["id"]}"')
                self.assertEqual(at > split, c.get("kind") == "derived", c["id"])


@unittest.skipUnless(NODE, "node is not installed")
class LiftCalculator(unittest.TestCase):
    def test_matches_reference(self):
        ref = json.loads((HERE / "lift_reference.json").read_text(encoding="utf-8"))
        self.assertEqual({r["id"] for r in ref}, {c["id"] for c in CASES["lift"]},
                         "run python3 _build/lift_reference.py --write after changing the cases")
        self.assertEqual(REF.compare(run_js()["lift"], ref), [])

    def test_reference_is_current(self):
        try:
            import scipy  # noqa: F401
            import statsmodels  # noqa: F401
        except ImportError:
            self.skipTest("scipy / statsmodels not installed")
        ref = json.loads((HERE / "lift_reference.json").read_text(encoding="utf-8"))
        self.assertEqual(REF.compare(REF.references(), ref), [])

    def test_page_shows_the_reference(self):
        """The validation table on the page prints each case's interval as the reference has it."""
        page = html.unescape((ROOT / "tools" / "is-the-lift-real.html").read_text(encoding="utf-8"))
        ref = {r["id"]: r for r in json.loads((HERE / "lift_reference.json").read_text(encoding="utf-8"))}

        def pts(x):
            s = f"{abs(100 * x):.1f}"
            return s if s == "0.0" else ("−" if x < 0 else "+") + s
        for cid, r in ref.items():
            with self.subTest(case=cid):
                ci = r["ciPaired"] or r["ciUnpaired"]
                cell = re.search(rf'data-lift-case="{re.escape(cid)}"[^>]*>(.*?)</td>', page, re.S)
                self.assertIsNotNone(cell, cid)
                self.assertEqual(cell.group(1).strip(), f"{pts(ci[0])} to {pts(ci[1])}")

    def test_lift_needs_the_exact_test_to_agree(self):
        """Review M1: no paired 'lift' or 'drop' with exact McNemar p >= 0.05, over a scan of small tables."""
        js = run_js()
        self.assertGreater(js["scanned"], 10000)
        self.assertEqual(js["verdictViolations"], [])
        ref = {r["id"]: r for r in json.loads((HERE / "lift_reference.json").read_text(encoding="utf-8"))}
        self.assertEqual(ref["five-3-0"]["verdict"], "borderline")
        self.assertEqual(ref["fifty-4-0"]["verdict"], "borderline")
        self.assertEqual(ref["fifty-6-0"]["verdict"], "lift")

    def test_at_least_eight_cases_with_edges(self):
        lift = CASES["lift"]
        self.assertGreaterEqual(len(lift), 8)
        self.assertTrue(any(c["base"] == 0 for c in lift))
        self.assertTrue(any(c["tuned"] == c["n"] for c in lift))


_site_spec = importlib.util.spec_from_file_location("site_build_tools", HERE / "site.py")
SITE = importlib.util.module_from_spec(_site_spec)
_site_spec.loader.exec_module(SITE)


class ToolPageChecks(unittest.TestCase):
    def test_real_tool_files_pass(self):
        self.assertEqual(SITE.tool_file_errors(SITE.discover()), [])

    def test_script_rules_catch_inline_styles_and_literals(self):
        for bad in ('el.style.width = "50%";', 'el.setAttribute("style", "x")', 'var c = "#ff0000";',
                    'ctx.fillStyle = rgba(0, 0, 0, 1);'):
            with self.subTest(code=bad):
                self.assertTrue(any(rx.search(bad) for rx, _ in SITE.TOOL_JS_FORBIDDEN), bad)
        self.assertFalse(any(rx.search('el.className = "bar__fill--w-50";') for rx, _ in SITE.TOOL_JS_FORBIDDEN))

    def test_tool_pages_carry_webapplication_and_faq(self):
        pages, _, _ = SITE.render()
        vis = {rel: SITE.Visible(rel, text) for rel, text in pages.items()}
        tools = [p for p in pages if SITE.page_type(p) == "tool"]
        self.assertEqual(len(tools), 2)
        for rel in tools:
            errs, found = SITE.check_jsonld(rel, pages[rel], vis, set(pages), pages)
            self.assertEqual(errs, [], rel)
            self.assertTrue({"WebApplication", "FAQPage", "BreadcrumbList"} <= set(found), rel)
            # a FAQ answer edited on the page without a build must fail
            broken = pages[rel].replace("<summary>", "<summary>Edited: ", 1)
            v2 = dict(vis, **{rel: SITE.Visible(rel, broken)})
            errs, _ = SITE.check_jsonld(rel, broken, v2, set(pages), pages)
            self.assertTrue(any("FAQPage" in e for e in errs), rel)


if __name__ == "__main__":
    unittest.main()
