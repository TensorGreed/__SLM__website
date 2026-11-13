"""Mutation tests for the quiz, certificate, lesson-order, "Lesson X.Y",
freshness and shared-box checks in _build/site.py.

Each test breaks a copy of the real pages in memory (never on disk) and
asserts that `check` would catch it. Run with:

  python3 -m unittest discover -s _build -p 'test_*.py'
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("site_build", Path(__file__).with_name("site.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


def real_pages() -> dict[str, str]:
    return {p: (S.ROOT / p).read_text(encoding="utf-8") for p in S.discover()}


def real_quizzes() -> dict[str, list]:
    out = {}
    for rel, text in real_pages().items():
        m = S.QUIZ_JSON_RE.search(text)
        if m and rel.startswith("academy/"):
            out[rel] = json.loads(m.group(3))
    return out


def with_quiz(text: str, quiz: list) -> str:
    m = S.QUIZ_JSON_RE.search(text)
    return text[:m.start(3)] + json.dumps(quiz) + text[m.end(3):]


class QuizChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quizzes = real_quizzes()

    def test_real_quizzes_pass(self):
        self.assertEqual(S.quiz_balance_errors(self.quizzes), [])
        for rel, quiz in self.quizzes.items():
            self.assertEqual(S.quiz_shape_errors(quiz), [], rel)

    def test_answers_all_in_one_slot_fail(self):
        mutated = {}
        for rel, quiz in self.quizzes.items():
            qs = []
            for q in quiz:
                opts = [q["options"][q["answer"]]] + [o for i, o in enumerate(q["options"]) if i != q["answer"]]
                qs.append({**q, "options": opts, "answer": 0})
            mutated[rel] = qs
        errs = S.quiz_balance_errors(mutated)
        tracks = {rel.split("/")[1] for rel in mutated}
        self.assertEqual(len([e for e in errs if "are option A" in e]), len(tracks), errs)

    def test_correct_always_longest_fails(self):
        mutated = {rel: [{**q, "options": [o + (" because of the reason stated in the lesson text" * 3
                                                if i == q["answer"] else "")
                                           for i, o in enumerate(q["options"])]} for q in quiz]
                   for rel, quiz in self.quizzes.items()}
        errs = S.quiz_balance_errors(mutated)
        tracks = {rel.split("/")[1] for rel in mutated}
        self.assertEqual(len([e for e in errs if "is the longest" in e]), len(tracks), errs)

    def test_correct_always_second_shortest_fails(self):
        # The tell left after padding distractors: the answer is never the
        # longest or shortest, but always the same middle rank.
        def pad(q):
            opts = [o + "." * (60 - len(o)) if i == q["answer"] else o for i, o in enumerate(q["options"])]
            others = sorted(i for i in range(len(opts)) if i != q["answer"])
            opts[others[0]] = opts[others[0]][:20] or "x"          # one shorter
            for k in others[1:]:
                opts[k] = opts[k] + " -" * 60 + str(k)               # the rest longer
            return {**q, "options": opts}
        mutated = {rel: [pad(q) for q in quiz] for rel, quiz in self.quizzes.items()}
        for quiz in mutated.values():
            for q in quiz:
                self.assertEqual(S.length_rank(q), 1)
        errs = S.quiz_balance_errors(mutated)
        tracks = {rel.split("/")[1] for rel in mutated}
        hits = [e for e in errs if "length rank 2" in e]
        self.assertEqual(len(hits), len(tracks), errs)
        self.assertEqual([e for e in errs if "is the longest" in e or "is the shortest" in e], [])

    def test_length_rank_counts_strictly_shorter_options(self):
        self.assertEqual(S.length_rank({"options": ["aa", "b", "ccc", "dd"], "answer": 0}), 1)
        self.assertEqual(S.length_rank({"options": ["aa", "b", "ccc", "dd"], "answer": 2}), 3)
        self.assertEqual(S.length_rank({"options": ["aa", "b", "ccc", "dd"], "answer": 1}), 0)

    def test_positional_option_fails(self):
        rel, quiz = next(iter(sorted(self.quizzes.items())))
        q = dict(quiz[0])
        q["options"] = q["options"][:-1] + ["None of the above"] if q["answer"] != len(q["options"]) - 1 \
            else ["All of the above"] + q["options"][1:]
        self.assertTrue(any("by position" in e for e in S.quiz_shape_errors([q] + quiz[1:])))
        q2 = {**quiz[0], "explanation": "Option B is right because ..."}
        self.assertTrue(any("by position" in e for e in S.quiz_shape_errors([q2])))
        for bad in ("All the above", "The first option", "Options 1 and 2", "The last one",
                    "Neither of the options"):
            q3 = {**quiz[0], "options": [bad if i != quiz[0]["answer"] else o
                                         for i, o in enumerate(quiz[0]["options"])][:4]}
            self.assertTrue(any("by position" in e for e in S.quiz_shape_errors([q3])), bad)
        for fine in ("It is often not the last one you saved", "The judge scores each answer 1-5",
                     "Retraining answers a different question"):
            self.assertIsNone(S.POSITION_RE.search(fine), fine)

    def test_bad_answer_index_fails(self):
        q = {**next(iter(self.quizzes.values()))[0], "answer": 7}
        self.assertTrue(any("not an option index" in e for e in S.quiz_shape_errors([q])))

    def test_rebalance_is_balanced_and_idempotent(self):
        skewed = {rel: [{**q, "options": [q["options"][q["answer"]]] +
                         [o for i, o in enumerate(q["options"]) if i != q["answer"]], "answer": 0}
                        for q in quiz] for rel, quiz in self.quizzes.items()}
        once = S.rebalance(skewed)
        self.assertEqual([e for e in S.quiz_balance_errors(once) if "are option" in e], [])
        self.assertEqual(S.rebalance(once), once)
        for rel in skewed:   # the same questions, the same correct text
            for a, b in zip(skewed[rel], once[rel]):
                self.assertEqual(a["options"][a["answer"]], b["options"][b["answer"]])
                self.assertEqual(sorted(a["options"]), sorted(b["options"]))


class PageChecks(unittest.TestCase):
    """Mutations of whole pages, run through the same functions `check` uses."""

    @classmethod
    def setUpClass(cls):
        cls.pages, _, _ = S.render()
        cls.lesson = next(r for r in sorted(cls.pages) if S.page_type(r) == "lesson"
                          and S.QUIZ_JSON_RE.search(cls.pages[r]))

    def vis(self, pages):
        return {r: S.Visible(r, t) for r, t in pages.items()}

    def test_quiz_jsonld_must_match_visible_quiz(self):
        text = self.pages[self.lesson]
        quiz = json.loads(S.QUIZ_JSON_RE.search(text).group(3))
        q0 = quiz[0]
        wrong = (q0["answer"] + 1) % len(q0["options"])
        mutated = with_quiz(text, [{**q0, "answer": wrong}] + quiz[1:])   # JSON-LD still has the old key
        pages = {**self.pages, self.lesson: mutated}
        errs, _ = S.check_jsonld(self.lesson, mutated, self.vis(pages), set(pages), pages)
        self.assertTrue(any("differs from the visible quiz" in e for e in errs), errs)

    def test_static_fallback_follows_the_json(self):
        text = self.pages[self.lesson]
        quiz = json.loads(S.QUIZ_JSON_RE.search(text).group(3))
        q0 = quiz[0]
        wrong = (q0["answer"] + 1) % len(q0["options"])
        rebuilt = S.build_quiz_static(with_quiz(text, [{**q0, "answer": wrong}] + quiz[1:]))
        self.assertIn(f"Answer: {S.LETTERS[wrong]}. ", rebuilt)
        self.assertEqual(rebuilt.count("<!-- site:quiz"), 1)
        self.assertEqual(S.build_quiz_static(rebuilt), rebuilt)

    def test_second_quiz_on_a_page_fails(self):
        text = self.pages[self.lesson]
        m = S.QUIZ_JSON_RE.search(text)
        section = text[m.start(1):text.index("</section>", m.end(4)) + len("</section>")]
        doubled = text.replace(section, section + "\n" + section, 1)
        errs = S.check_quizzes({self.lesson: S.Visible(self.lesson, doubled)})
        self.assertTrue(any("2 quizzes on one page" in e for e in errs), errs)
        self.assertFalse(any("quizzes on one page" in e for e in S.check_quizzes(self.vis(self.pages))))

    def test_certificate_manifest_must_list_every_lesson(self):
        rel = "academy/certificate.html"
        text = self.pages[rel]
        manifest = json.loads(S.MANIFEST_RE.search(text).group(1))
        self.assertEqual(S.check_certificate(self.vis(self.pages), self.pages), [])
        m = S.MANIFEST_RE.search(text)
        dropped = text[:m.start(1)] + json.dumps(manifest[:-1]) + text[m.end(1):]
        pages = {**self.pages, rel: dropped}
        errs = S.check_certificate(self.vis(pages), pages)
        self.assertTrue(any("missing" in e for e in errs), errs)
        # the visible count follows the manifest
        self.assertEqual(set(re.findall(r"data-cert-total>(\d+)<", S.build_cert_total(dropped))),
                         {str(len(manifest) - 1)})


class SequenceChecks(unittest.TestCase):
    """Lesson order, pagers, badges, the hub, and "Lesson X.Y" references."""

    @classmethod
    def setUpClass(cls):
        cls.pages, _, _ = S.render()
        cls.vis = {r: S.Visible(r, t) for r, t in cls.pages.items()}

    def mutate(self, rel, old, new, count=1):
        text = self.pages[rel]
        self.assertIn(old, text, rel)
        text = text.replace(old, new, count)
        return {**self.pages, rel: text}, {**self.vis, rel: S.Visible(rel, text)}

    def seq(self, pages, vis):
        return S.check_sequence(vis, pages)

    def refs(self, pages, vis):
        return S.check_lesson_refs(vis, pages)

    def test_real_site_passes(self):
        self.assertEqual(self.seq(self.pages, self.vis), [])
        self.assertEqual(self.refs(self.pages, self.vis), [])
        self.assertEqual(S.check_freshness(self.vis)[0], [])

    def test_numbers_follow_the_track_index(self):
        nums = S.lesson_numbers(self.vis, self.pages)
        self.assertEqual(nums["0.6"], "academy/foundations/from-n-grams-to-transformers.html")
        self.assertEqual(nums["0.8"], "academy/foundations/how-language-models-work.html")
        self.assertEqual(nums["4.10"], "academy/advanced/capstone-graduating.html")
        self.assertEqual(nums["1.22"], "academy/sft-fundamentals/catastrophic-forgetting.html")

    def test_reordered_index_breaks_pagers_badges_and_numbers(self):
        rel = "academy/foundations/index.html"
        text = self.pages[rel]
        a = text.index('href="from-n-grams-to-transformers.html"')
        b = text.index('href="architecture-taxonomy.html"')
        swapped = (text[:a] + 'href="architecture-taxonomy.html"' + text[a + len('href="from-n-grams-to-transformers.html"'):b]
                   + 'href="from-n-grams-to-transformers.html"' + text[b + len('href="architecture-taxonomy.html"'):])
        pages = {**self.pages, rel: swapped}
        vis = {**self.vis, rel: S.Visible(rel, swapped)}
        errs = self.seq(pages, vis)
        self.assertTrue(any("pager Next" in e for e in errs), errs)
        self.assertTrue(any("badge says Track 0, Lesson 6" in e for e in errs), errs)
        self.assertTrue(any("item 6." in e for e in errs), errs)
        self.assertTrue(any("the foundations card" in e for e in errs), errs)

    def test_pager_to_the_wrong_lesson_fails(self):
        rel = "academy/foundations/attention-and-the-transformer.html"
        pages, vis = self.mutate(rel, 'href="from-n-grams-to-transformers.html" class="lesson-nav__next"',
                                 'href="how-language-models-work.html" class="lesson-nav__next"')
        errs = self.seq(pages, vis)
        self.assertTrue(any(e.startswith(rel + ": pager Next") for e in errs), errs)

    def test_pager_title_must_name_the_target(self):
        rel = "academy/foundations/attention-and-the-transformer.html"
        pages, vis = self.mutate(rel, '<span class="lesson-nav__title">From n-grams to Transformers</span>',
                                 '<span class="lesson-nav__title">How language models work</span>')
        errs = self.seq(pages, vis)
        self.assertTrue(any("title 'How language models work' does not name" in e for e in errs), errs)

    def test_last_lesson_goes_to_the_certificate(self):
        rel = "academy/advanced/reasoning-training.html"
        pages, vis = self.mutate(rel, 'href="../certificate.html" class="lesson-nav__next"',
                                 'href="../index.html" class="lesson-nav__next"')
        self.assertTrue(any("want academy/certificate.html" in e for e in self.seq(pages, vis)))

    def test_badge_number_fails(self):
        rel = "academy/advanced/structured-pruning.html"
        pages, vis = self.mutate(rel, "Track 4 · Advanced · Lesson 13", "Track 4 · Advanced · Lesson 12")
        self.assertTrue(any("badge says Track 4, Lesson 12" in e for e in self.seq(pages, vis)))

    def test_unknown_lesson_number_fails(self):
        rel = "academy/advanced/production-feedback-loop.html"
        pages, vis = self.mutate(rel, "(Lesson 1.22)", "(Lesson 1.29)")
        self.assertTrue(any("Lesson 1.29 does not exist" in e for e in self.refs(pages, vis)))

    def test_backwards_range_fails(self):
        rel = "academy/advanced/structured-pruning.html"
        pages, vis = self.mutate(rel, "Lessons 4.2–4.4", "Lessons 4.4–4.2")
        self.assertTrue(any("not a range inside one track" in e for e in self.refs(pages, vis)))

    def test_title_after_a_reference_must_match(self):
        rel = "academy/advanced/speculative-decoding.html"
        pages, vis = self.mutate(rel, "Lesson 4.8 — serving and inference", "Lesson 4.7 — serving and inference")
        errs = self.refs(pages, vis)
        self.assertTrue(any("'Lesson 4.7' is followed by 'serving and inference'" in e for e in errs), errs)

    def test_title_hint_with_overlapping_words_but_wrong_lesson_fails(self):
        # R1 S4: "base" and "model" also fit 0.12 (base vs instruct), but the
        # hint names 0.13 (picking a base model); overlap alone used to pass.
        rel = "academy/advanced/speculative-decoding.html"
        nums = S.lesson_numbers(self.vis, self.pages)
        self.assertTrue(S.names_lesson("picking a base model", self.vis, nums["0.13"]))
        self.assertFalse(S.names_lesson("picking a base model", self.vis, nums["0.12"]))
        self.assertTrue(S.names_lesson("base vs instruct", self.vis, nums["0.12"]))
        self.assertFalse(S.names_lesson("base vs instruct", self.vis, nums["0.13"]))
        pages, vis = self.mutate(rel, "Lesson 4.8 — serving and inference",
                                 "Lesson 0.12 (picking a base model) and Lesson 4.8 — serving and inference")
        errs = self.refs(pages, vis)
        self.assertTrue(any("'Lesson 0.12' is followed by 'picking a base model'" in e for e in errs), errs)
        pages, vis = self.mutate(rel, "Lesson 4.8 — serving and inference",
                                 "Lesson 0.13 (picking a base model) and Lesson 4.8 — serving and inference")
        self.assertEqual(self.refs(pages, vis), [])

    def test_linked_reference_must_link_that_lesson(self):
        rel = "academy/with-brewslm/eval-pack-and-failure-cluster-reference.html"
        pages, vis = self.mutate(rel, 'href="eval-packs-and-failure-clusters.html">Lesson 3.8</a>',
                                 'href="eval-packs-and-failure-clusters.html">Lesson 3.9</a>')
        self.assertTrue(any("a link reading Lesson 3.9 goes to" in e for e in self.refs(pages, vis)))

    def test_track_and_number_must_agree(self):
        rel = "academy/foundations/base-vs-instruct-models.html"
        pages, vis = self.mutate(rel, "(Track 4, Lesson 4.5)", "(Track 3, Lesson 4.5)")
        self.assertTrue(any("'Track 3, Lesson 4.5'" in e for e in self.refs(pages, vis)))

    def test_undotted_number_outside_a_track_index_fails(self):
        rel = "academy/with-brewslm/preflight-and-trainability.html"
        pages, vis = self.mutate(rel, "after evaluation (Lesson 3.8).", "after evaluation (lesson 8).")
        self.assertTrue(any("'lesson 8'" in e for e in self.refs(pages, vis)))
        pages, vis = self.mutate(rel, "after evaluation (Lesson 3.8).", "after evaluation (Track 3, Lesson 8).")
        self.assertTrue(any("'Lesson 8'" in e for e in self.refs(pages, vis)))

    def test_certificate_order_follows_the_index(self):
        rel = "academy/certificate.html"
        m = S.MANIFEST_RE.search(self.pages[rel])
        manifest = json.loads(m.group(1))
        manifest[5], manifest[7] = manifest[7], manifest[5]
        text = self.pages[rel][:m.start(1)] + json.dumps(manifest) + self.pages[rel][m.end(1):]
        pages = {**self.pages, rel: text}
        errs = S.check_certificate({**self.vis, rel: S.Visible(rel, text)}, pages)
        self.assertTrue(any("order differs" in e for e in errs), errs)


class FreshnessChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages, _, _ = S.render()
        cls.lesson = "academy/foundations/what-is-a-model.html"

    def test_every_lesson_and_tutorial_shows_one_verified_date(self):
        for rel, text in self.pages.items():
            if S.page_type(rel) in ("lesson", "tutorial"):
                v = S.Visible(rel, text)
                self.assertIsNotNone(v.verified, rel)
                self.assertEqual(v.verified_count, 1, rel)
                self.assertIn(f'"dateModified": "{v.verified}"', text, rel)

    def test_build_is_idempotent_and_replaces_the_date(self):
        text = self.pages[self.lesson]
        self.assertEqual(S.build_verified(self.lesson, text, S.Visible(self.lesson, text).verified), text)
        moved = S.build_verified(self.lesson, text, "2025-01-02")
        self.assertEqual(S.Visible(self.lesson, moved).verified, "2025-01-02")
        self.assertEqual(S.Visible(self.lesson, moved).verified_count, 1)
        self.assertIn(">2 Jan 2025</time>", moved)
        self.assertEqual(S.build_verified(self.lesson, moved, "2025-01-02"), moved)

    def test_the_date_is_not_content(self):
        # lastmod reads content_key; if the Verified line counted, every build
        # would move the date it writes.
        text = self.pages[self.lesson]
        moved = S.build_verified(self.lesson, text, "2025-01-02")
        self.assertEqual(S.content_key(moved), S.content_key(text))
        self.assertEqual(S.strip_verified(moved),
                         S.strip_verified(re.sub(r'"dateModified": "[^"]*"', '"dateModified": "2025-01-02"', text)))

    def test_jsonld_date_must_match_the_visible_date(self):
        text = self.pages[self.lesson]
        moved = S.build_verified(self.lesson, text, "2025-01-02")   # JSON-LD keeps the old date
        pages = {**self.pages, self.lesson: moved}
        vis = {r: S.Visible(r, t) for r, t in pages.items()}
        errs, _ = S.check_jsonld(self.lesson, moved, vis, set(pages), pages)
        self.assertTrue(any("is not the visible Updated/Verified date '2025-01-02'" in e for e in errs), errs)

    def test_stale_and_future_dates(self):
        text = self.pages[self.lesson]
        today = dt.date(2026, 11, 13)
        old = S.Visible(self.lesson, S.build_verified(self.lesson, text, "2026-05-01"))
        fresh = S.Visible(self.lesson, S.build_verified(self.lesson, text, "2026-06-01"))
        future = S.Visible(self.lesson, S.build_verified(self.lesson, text, "2026-12-01"))
        self.assertEqual(S.check_freshness({self.lesson: old}, today), ([], [f"{self.lesson} (2026-05-01, 196 days)"]))
        self.assertEqual(S.check_freshness({self.lesson: fresh}, today), ([], []))
        self.assertTrue(any("in the future" in e for e in S.check_freshness({self.lesson: future}, today)[0]))
        missing = S.Visible(self.lesson, S.VERIFIED_REGION_RE.sub("", text))
        self.assertTrue(any("no Updated/Verified date" in e for e in S.check_freshness({self.lesson: missing}, today)[0]))

    def test_git_date_is_labelled_updated(self):
        # R1 S5: the last content-commit date is not a verification.
        text = self.pages[self.lesson]
        self.assertIsNone(S.verified_by_hand(self.lesson))
        self.assertIn("<strong>Updated:</strong> <time", text)
        self.assertNotIn("<strong>Verified:</strong>", text)
        self.assertEqual(S.Visible(self.lesson, text).verified_label, "Updated")
        relabelled = text.replace("<strong>Updated:</strong>", "<strong>Verified:</strong>")
        errs = S.check_freshness({self.lesson: S.Visible(self.lesson, relabelled)}, dt.date(2026, 11, 13))[0]
        self.assertTrue(any("labelled 'Verified', want 'Updated'" in e for e in errs), errs)

    def test_site_toml_override_wins(self):
        pages_cfg = S.CFG.setdefault("pages", {})
        saved = pages_cfg.get(self.lesson)
        pages_cfg[self.lesson] = {**(saved or {}), "verified": "2026-10-01"}
        try:
            self.assertEqual(S.verified_date(self.lesson, self.pages[self.lesson]), "2026-10-01")
            built = S.build_verified(self.lesson, self.pages[self.lesson], "2026-10-01")
            self.assertIn('<strong>Verified:</strong> <time datetime="2026-10-01">1 Oct 2026</time>', built)
            v = S.Visible(self.lesson, built)
            self.assertEqual(v.verified_label, "Verified")
            self.assertEqual(S.check_freshness({self.lesson: v}, dt.date(2026, 11, 13))[0], [])
            # a hand-set date the page doesn't show yet, or an "Updated" label, fails
            stale = S.Visible(self.lesson, self.pages[self.lesson])
            errs = S.check_freshness({self.lesson: stale}, dt.date(2026, 11, 13))[0]
            self.assertTrue(any("labelled 'Updated', want 'Verified'" in e for e in errs), errs)
            wrong = S.Visible(self.lesson, S.build_verified(self.lesson, self.pages[self.lesson], "2026-10-02"))
            errs = S.check_freshness({self.lesson: wrong}, dt.date(2026, 11, 13))[0]
            self.assertTrue(any("is not site.toml's 2026-10-01" in e for e in errs), errs)
        finally:
            if saved is None:
                del pages_cfg[self.lesson]
            else:
                pages_cfg[self.lesson] = saved


class BoxChecks(unittest.TestCase):
    BOXES = {"demo-box": "<!--\nsources: here\n-->\n<aside class=\"sbox\" data-box=\"demo-box\"><p>Demo v1</p></aside>\n"}

    def page(self, marker: str) -> str:
        return f"<main>\n<p>Before</p>\n{marker}\n<p>After</p>\n</main>\n"

    def test_real_boxes_and_pages_pass(self):
        self.assertTrue(S.BOXES, "no _build/boxes/*.html found")
        self.assertEqual(S.box_file_errors(S.BOXES), [])
        self.assertEqual(S.check_boxes(real_pages(), S.BOXES), [])

    def test_real_boxes_expand(self):
        for name in S.BOXES:
            out = S.build_boxes(self.page(f"<!-- box:{name} -->"))
            self.assertIn(S.box_begin(name), out)
            self.assertEqual(S.build_boxes(out), out, name)
            self.assertEqual(S.check_boxes({"p.html": out}, S.BOXES), [], name)

    def test_marker_expands_once_and_build_is_idempotent(self):
        once = S.build_boxes(self.page("<!-- box:demo-box -->"), self.BOXES)
        self.assertIn(S.box_begin("demo-box") + "\n<aside", once)
        self.assertIn("</aside>\n" + S.box_end("demo-box"), once)
        self.assertNotIn("sources: here", once)            # the doc comment stays in the box file
        self.assertTrue(once.startswith("<main>\n<p>Before</p>\n") and once.endswith("\n<p>After</p>\n</main>\n"))
        self.assertEqual(S.build_boxes(once, self.BOXES), once)
        self.assertEqual(S.check_boxes({"p.html": once}, self.BOXES), [])

    def test_indented_marker_indents_the_box(self):
        text = "<main>\n    <!-- box:demo-box -->\n</main>\n"
        once = S.build_boxes(text, self.BOXES)
        self.assertIn("\n    " + S.box_begin("demo-box") + "\n    <aside", once)
        self.assertIn("</aside>\n    " + S.box_end("demo-box") + "\n</main>", once)
        self.assertEqual(S.build_boxes(once, self.BOXES), once)
        self.assertEqual(S.check_boxes({"p.html": once}, self.BOXES), [])

    def test_changed_box_rewrites_the_region_in_place(self):
        once = S.build_boxes(self.page("<!-- box:demo-box -->"), self.BOXES)
        v2 = {"demo-box": self.BOXES["demo-box"].replace("Demo v1", "Demo v2")}
        twice = S.build_boxes(once, v2)
        self.assertIn("Demo v2", twice)
        self.assertNotIn("Demo v1", twice)
        self.assertEqual(twice.count(S.box_begin("demo-box")), 1)
        self.assertEqual(twice.count(S.box_end("demo-box")), 1)
        # until rebuilt, the old region reads as a hand edit
        self.assertTrue(any("edited in the page" in e for e in S.check_boxes({"p.html": once}, v2)))

    def test_unknown_box_is_left_alone_and_fails(self):
        text = self.page("<!-- box:no-such-box -->")
        self.assertEqual(S.build_boxes(text, self.BOXES), text)
        errs = S.check_boxes({"p.html": text}, self.BOXES)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("p.html:3: unknown box 'no-such-box'", errs[0])

    def test_unknown_region_fails(self):
        once = S.build_boxes(self.page("<!-- box:demo-box -->"), self.BOXES)
        errs = S.check_boxes({"p.html": once}, {})
        self.assertTrue(any("unknown box 'demo-box'" in e for e in errs), errs)

    def test_hand_edit_inside_the_region_fails(self):
        once = S.build_boxes(self.page("<!-- box:demo-box -->"), self.BOXES)
        edited = once.replace("Demo v1", "Demo (fixed by hand)")
        errs = S.check_boxes({"p.html": edited}, self.BOXES)
        self.assertTrue(any("box 'demo-box' was edited in the page" in e for e in errs), errs)
        self.assertEqual(S.build_boxes(edited, self.BOXES), once)   # build restores it

    def test_unpaired_region_markers_fail(self):
        once = S.build_boxes(self.page("<!-- box:demo-box -->"), self.BOXES)
        no_end = once.replace(S.box_end("demo-box"), "")
        no_begin = once.replace(S.box_begin("demo-box"), "")
        for text in (no_end, no_begin):
            errs = S.check_boxes({"p.html": text}, self.BOXES)
            self.assertTrue(any("malformed or unpaired box marker" in e for e in errs), errs)

    def test_malformed_markers_fail(self):
        for marker in ("<!-- box:Demo_Box -->", "<!-- box: demo-box -->", "<!-- box:demo-box- -->",
                       "<!-- /box:demo-box -->", "<!--box:-->"):
            errs = S.check_boxes({"p.html": self.page(marker)}, self.BOXES)
            self.assertTrue(any("malformed or unpaired box marker" in e for e in errs), (marker, errs))
            self.assertEqual(S.build_boxes(self.page(marker), self.BOXES), self.page(marker))

    def test_box_file_rules(self):
        ok = '<figure class="sbox-fig" data-box="fine"><svg class="sbox-svg"><rect class="sv-node" fill="none"></rect></svg>' \
             '<a href="#abc">x</a> &#123;</figure>'
        self.assertEqual(S.box_file_errors({"fine": ok}), [])
        bad = {
            "inline-style": '<p style="margin:0">x</p>',
            "style-element": "<style>.a{}</style><p>x</p>",
            "hex": "<p>colour #1a1a1a</p>",
            "fn": '<svg><rect class="a"></rect></svg><p>rgb(0, 0, 0)</p>',
            "attr": '<svg><rect fill="currentColor"></rect></svg>',
            "stroke": '<svg><line stroke="black"></line></svg>',
            "nested": "<p>x</p><!-- box:other -->",
            "empty": "<!-- only a doc comment -->\n",
            "Bad_Name": "<p>x</p>",
            "no-id": '<aside class="sbox"><p>x</p></aside>',
            "wrong-id": '<aside class="sbox" data-box="other"><p>x</p></aside>',
        }
        for name, body in bad.items():
            self.assertTrue(S.box_file_errors({name: body}), name)

    def test_doc_comment_is_not_scanned_for_colours(self):
        self.assertEqual(S.box_file_errors({"doc": "<!-- was #000000, style=\"x\" -->\n<p data-box=\"doc\">x</p>"}), [])

    def test_hand_copied_box_without_its_region_fails(self):
        # R1 S3: strip the region comments, then edit the copy: still caught.
        once = S.build_boxes(self.page("<!-- box:demo-box -->"), self.BOXES)
        forked = once.replace(S.box_begin("demo-box"), "").replace(S.box_end("demo-box"), "")
        forked = forked.replace("Demo v1", "Demo (forked)")
        errs = S.check_boxes({"p.html": forked}, self.BOXES)
        self.assertTrue(any("a copy of box 'demo-box' outside a generated box region" in e for e in errs), errs)
        # ... and with the id removed too, the box classes still give it away
        no_id = forked.replace(' data-box="demo-box"', "")
        errs = S.check_boxes({"p.html": no_id}, self.BOXES)
        self.assertTrue(any("box markup" in e and "outside a generated box region" in e for e in errs), errs)

    def test_real_page_with_a_forked_box_fails(self):
        rel = "academy/sft-fundamentals/evaluation-fundamentals.html"
        text = real_pages()[rel]
        self.assertIn(S.box_begin("small-test-sets"), text)
        forked = text.replace(S.box_begin("small-test-sets"), "").replace(S.box_end("small-test-sets"), "")
        forked = forked.replace("22 right (44%)", "23 right (46%)", 1)
        errs = S.check_boxes({rel: forked}, S.BOXES)
        self.assertTrue(any(e.startswith(rel) and "a copy of box 'small-test-sets'" in e for e in errs), errs)

    def test_marker_inside_a_paragraph_fails(self):
        text = "<main>\n<p>Before <!-- box:demo-box --> after</p>\n</main>\n"
        errs = S.check_boxes({"p.html": text}, self.BOXES)
        self.assertTrue(any("sits inside a <p>" in e for e in errs), errs)
        multi = "<main>\n<p>Before\n  <!-- box:demo-box -->\nafter</p>\n</main>\n"
        self.assertTrue(any("sits inside a <p>" in e for e in S.check_boxes({"p.html": multi}, self.BOXES)))


if __name__ == "__main__":
    unittest.main()
