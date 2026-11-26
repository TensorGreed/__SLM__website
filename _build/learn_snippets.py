#!/usr/bin/env python3
"""Rerun the code on BrewSLM Learn pages and check the output they show.

Standard library only; the snippets themselves run in a separate venv.

A page marks a runnable block and the output it claims like this:

    <pre class="code-block" data-run="fixed"><code>print(...)</code></pre>
    <pre class="code-block code-output" data-output-of="fixed"><code>...</code></pre>

All data-run blocks on a page run top to bottom in one Python session (later
blocks see earlier names), in a working folder kept per page under the cache
dir, so a download made by one run is reused by the next. A block's captured
stdout must equal its output block line by line (trailing spaces and leading /
trailing blank lines ignored), except inside spans marked as nondeterministic:

    <span class="nd" data-nd="number">31.4</span>   any number (timings)
    <span class="nd" data-nd="any">...</span>       any text on that line

A page that passes gets a stamp in _build/learn_stamps.json (code hash, date,
versions); `site.py check` fails when the code, the shown output (outside nd
spans), a data file or a Tested-on version no longer matches it.

Versions on the page's "Tested on" line (<span data-pkg="NAME">X.Y.Z</span>,
NAME "python" for the interpreter) must match the venv. Files named in the
article's data-run-files attribute (paths relative to the page, separated by
spaces) are copied into the working folder before the run.

Usage:
  python3 _build/learn_snippets.py setup          # create the venv from _build/learn-requirements.txt
  python3 _build/learn_snippets.py run [PAGE...]  # rerun and compare (default: every learn/ page)
  python3 _build/learn_snippets.py run --show P   # ... and print what each block printed
  python3 _build/learn_snippets.py run --fresh P  # empty the page's working folder first

Everything runs on CPU: CUDA_VISIBLE_DEVICES is set to "". The venv, model
caches and working folders live under ~/.cache/brewslm/learn (override with
BREWSLM_LEARN_CACHE).

Pages that generate text call a local Ollama at $OLLAMA_HOST. The runner sets
OLLAMA_HOST to its own CPU-only instance, 127.0.0.1:6572 (override with
BREWSLM_LEARN_OLLAMA), never the default port, so a run can't reach a shared
server. Start that instance first:

  CUDA_VISIBLE_DEVICES="" OLLAMA_HOST=127.0.0.1:6572 \
      OLLAMA_MODELS=~/.cache/brewslm/learn/ollama-models ollama serve
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BREWSLM_LEARN_CACHE", Path.home() / ".cache" / "brewslm" / "learn"))
VENV = CACHE / "venv"
REQS = ROOT / "_build" / "learn-requirements.txt"
# Written after a page passes: its code hash, run date and versions. `site.py check`
# fails when a page's code no longer matches its stamp or its Tested-on date is
# later than the last passing run.
STAMPS = ROOT / "_build" / "learn_stamps.json"
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
# The runner's own Ollama (CPU only). Pages read $OLLAMA_HOST; never the default :11434.
OLLAMA = os.environ.get("BREWSLM_LEARN_OLLAMA", "127.0.0.1:6572")
NUMBER = r"-?\d+(?:[.,]\d+)*"


class Blocks(HTMLParser):
    """Collects runnable blocks, output blocks, package versions and data files."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.runs: list[tuple[str, str]] = []        # (id, code)
        self.outputs: dict[str, list] = {}           # id -> [("text", s) | ("nd", kind, s)]
        self.pkgs: dict[str, str] = {}
        self.files: list[str] = []
        self._cur = None                             # ("run", id, buf) | ("out", id, parts)
        self._nd = None
        self._pkg = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "article" and a.get("data-run-files"):
            self.files += a["data-run-files"].split()
        if tag == "pre" and "data-run" in a:
            self._cur = ("run", a["data-run"], [])
        elif tag == "pre" and "data-output-of" in a:
            self._cur = ("out", a["data-output-of"], [])
        elif tag == "span" and "data-nd" in a and self._cur and self._cur[0] == "out":
            self._nd = [a["data-nd"], ""]
        elif tag == "span" and "data-pkg" in a:
            self._pkg = [a["data-pkg"], ""]

    def handle_endtag(self, tag):
        if tag == "span" and self._nd is not None:
            self._cur[2].append(("nd", self._nd[0], self._nd[1]))
            self._nd = None
        elif tag == "span" and self._pkg is not None:
            self.pkgs[self._pkg[0]] = self._pkg[1].strip()
            self._pkg = None
        elif tag == "pre" and self._cur:
            kind, bid, buf = self._cur
            if kind == "run":
                self.runs.append((bid, "".join(buf)))
            else:
                self.outputs[bid] = buf
            self._cur = None

    def handle_data(self, data):
        if self._pkg is not None:
            self._pkg[1] += data
        if self._nd is not None:
            self._nd[1] += data
        elif self._cur and self._cur[0] == "run":
            self._cur[2].append(data)
        elif self._cur and self._cur[0] == "out":
            self._cur[2].append(("text", data))


