"""Mutation tests for the quiz and certificate checks in _build/site.py.

Each test breaks a copy of the real pages in memory (never on disk) and
asserts that `check` would catch it. Run with:

  python3 -m unittest discover -s _build -p 'test_*.py'
"""
from __future__ import annotations

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

    def test_positional_option_fails(self):
        rel, quiz = next(iter(sorted(self.quizzes.items())))
        q = dict(quiz[0])
        q["options"] = q["options"][:-1] + ["None of the above"] if q["answer"] != len(q["options"]) - 1 \
            else ["All of the above"] + q["options"][1:]
        self.assertTrue(any("by position" in e for e in S.quiz_shape_errors([q] + quiz[1:])))
        q2 = {**quiz[0], "explanation": "Option B is right because ..."}
        self.assertTrue(any("by position" in e for e in S.quiz_shape_errors([q2])))

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


if __name__ == "__main__":
    unittest.main()