def parse(path: Path) -> Blocks:
    b = Blocks()
    b.feed(path.read_text(encoding="utf-8"))
    b.close()
    return b


def expected_regex(parts: list) -> tuple[str, re.Pattern]:
    """The shown output as plain text (for diffs) and as a regex that lets the nd spans vary."""
    shown, marked, kinds = "", "", []
    for p in parts:
        if p[0] == "text":
            shown += p[1]
            marked += p[1]
        else:
            shown += p[2]
            marked += f"\x00{len(kinds)}\x00"
            kinds.append(p[1])
    rx = re.escape(norm(marked))
    for i, kind in enumerate(kinds):
        rx = rx.replace(re.escape(f"\x00{i}\x00"), NUMBER if kind == "number" else r"[^\n]*?")
    return norm(shown), re.compile(rx)


ND_MARK = "\x00nd\x00"


def page_hash(b: "Blocks", page: Path) -> str:
    """Hash of what a passing run vouches for: the code, the shown output (nd spans
    masked) and the bytes of the data files. site.py computes the same."""
    outs = ["".join(p[1] if p[0] == "text" else ND_MARK for p in b.outputs.get(bid, [])) for bid, _ in b.runs]
    files = [f"{f}\n{hashlib.sha256((page.parent / f).read_bytes()).hexdigest()}" for f in b.files]
    payload = "\n\x01\n".join(["\n\x00\n".join(code for _, code in b.runs), "\n\x00\n".join(outs),
                                "\n\x00\n".join(files)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def norm(s: str) -> str:
    return "\n".join(ln.rstrip() for ln in s.strip("\n").split("\n"))


DRIVER = r'''
import contextlib, io, json, sys, time, traceback
blocks = json.load(open(sys.argv[1]))
ns = {"__name__": "__main__"}
out = []
for bid, code in blocks:
    buf = io.StringIO()
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, f"<block {bid}>", "exec"), ns)
        err = None
    except Exception:
        err = traceback.format_exc()
    out.append({"id": bid, "stdout": buf.getvalue(), "error": err, "seconds": round(time.time() - t0, 1)})
    if err:
        break
import importlib.metadata as md
vers = {"python": sys.version.split()[0]}
for name in json.load(open(sys.argv[2])):
    if name != "python":
        try:
            vers[name] = md.version(name)
        except md.PackageNotFoundError:
            vers[name] = None
json.dump({"blocks": out, "versions": vers}, open(sys.argv[3], "w"))
'''


def run_page(rel: str, show: bool, fresh: bool) -> list[str]:
    page = ROOT / rel
    b = parse(page)
    errs: list[str] = []
    if not b.runs:
        return [f"{rel}: no data-run blocks"]
    work = CACHE / "work" / rel.replace("/", "__").removesuffix(".html")
    if fresh and work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    names = [Path(f).name for f in b.files]
    if len(set(names)) != len(names):
        return [f"{rel}: two data-run-files share a file name"]
    for f in b.files:
        src = (page.parent / f).resolve()
        shutil.copy(src, work / src.name)
    (work / "_driver.py").write_text(DRIVER, encoding="utf-8")
    (work / "_blocks.json").write_text(json.dumps(b.runs), encoding="utf-8")
    (work / "_pkgs.json").write_text(json.dumps(sorted(b.pkgs)), encoding="utf-8")
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "TOKENIZERS_PARALLELISM": "false",
           "PYTHONHASHSEED": "0", "HF_HOME": str(CACHE / "hf"),
           "SENTENCE_TRANSFORMERS_HOME": str(CACHE / "hf" / "sentence-transformers"),
           "OLLAMA_HOST": OLLAMA}
    py = VENV / "bin" / "python"
    if not py.exists():
        return [f"no venv at {VENV}; run `python3 _build/learn_snippets.py setup` first"]
    # -P: the working folder holds downloaded files; never import from it. (Not -I, which
    # would also ignore PYTHONHASHSEED.) -s: no user site-packages.
    r = subprocess.run([str(py), "-P", "-s", "_driver.py", "_blocks.json", "_pkgs.json", "_result.json"],
                       cwd=work, env=env, capture_output=True, text=True)
    if r.returncode != 0 or not (work / "_result.json").exists():
        return [f"{rel}: the driver failed:\n{r.stderr[-3000:]}"]
    res = json.loads((work / "_result.json").read_text(encoding="utf-8"))
    for name, want in sorted(b.pkgs.items()):
        got = res["versions"].get(name)
        if got != want:
            errs.append(f"{rel}: Tested-on line says {name} {want}, the venv has {got}")
    ran = {x["id"]: x for x in res["blocks"]}
    for bid, _ in b.runs:
        x = ran.get(bid)
        if x is None:
            errs.append(f"{rel}: block {bid!r} did not run (an earlier block failed)")
            continue
        if x["error"]:
            errs.append(f"{rel}: block {bid!r} raised:\n{x['error']}")
            continue
        got = norm(x["stdout"])
        if show:
            # HTML-escaped, ready to paste into the output block
            print(f"── {rel} · {bid} ({x['seconds']} s), HTML-escaped:\n{html.escape(got, quote=False)}\n")
        if bid not in b.outputs:
            errs.append(f"{rel}: block {bid!r} has no data-output-of block")
            continue
        shown, rx = expected_regex(b.outputs[bid])
        if not rx.fullmatch(got):
            diff = "\n".join(difflib.unified_diff(shown.split("\n"), got.split("\n"), "page", "run", lineterm=""))
            errs.append(f"{rel}: block {bid!r} output differs from the page:\n{diff}")
        else:
            print(f"ok   {rel} · {bid} ({x['seconds']} s)")
    for bid in b.outputs:
        if bid not in ran and bid not in dict(b.runs):
            errs.append(f"{rel}: output block for unknown run {bid!r}")
    if not errs:
        stamps = json.loads(STAMPS.read_text(encoding="utf-8")) if STAMPS.exists() else {}
        stamps[rel] = {"page_sha256": page_hash(b, page), "last_pass": eastern_today(),
                       "versions": res["versions"]}
        STAMPS.write_text(json.dumps(stamps, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return errs


def eastern_today() -> str:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def cmd_setup(_args) -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    if not (VENV / "bin" / "python").exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    pip = [str(VENV / "bin" / "python"), "-m", "pip", "install", "-q"]
    torch = [ln for ln in REQS.read_text().splitlines() if ln.startswith("torch==")]
    subprocess.run(pip + torch + ["--index-url", TORCH_INDEX], check=True)
    subprocess.run(pip + ["-r", str(REQS)], check=True)
    print(f"setup: venv ready at {VENV}")
    return 0


def learn_pages() -> list[str]:
    return sorted(str(p.relative_to(ROOT)) for p in (ROOT / "learn").glob("*/*.html") if p.name != "index.html")


def cmd_run(args) -> int:
    pages = args.pages or learn_pages()
    errs = []
    for rel in pages:
        rel = str(Path(rel).resolve().relative_to(ROOT)) if Path(rel).is_absolute() or Path(rel).exists() else rel
        errs += run_page(rel, args.show, args.fresh)
    for e in errs:
        print("FAIL", e)
    print(f"learn_snippets: {len(pages)} page(s), {len(errs)} failure(s)")
    return 1 if errs else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="create the snippet venv")
    r = sub.add_parser("run", help="rerun the snippets and compare their output")
    r.add_argument("pages", nargs="*")
    r.add_argument("--show", action="store_true", help="print each block's actual output")
    r.add_argument("--fresh", action="store_true", help="empty the working folder first")
    args = ap.parse_args()
    return {"setup": cmd_setup, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
