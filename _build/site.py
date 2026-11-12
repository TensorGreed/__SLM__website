#!/usr/bin/env python3
"""Tiny build step for brewslm.com. Standard library only.

The hand-written HTML stays the source of truth. This script only:

  * rewrites marker-delimited regions inside pages
      <!-- site:head ... -->  ...  <!-- /site:head -->
    (canonical, robots, Open Graph, Twitter, feed link, deferred analytics),
      <!-- site:jsonld ... -->  ...  <!-- /site:jsonld -->
    (schema.org JSON-LD per page type, read from what the page shows: h1,
    breadcrumb, lesson meta, quiz, FAQ, glossary, blog cards; hand-written
    HowTo and SoftwareApplication blocks outside the region are kept as-is,
    any other hand-written JSON-LD block is replaced by the region);
  * writes whole generated files: sitemap.xml, feed.xml, 404.html.

Usage:
  python3 _build/site.py build    # rewrite regions + generated files
  python3 _build/site.py check    # exit 1 if build would change anything, or a
                                 # link is broken, a page lacks a title, ...

`build` is idempotent: running it twice gives a zero diff. The head values
are derived on every build: og:title / twitter:title from <title>,
og:description / twitter:description from the meta description, og:image from
site.toml's default_image. To override one for a page, add a
[pages."<path>"] table to _build/site.toml (keys: robots, og_title,
og_description, og_image, og_image_alt, twitter_title, twitter_description).
The tooling lives in _build/ so /tools/ stays free for site pages.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import html
import json
import os
import re
import struct
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
CFG = tomllib.loads((ROOT / "_build" / "site.toml").read_text(encoding="utf-8"))
BASE = CFG["base_url"].rstrip("/") + "/"
NOTFOUND = CFG["notfound"]["path"]
# Top-level paths that are not site pages (this tooling lives in _build/).
SKIP_DIRS = {".git", ".github", "node_modules", "_build"}
RASTER = (".png", ".jpg", ".jpeg", ".gif", ".webp")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


# ── discovery + classification ─────────────────────────────────────────────

def discover() -> list[str]:
    out = []
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(x for x in dirs if not (d == str(ROOT) and x in SKIP_DIRS) and x != ".git")
        for f in files:
            if f.endswith(".html"):
                rel = os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/")
                if rel != NOTFOUND:
                    out.append(rel)
    return sorted(out)


def page_type(rel: str) -> str:
    parts = rel.split("/")
    if rel == NOTFOUND:
        return "notfound"
    if rel == "index.html":
        return "home"
    if rel == CFG["feed"]["index"]:
        return "blog_index"
    if len(parts) == 1 and fnmatch.fnmatch(rel, CFG["feed"]["posts"]):
        return "post"
    if parts[0] == "academy":
        if len(parts) == 2:
            return {"index.html": "academy_index", "glossary.html": "glossary",
                    "certificate.html": "certificate",
                    "small-language-model-roadmap.html": "roadmap"}.get(parts[1], "academy_page")
        if parts[-1] == "index.html":
            return "track_index"
        return "tutorial" if parts[1] == "tutorials" else "lesson"
    if parts[0] == "tools":
        return "tool"
    return "top_level"


def url_of(rel: str) -> str:
    if rel == "index.html" or rel.endswith("/index.html"):
        return BASE + rel[: -len("index.html")]
    return BASE + rel


def rel_prefix(rel: str) -> str:
    depth = rel.count("/")
    return "./" if depth == 0 else "../" * depth


# ── HTML scanning ──────────────────────────────────────────────────────────

class Scan(HTMLParser):
    """Collects the facts the build and the checks need from one page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []
        self.title = None
        self._in_title = False
        self.metas: dict[str, str] = {}
        self.links: list[tuple[str, str, int]] = []   # (attr value, kind, line)
        self.headings: list[int] = []
        self.tables: list[tuple[int, bool]] = []     # (line, scroll-safe)
        self.jsonld: list[str] = []
        self._in_ld = False
        self._buf: list[str] = []

    def _classes(self):
        return set().union(*(c for _, c in self.stack)) if self.stack else set()

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        cls = set(a.get("class", "").split())
        line = self.getpos()[0]
        if tag == "title":
            self._in_title = True
            self._buf = []
        elif tag == "meta":
            key = a.get("name") or a.get("property")
            if key and key not in self.metas:
                self.metas[key] = a.get("content", "")
        elif tag == "a" and "href" in a:
            self.links.append((a["href"], "a", line))
        elif tag == "link" and "href" in a and set(a.get("rel", "").split()) & {"stylesheet", "icon"}:
            self.links.append((a["href"], "link", line))
        elif tag in ("img", "script", "source") and a.get("src"):
            self.links.append((a["src"], tag, line))
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        if tag == "table":
            safe = bool(cls & {"comparison-table", "mini-table"}) or "table-wrap" in self._classes()
            self.tables.append((line, safe))
        if tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld = True
            self._buf = []
        if tag not in VOID:
            self.stack.append((tag, cls))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self.title = "".join(self._buf).strip()
            self._in_title = False
        if tag == "script" and self._in_ld:
            self.jsonld.append("".join(self._buf))
            self._in_ld = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._in_title or self._in_ld:
            self._buf.append(data)


def scan(text: str) -> Scan:
    s = Scan()
    s.feed(text)
    s.close()
    return s


def jsonld_field(s: Scan, field: str):
    def walk(o):
        if isinstance(o, list):
            for x in o:
                r = walk(x)
                if r is not None:
                    return r
        elif isinstance(o, dict):
            if field in o:
                return o[field]
            if "@graph" in o:
                return walk(o["@graph"])
        return None
    for block in s.jsonld:
        try:
            r = walk(json.loads(block))
        except ValueError:
            continue
        if r is not None:
            return r
    return None


# ── image sizes ────────────────────────────────────────────────────────────

def image_size(path: Path):
    try:
        with open(path, "rb") as f:
            head = f.read(32)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", head[6:10])
    return None


def local_file_for_url(u: str):
    p = urlparse(u)
    if p.netloc and p.netloc != urlparse(BASE).netloc:
        return None
    return ROOT / p.path.lstrip("/")


# ── site:head region ───────────────────────────────────────────────────────

TAG_RE = re.compile(
    r"<!--\s*/?site:head\b.*?-->"
    r"|<(?:meta|link)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>"
    r"|<script\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>\s*</script>",
    re.S | re.I)


def tag_attrs(tag: str) -> tuple[str, dict]:
    out: dict = {}
    name = [""]

    class P(HTMLParser):
        def handle_starttag(self, t, attrs):
            name[0] = t
            out.update({k: (v or "") for k, v in attrs})
        handle_startendtag = handle_starttag
    P(convert_charrefs=True).feed(tag)
    return name[0], out


def managed_key(tag: str):
    """Key for a tag the head region owns, or None."""
    if tag.startswith("<!--"):
        return "marker"
    name, a = tag_attrs(tag)
    if name == "meta":
        k = a.get("property") or a.get("name") or ""
        if k == "robots" or k.startswith(("og:", "article:", "twitter:")):
            return k
    elif name == "link":
        rel = a.get("rel", "")
        if rel == "canonical":
            return "canonical"
        if rel == "alternate" and re.search(r"(atom|rss)\+xml", a.get("type", "")):
            return "feed"
    elif name == "script" and a.get("src", "").endswith("assets/analytics.js"):
        return "analytics"
    return None


KNOWN = {"robots", "canonical", "feed", "analytics", "og:type", "og:site_name", "og:title",
         "og:description", "og:url", "og:image", "og:image:width", "og:image:height",
         "og:image:alt", "article:published_time", "article:modified_time", "twitter:card",
         "twitter:title", "twitter:description", "twitter:image", "twitter:image:alt"}


def esc(v: str) -> str:
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen [start, end) to swallow the tag's own line (leading newline + indent)."""
    ls = text.rfind("\n", 0, start)
    if ls != -1 and text[ls + 1:start].strip() == "":
        start = ls
    le = text.find("\n", end)
    if le != -1 and text[end:le].strip() == "":
        end = le
    return start, end


OVERRIDE_KEYS = ("robots", "og_title", "og_description", "og_image", "og_image_alt",
                 "twitter_title", "twitter_description", "date_modified")


def head_values(rel: str, s: "Scan") -> dict:
    """The managed head values for a page: derived on every build from its
    <title>, meta description and site.toml, unless site.toml's [pages."<rel>"]
    table overrides a key."""
    ov = CFG.get("pages", {}).get(rel, {})
    unknown = set(ov) - set(OVERRIDE_KEYS)
    if unknown:
        raise SystemExit(f"site.toml [pages.\"{rel}\"]: unknown key(s) {sorted(unknown)}")
    ptype = page_type(rel)
    v = {"canonical": url_of(rel)}
    v["robots"] = ov.get("robots", CFG["robots"])
    v["og_type"] = "article" if ptype in ("post", "lesson", "tutorial") else "website"
    v["og_title"] = ov.get("og_title", s.title or "")
    v["og_description"] = ov.get("og_description", s.metas.get("description", ""))
    img = ov.get("og_image", CFG["default_image"])
    v["og_image"] = img if urlparse(img).scheme else BASE + img.lstrip("/")
    default_alt = CFG["default_image_alt"] if img == CFG["default_image"] else v["og_title"]
    v["og_image_alt"] = ov.get("og_image_alt", default_alt)
    v["twitter_title"] = ov.get("twitter_title", v["og_title"])
    v["twitter_description"] = ov.get("twitter_description", v["og_description"])
    lf = local_file_for_url(v["og_image"])
    v["image_size"] = image_size(lf) if lf is not None else None
    v["published"] = jsonld_field(s, "datePublished")
    v["modified"] = jsonld_field(s, "dateModified")
    return v


def comment_spans(head: str) -> list[tuple[int, int]]:
    """HTML comments in the head other than the site:head markers."""
    return [c.span() for c in re.finditer(r"<!--.*?-->", head, re.S)
            if not re.match(r"<!--\s*/?site:head\b", c.group(0))]


def build_head(rel: str, text: str) -> str:
    m = re.search(r"<head\b[^>]*>(.*?)</head>", text, re.S | re.I)
    if not m:
        return text
    h0, h1 = m.span(1)
    head = text[h0:h1]
    comments = comment_spans(head)
    extra: list[str] = []      # og:/article:/twitter: tags this script does not generate
    spans = []
    for t in TAG_RE.finditer(head):
        if any(a <= t.start() < b for a, b in comments):
            continue           # commented-out tags are left alone
        k = managed_key(t.group(0))
        if not k:
            continue
        if k not in KNOWN and k != "marker":
            _, a = tag_attrs(t.group(0))
            attr = "name" if k.startswith("twitter:") else "property"
            extra.append(f'<meta {attr}="{esc(k)}" content="{esc(a.get("content", ""))}" />')
        spans.append(line_span(head, *t.span()))
    if spans:
        ins = spans[0][0]
        indent = re.match(r"\n?([ \t]*)", head[ins:]).group(1)
    else:              # no anchor yet: append the region at the end of <head>
        ins = len(head.rstrip())
        indent = (re.findall(r"\n([ \t]*)\S", head) or ["    "])[-1]

    v = head_values(rel, scan(text))
    lines = ["<!-- site:head: generated by _build/site.py from <title>, meta description and "
             "_build/site.toml; edit those, not these tags -->"]
    lines.append(f'<meta name="robots" content="{esc(v["robots"])}" />')
    lines.append(f'<link rel="canonical" href="{esc(v["canonical"])}" />')
    lines.append(f'<link rel="alternate" type="application/atom+xml" title="{esc(CFG["feed"]["title"])}" '
                 f'href="{esc(BASE + CFG["feed"]["path"])}" />')
    lines.append(f'<meta property="og:type" content="{v["og_type"]}" />')
    lines.append(f'<meta property="og:site_name" content="{esc(CFG["site_name"])}" />')
    lines.append(f'<meta property="og:title" content="{esc(v["og_title"])}" />')
    lines.append(f'<meta property="og:description" content="{esc(v["og_description"])}" />')
    lines.append(f'<meta property="og:url" content="{esc(v["canonical"])}" />')
    lines.append(f'<meta property="og:image" content="{esc(v["og_image"])}" />')
    if v["image_size"]:
        lines.append(f'<meta property="og:image:width" content="{v["image_size"][0]}" />')
        lines.append(f'<meta property="og:image:height" content="{v["image_size"][1]}" />')
    lines.append(f'<meta property="og:image:alt" content="{esc(v["og_image_alt"])}" />')
    if v["og_type"] == "article" and v["published"]:
        lines.append(f'<meta property="article:published_time" content="{esc(str(v["published"]))}" />')
    if v["og_type"] == "article" and v["modified"]:
        lines.append(f'<meta property="article:modified_time" content="{esc(str(v["modified"]))}" />')
    lines += extra
    lines.append('<meta name="twitter:card" content="summary_large_image" />')
    lines.append(f'<meta name="twitter:title" content="{esc(v["twitter_title"])}" />')
    lines.append(f'<meta name="twitter:description" content="{esc(v["twitter_description"])}" />')
    lines.append(f'<meta name="twitter:image" content="{esc(v["og_image"])}" />')
    lines.append(f'<meta name="twitter:image:alt" content="{esc(v["og_image_alt"])}" />')
    lines.append(f'<script defer src="{rel_prefix(rel)}assets/analytics.js"></script>')
    lines.append("<!-- /site:head -->")

    new_head = head
    for a, b in reversed(spans):
        new_head = new_head[:a] + new_head[b:]
    block = "".join(f"\n{indent}{ln}" for ln in lines)
    new_head = new_head[:ins] + block + new_head[ins:]
    return text[:h0] + new_head + text[h1:]


# ── visible-content facts ─────────────────────────────────────────────────
# Everything the JSON-LD says is read from what a visitor sees on the page
# (h1, breadcrumb, lesson meta, quiz, FAQ, glossary, blog cards), plus the
# meta description and the few site-wide facts in site.toml.

_BLOCK_TAGS = r"br|p|li|ul|ol|div|dd|dt|tr|td|th|h[1-6]|section|article|pre|table|summary|details|blockquote"


def text_of(fragment: str) -> str:
    """Visible text of an HTML fragment: tags dropped, entities decoded,
    whitespace collapsed (block tags count as a space)."""
    s = re.sub(r"<!--.*?-->|<script\b.*?</script>|<style\b.*?</style>", " ", fragment, flags=re.S | re.I)
    s = re.sub(rf"</?(?:{_BLOCK_TAGS})\b[^>]*>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return " ".join(html.unescape(s).split())


def norm_url(u: str) -> str:
    """Absolute URL with a trailing index.html folded to the directory."""
    p = urlparse(u)
    path = p.path[: -len("index.html")] if p.path.endswith("/index.html") else p.path
    return f"{p.scheme}://{p.netloc}{path}" + (f"#{p.fragment}" if p.fragment else "")


def abs_url(rel: str, href: str) -> str:
    return norm_url(urljoin(url_of(rel), html.unescape(href)))


def body_of(text: str) -> str:
    i = text.find("<body")
    return text[i:] if i != -1 else text


def main_of(text: str) -> str:
    m = re.search(r"<main\b[^>]*>(.*?)</main>", text, re.S | re.I)
    return m.group(1) if m else body_of(text)


def first(pattern: str, text: str, flags=re.S):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


def duration_minutes(s) -> int | None:
    """Minutes in a visible time ('~9 min', '~2.5 hours total') or an ISO 8601
    duration ('PT2H30M')."""
    if not s:
        return None
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", str(s).strip())
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?|h)\b", str(s), re.I)
    if not m:
        return None
    n = float(m.group(1))
    return round(n * 60) if m.group(2).lower().startswith("h") else round(n)


def iso_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m or not h else "")


class Facts:
    """What a visitor can read on one page."""

    def __init__(self, rel: str, text: str):
        self.rel, self.text = rel, text
        self.url = url_of(rel)
        s = scan(text)
        self.title = s.title or ""
        self.description = s.metas.get("description", "")
        body = body_of(text)
        self.h1 = text_of(first(r"<h1\b[^>]*>(.*?)</h1>", body) or "")
        self.crumbs = self._crumbs(body)
        self.level = text_of(first(r"<strong>Level:</strong>(.*?)</span>", body) or "") or None
        self.time = text_of(first(r"<strong>(?:Read time|Time):</strong>(.*?)</span>", body) or "") or None
        self.outcome = text_of(first(r'class="lesson-header__outcome"[^>]*>(.*?)</p>', body) or "") or None
        self.published = first(r'class="blog-post-meta"[^>]*>\s*<time datetime="([^"]+)"', body)
        q = first(r"<section\b[^>]*\bdata-quiz\b[^>]*>.*?<script type=\"application/json\">(.*?)</script>", body)
        self.quiz = json.loads(q) if q else None
        self.quiz_title = text_of(first(r'class="quiz__title"[^>]*>(.*?)</h2>', body) or "") or None

    def _crumbs(self, body: str) -> list[tuple[str, str]]:
        nav = first(r'<nav class="breadcrumb[^"]*"[^>]*>(.*?)</nav>', body)
        if not nav:
            return []
        out = []
        for li in re.findall(r"<li\b[^>]*>(.*?)</li>", nav, re.S):
            href = first(r'<a\b[^>]*href="([^"]*)"', li)
            out.append((text_of(li), abs_url(self.rel, href) if href is not None else self.url))
        return out

    def faq(self) -> list[tuple[str, str]]:
        out = []
        for d in re.findall(r"<details\b[^>]*>(.*?)</details>", main_of(self.text), re.S):
            q = first(r"<summary\b[^>]*>(.*?)</summary>", d) or ""
            a = re.sub(r"<summary\b[^>]*>.*?</summary>", "", d, flags=re.S)
            out.append((text_of(q), text_of(a)))
        return out

    def terms(self) -> list[tuple[str, str, str]]:
        """Glossary (id, term, definition), in page order."""
        return [(i, text_of(t), text_of(d)) for i, t, d in re.findall(
            r'<dt\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>', main_of(self.text), re.S)]

    def cards(self) -> list[tuple[str, str]]:
        """Blog index cards: (headline, absolute post URL)."""
        out = []
        for c in re.findall(r'<article class="blog-card[^"]*"[^>]*>(.*?)</article>', main_of(self.text), re.S):
            h = text_of(first(r"<h3\b[^>]*>(.*?)</h3>", c) or "")
            href = first(r'<a\b[^>]*href="([^"]+)"[^>]*>\s*Read\b', c) or first(r'<a\b[^>]*href="([^"]+)"', c)
            out.append((h, abs_url(self.rel, href or "")))
        return out

    def child_pages(self, pages: list[str], want) -> list[str]:
        """Pages (rels) linked from <main>, in order of first link, that `want` accepts."""
        out = []
        for href in re.findall(r'<a\b[^>]*href="([^"]+)"', main_of(self.text)):
            target = resolve_link(self.rel, html.unescape(href))
            if target in pages and target != self.rel and want(target) and target not in out:
                out.append(target)
        return out


def page_name(f: Facts) -> str:
    """A page's name from its <title>, without the site suffix."""
    return re.split(r"\s+[—·|]\s+(?:BrewSLM\b.*)$", f.title)[0].strip() or f.h1


# ── JSON-LD (site:jsonld region) ───────────────────────────────────────────

LD_SCRIPT_RE = re.compile(r'<script\b[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
LD_REGION_RE = re.compile(r"<!--\s*site:jsonld\b.*?<!--\s*/site:jsonld\s*-->", re.S)
# Hand-written blocks of these types stay outside the region, untouched.
KEEP_LD = {"HowTo", "SoftwareApplication"}
ORG_ID = BASE + "#organization"
SITE_ID = BASE + "#website"
ACADEMY_URL = BASE + "academy/"


def ld_types(o) -> set[str]:
    t = o.get("@type") if isinstance(o, dict) else None
    return set(t) if isinstance(t, list) else ({t} if t else set())


def org_ref() -> dict:
    o = CFG["org"]
    return {"@type": "Organization", "@id": ORG_ID, "name": o["name"], "url": BASE}


def course_ref(url: str, name: str) -> dict:
    return {"@type": "Course", "@id": url + "#course", "name": name, "url": url}


def academy_ref() -> dict:
    return course_ref(ACADEMY_URL, CFG["academy"]["name"])


def breadcrumb_ld(f: Facts):
    if not f.crumbs:
        return None
    items = list(f.crumbs)
    if items[0][1] != BASE:
        items.insert(0, ("Home", BASE))
    return {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": n, "item": u} for i, (n, u) in enumerate(items, 1)]}


def quiz_ld(f: Facts) -> dict:
    qs = []
    for q in f.quiz:
        opts = q["options"]
        ans = {"@type": "Answer", "position": q["answer"], "text": opts[q["answer"]]}
        if q.get("explanation"):
            ans["answerExplanation"] = {"@type": "Comment", "text": q["explanation"]}
        qs.append({
            "@type": "Question", "eduQuestionType": "Multiple choice",
            "learningResourceType": "Practice problem", "name": q["q"], "text": q["q"],
            "suggestedAnswer": [{"@type": "Answer", "position": i, "text": o}
                                for i, o in enumerate(opts) if i != q["answer"]],
            "acceptedAnswer": ans})
    return {"@type": "Quiz", "name": f"{f.quiz_title or 'Quiz'}: {f.h1}",
            "about": {"@type": "Thing", "name": f.h1}, "url": f.url,
            "educationalLevel": f.level.capitalize() if f.level else None, "hasPart": qs}


def jsonld_blocks(rel: str, facts: dict[str, "Facts"], pages: list[str]) -> list[dict]:
    """The generated JSON-LD objects for one page."""
    f, pt = facts[rel], page_type(rel)
    blocks: list[dict] = []
    org = org_ref()
    if pt == "home":
        o = CFG["org"]
        blocks.append({"@type": "Organization", "@id": ORG_ID, "name": o["name"], "url": BASE,
                       "logo": {"@type": "ImageObject", "url": BASE + o["logo"]},
                       "sameAs": o["same_as"]})
        blocks.append({"@type": "WebSite", "@id": SITE_ID, "name": CFG["site_name"], "url": BASE,
                       "description": f.description, "inLanguage": "en", "publisher": {"@id": ORG_ID}})
    elif pt == "post":
        ov = CFG.get("pages", {}).get(rel, {})
        img = head_values(rel, scan(f.text))["og_image"]
        blocks.append({
            "@type": "BlogPosting", "@id": f.url + "#article", "headline": f.h1,
            "description": f.description, "url": f.url, "mainEntityOfPage": f.url,
            "datePublished": f.published, "dateModified": ov.get("date_modified", f.published),
            "image": img, "inLanguage": "en", "author": org,
            "publisher": {**org, "logo": {"@type": "ImageObject", "url": BASE + CFG["org"]["logo"]}},
            "isPartOf": {"@type": "Blog", "@id": url_of(CFG["feed"]["index"]) + "#blog"}})
    elif pt == "blog_index":
        posts = []
        for headline, u in f.cards():
            prel = resolve_link(rel, u)
            posts.append({"@type": "BlogPosting", "headline": headline, "url": u,
                          "datePublished": facts[prel].published if prel in facts else None})
        blocks.append({"@type": "Blog", "@id": f.url + "#blog", "name": CFG["feed"]["title"],
                       "description": f.description, "url": f.url, "inLanguage": "en",
                       "publisher": org, "blogPost": posts})
    elif pt == "academy_index":
        tracks = [f"academy/{t}/index.html" for t in CFG["academy"]["course_tracks"]]
        blocks.append({
            **academy_ref(), "description": f.description, "inLanguage": "en",
            "isAccessibleForFree": True, "provider": org,
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD", "category": "Free"},
            "hasPart": [{**course_ref(url_of(t), facts[t].h1), "isAccessibleForFree": True} for t in tracks]})
    elif pt == "track_index":
        d = rel.rsplit("/", 1)[0] + "/"
        kids = f.child_pages(pages, lambda t: t.startswith(d) and t.count("/") == d.count("/")
                             and page_type(t) in ("lesson", "tutorial"))
        parts = [{"@type": "LearningResource", "position": i, "name": facts[k].h1, "url": url_of(k)}
                 for i, k in enumerate(kids, 1)]
        if rel.split("/")[1] in CFG["academy"]["course_tracks"]:
            blocks.append({**course_ref(f.url, f.h1), "description": f.description, "inLanguage": "en",
                           "isAccessibleForFree": True, "provider": org, "isPartOf": academy_ref(),
                           "hasPart": parts})
        else:
            blocks.append({"@type": "CollectionPage", "name": f.h1, "description": f.description,
                           "url": f.url, "inLanguage": "en", "isPartOf": academy_ref(), "hasPart": parts})
    elif pt in ("lesson", "tutorial"):
        track = rel.rsplit("/", 1)[0] + "/index.html"
        part_of = (course_ref(url_of(track), facts[track].h1)
                   if rel.split("/")[1] in CFG["academy"]["course_tracks"] and track in facts
                   else {"@type": "CollectionPage", "name": facts[track].h1, "url": url_of(track)}
                   if track in facts else academy_ref())
        mins = duration_minutes(f.time)
        lr = {"@type": ["LearningResource", "Article"], "@id": f.url + "#lesson",
              "name": f.h1, "headline": f.h1, "description": f.description, "url": f.url,
              "mainEntityOfPage": f.url, "inLanguage": "en",
              "learningResourceType": "Tutorial" if pt == "tutorial" else "Lesson",
              "educationalLevel": f.level.capitalize() if f.level else None,
              "timeRequired": iso_duration(mins) if mins else None,
              # The visible "After this lesson you can ..." line; tutorials open
              # with a long overview instead, so they carry no teaches.
              "teaches": f.outcome if pt == "lesson" else None, "isAccessibleForFree": True,
              "isPartOf": part_of, "author": org, "publisher": org}
        blocks.append(lr)
        if f.quiz:
            blocks.append(quiz_ld(f))
    elif pt == "glossary":
        set_id = f.url + "#terms"
        blocks.append({"@type": "DefinedTermSet", "@id": set_id, "name": page_name(f),
                       "description": f.description, "url": f.url, "inLanguage": "en",
                       "hasDefinedTerm": [{"@type": "DefinedTerm", "@id": f"{f.url}#{i}", "name": t,
                                           "description": d, "url": f"{f.url}#{i}",
                                           "inDefinedTermSet": {"@id": set_id}}
                                          for i, t, d in f.terms()]})
    elif rel == CFG["faq"]["path"]:
        blocks.append({"@type": "FAQPage", "name": f.h1, "description": f.description, "url": f.url,
                       "inLanguage": "en", "isPartOf": {"@id": SITE_ID},
                       "mainEntity": [{"@type": "Question", "name": q,
                                       "acceptedAnswer": {"@type": "Answer", "text": a}}
                                      for q, a in f.faq()]})
    elif pt in ("top_level", "certificate", "roadmap", "academy_page"):
        page = {"@type": "WebPage", "name": f.h1, "description": f.description, "url": f.url,
                "inLanguage": "en", "isPartOf": academy_ref() if rel.startswith("academy/") else {"@id": SITE_ID}}
        if pt == "certificate":
            page["about"] = academy_ref()
        blocks.append(page)
    bc = breadcrumb_ld(f)
    if bc:
        blocks.append(bc)
    return [{"@context": "https://schema.org", **prune(b)} for b in blocks]


def prune(o):
    """Drop None values (a fact the page doesn't show is left out, not invented)."""
    if isinstance(o, dict):
        return {k: prune(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [prune(x) for x in o]
    return o


def ld_json(o: dict, indent: str) -> str:
    s = json.dumps(o, ensure_ascii=False, indent=2).replace("<", "\\u003c")
    return "\n".join(indent + ln for ln in s.splitlines())


def build_jsonld(rel: str, text: str, facts: dict, pages: list[str]) -> str:
    """Replace the site:jsonld region (and, on the first run, the hand-written
    blocks it supersedes) with freshly generated blocks."""
    m = re.search(r"<head\b[^>]*>(.*?)</head>", text, re.S | re.I)
    if not m:
        return text
    h0, h1 = m.span(1)
    head = text[h0:h1]
    spans = [r.span() for r in LD_REGION_RE.finditer(head)]
    for s in LD_SCRIPT_RE.finditer(head):
        if any(a <= s.start() < b for a, b in spans):
            continue
        try:
            types = ld_types(json.loads(s.group(1)))
        except ValueError:
            types = set()
        if not types & KEEP_LD:
            spans.append(s.span())
    spans = sorted(line_span(head, a, b) for a, b in spans)
    if spans:
        ins = spans[0][0]
        indent = re.match(r"\n?([ \t]*)", head[ins:]).group(1)
    else:
        ins = len(head.rstrip())
        indent = (re.findall(r"\n([ \t]*)\S", head) or ["    "])[-1]
    blocks = jsonld_blocks(rel, facts, pages)
    lines = [f"{indent}<!-- site:jsonld: generated by _build/site.py from the visible page and "
             f"_build/site.toml; edit those, not these blocks -->"]
    for b in blocks:
        lines += [f'{indent}<script type="application/ld+json">', ld_json(b, indent), f"{indent}</script>"]
    lines.append(f"{indent}<!-- /site:jsonld -->")
    new_head = head
    for a, b in reversed(spans):
        new_head = new_head[:a] + new_head[b:]
    new_head = new_head[:ins] + "".join("\n" + ln for ln in lines) + new_head[ins:]
    return text[:h0] + new_head + text[h1:]


# ── generated files ────────────────────────────────────────────────────────

def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def git_show(rev: str, rel: str):
    r = git("show", f"{rev}:{rel}")
    return r.stdout if r.returncode == 0 else None


def content_key(text: str) -> str:
    """Page text minus everything this script owns (and all JSON-LD, which is
    metadata, not content), whitespace-collapsed."""
    text = LD_SCRIPT_RE.sub("", LD_REGION_RE.sub("", text))
    m = re.search(r"<head\b[^>]*>(.*?)</head>", text, re.S | re.I)
    if m:
        head = m.group(1)
        head = re.sub(r"<!--\s*site:head\b.*?<!--\s*/site:head\s*-->", "", head, flags=re.S)
        head = TAG_RE.sub(lambda t: "" if managed_key(t.group(0)) else t.group(0), head)
        text = text[:m.start(1)] + head + text[m.end(1):]
    return " ".join(text.split())


def lastmod(rel: str, new_text: str) -> str:
    """Date of the last commit that changed the page's content (not just the
    regions and tags this script generates); today if the working copy does."""
    today = dt.date.today().isoformat()
    head = git_show("HEAD", rel)
    if head is None or content_key(head) != content_key(new_text):
        return today
    for ln in git("log", "--format=%H %cs", "--", rel).stdout.splitlines():
        sha, date = ln.split()
        cur, par = git_show(sha, rel), git_show(sha + "^", rel)
        if cur is None or par is None or content_key(cur) != content_key(par):
            return date
    return today


def build_sitemap(pages: list[str], texts: dict[str, str]) -> str:
    excl = set(CFG["sitemap"].get("exclude", [])) | {NOTFOUND}
    keep = [p for p in pages if p not in excl]
    order: list[str] = []
    cur = ROOT / CFG["sitemap"]["path"]
    if cur.exists():   # keep the existing (curated) order, append new pages
        try:
            ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            for loc in ET.parse(cur).getroot().findall("s:url/s:loc", ns):
                order.append((loc.text or "").strip())
        except ET.ParseError:
            pass
    pos = {u: i for i, u in enumerate(order)}
    keep.sort(key=lambda p: (pos.get(url_of(p), len(pos)), p))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for p in keep:
        out += ["  <url>", f"    <loc>{esc(url_of(p))}</loc>",
                f"    <lastmod>{lastmod(p, texts[p])}</lastmod>", "  </url>"]
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def rfc3339(d: str) -> str:
    d = str(d)
    return d if "T" in d else f"{d}T00:00:00Z"


def build_feed(texts: dict[str, str]) -> str:
    fc = CFG["feed"]
    posts = []
    for rel, text in texts.items():
        if page_type(rel) != "post":
            continue
        s = scan(text)
        pub = jsonld_field(s, "datePublished") or s.metas.get("article:published_time")
        if not pub:
            continue
        mod = jsonld_field(s, "dateModified") or s.metas.get("article:modified_time") or pub
        posts.append({
            "url": url_of(rel),
            "title": jsonld_field(s, "headline") or s.title or rel,
            "summary": s.metas.get("description", ""),
            "published": rfc3339(pub), "updated": rfc3339(mod),
            "image": s.metas.get("og:image", ""),
            "image_type": s.metas.get("og:image", "").lower(),
        })
    posts.sort(key=lambda p: (p["published"], p["url"]), reverse=True)
    updated = max((p["updated"] for p in posts), default="1970-01-01T00:00:00Z")
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">',
           f"  <title>{esc(fc['title'])}</title>",
           f"  <subtitle>{esc(fc['subtitle'])}</subtitle>",
           f'  <link rel="self" type="application/atom+xml" href="{esc(BASE + fc["path"])}" />',
           f'  <link rel="alternate" type="text/html" href="{esc(url_of(fc["index"]))}" />',
           f"  <id>{esc(BASE + fc['path'])}</id>",
           f"  <updated>{updated}</updated>",
           f"  <author><name>{esc(fc['author'])}</name><uri>{esc(fc['author_uri'])}</uri></author>"]
    for p in posts:
        out += ["  <entry>",
                f"    <title>{esc(p['title'])}</title>",
                f'    <link rel="alternate" type="text/html" href="{esc(p["url"])}" />',
                f"    <id>{esc(p['url'])}</id>",
                f"    <published>{p['published']}</published>",
                f"    <updated>{p['updated']}</updated>",
                f"    <summary>{esc(p['summary'])}</summary>"]
        if p["image"].lower().endswith(RASTER):
            out.append(f'    <media:thumbnail url="{esc(p["image"])}" />')
        out.append("  </entry>")
    out.append("</feed>")
    return "\n".join(out) + "\n"


def build_404() -> str:
    src = (ROOT / CFG["notfound"]["chrome_from"]).read_text(encoding="utf-8")
    header = re.search(r'[ \t]*<header class="site-header">.*?</header>', src, re.S).group(0)
    footer = re.search(r'[ \t]*<footer class="site-footer">.*?</footer>', src, re.S).group(0)

    src_url = url_of(CFG["notfound"]["chrome_from"])

    def to_root(m: re.Match) -> str:
        """Any relative href/src becomes root-relative, so it works at any depth."""
        val = html.unescape(m.group(2))
        if val.startswith(("#", "/")) or urlparse(val).scheme:
            return m.group(0)
        u = urlparse(urljoin(src_url, val))
        path = u.path + (f"?{u.query}" if u.query else "") + (f"#{u.fragment}" if u.fragment else "")
        return f'{m.group(1)}="{esc(path)}"'

    def absolutize(chunk: str) -> str:
        chunk = chunk.replace(' aria-current="page"', "")
        return re.sub(r'\b(href|src)="([^"]*)"', to_root, chunk)
    header, footer = absolutize(header), absolutize(footer)
    s = scan(src)
    gtag = re.search(r'[ \t]*<script async src="https://www\.googletagmanager\.com/[^"]*"></script>', src)
    fonts = re.findall(r'[ \t]*<link[^>]*(?:fonts\.googleapis|fonts\.gstatic)[^>]*>', src)
    head_extra = "\n".join(([gtag.group(0)] if gtag else []) + fonts)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <!-- generated by _build/site.py build; edit the template there -->
    <title>Page not found · {esc(CFG["site_name"])}</title>
    <meta name="description" content="This page does not exist. Try the BrewSLM home page, the Academy or the blog." />
    <meta name="robots" content="noindex,follow" />
    <meta name="theme-color" content="{esc(s.metas.get("theme-color", "#ffffff"))}" />
    <link rel="icon" type="image/svg+xml" href="/assets/favicon.svg" />
    <link rel="alternate" type="application/atom+xml" title="{esc(CFG["feed"]["title"])}" href="{esc(BASE + CFG["feed"]["path"])}" />
{head_extra}
    <script defer src="/assets/analytics.js"></script>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
{header}

    <main>
      <section class="section page-hero">
        <p class="eyebrow">404 · Page not found</p>
        <h1>This page isn't here.</h1>
        <p class="page-sub">
          The link may be old, or the page may have moved. Everything on the site starts from one
          of these pages.
        </p>
        <div class="hero-actions">
          <a class="btn btn-solid" href="/">Home</a>
          <a class="btn btn-outline" href="/academy/">Academy</a>
          <a class="btn btn-outline" href="/blog.html">Blog</a>
          <a class="btn btn-ghost" href="/faqs.html">FAQs</a>
        </div>
      </section>
    </main>

{footer}

    <script src="/script.js"></script>
  </body>
</html>
"""


# ── build / check ──────────────────────────────────────────────────────────

def render() -> tuple[dict[str, str], dict[str, str]]:
    """Return ({rel: new text} for pages, {rel: new text} for generated files)."""
    pages = discover()
    old = {p: (ROOT / p).read_text(encoding="utf-8") for p in pages}
    facts = {p: Facts(p, t) for p, t in old.items()}
    pset = set(pages)
    new = {p: build_head(p, build_jsonld(p, t, facts, pset)) for p, t in old.items()}
    gen = {
        CFG["sitemap"]["path"]: build_sitemap(pages, new),
        CFG["feed"]["path"]: build_feed(new),
        NOTFOUND: build_404(),
    }
    return new, gen


def write_all(files: dict[str, str]) -> list[str]:
    written = []
    for rel, text in files.items():
        p = ROOT / rel
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            written.append(rel)
    return written


def cmd_build(_args) -> int:
    pages, gen = render()
    written = write_all(pages) + write_all(gen)
    print(f"build: {len(written)} file(s) written" + (": " + ", ".join(written[:12]) if written else ""))
    if len(written) > 12:
        print(f"       … and {len(written) - 12} more")
    return 0


def strip_lastmod(x: str) -> str:
    return re.sub(r"<lastmod>[^<]*</lastmod>", "", x)


def resolve_link(rel: str, href: str):
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    u = urlparse(urljoin(url_of(rel), href))
    if u.scheme not in ("http", "https") or u.netloc != urlparse(BASE).netloc:
        return None
    path = u.path.lstrip("/")
    if path == "" or path.endswith("/"):
        path += "index.html"
    return path


def cmd_check(args) -> int:
    errors: list[str] = []
    warns: list[str] = []
    pages, gen = render()
    for rel, text in list(pages.items()) + list(gen.items()):
        p = ROOT / rel
        cur = p.read_text(encoding="utf-8") if p.exists() else None
        if cur == text:
            continue
        if rel == CFG["sitemap"]["path"] and cur is not None and strip_lastmod(cur) == strip_lastmod(text):
            warns.append(f"{rel}: lastmod dates differ from git (run build after committing to refresh)")
        else:
            errors.append(f"{rel}: out of date, run `python3 _build/site.py build`")
    for rel, text in gen.items():
        if rel.endswith(".xml"):
            try:
                ET.fromstring(text.encode("utf-8"))
            except ET.ParseError as e:
                errors.append(f"{rel}: not well-formed XML ({e})")

    long_t, long_d = [], []
    for rel in list(pages) + [NOTFOUND]:
        text = pages.get(rel) or gen[NOTFOUND]
        s = scan(text)
        for href, kind, line in s.links:
            target = resolve_link(rel, href)
            if target and not (ROOT / target).is_file() and target != NOTFOUND:
                errors.append(f"{rel}:{line}: broken link {href}")
        if rel == NOTFOUND:
            for val in re.findall(r'\b(?:href|src)="([^"]*)"', text):
                if not (val.startswith(("#", "/")) or urlparse(html.unescape(val)).scheme):
                    errors.append(f"{rel}: relative URL {val!r} breaks below the site root")
            continue
        if not re.search(r"<!--\s*site:head\b", text) or "<!-- /site:head -->" not in text:
            errors.append(f"{rel}: no <!-- site:head --> region (does the page have a <head>?)")
        if not s.title:
            errors.append(f"{rel}: missing <title>")
        elif len(s.title) > 60:
            long_t.append(f"{rel} ({len(s.title)})")
        d = s.metas.get("description")
        if not d:
            errors.append(f"{rel}: missing meta description")
        elif len(d) > 160:
            long_d.append(f"{rel} ({len(d)})")
        if s.headings.count(1) != 1:
            errors.append(f"{rel}: {s.headings.count(1)} <h1> elements (want 1)")
        for a, b in zip(s.headings, s.headings[1:]):
            if b > a + 1:
                errors.append(f"{rel}: heading level skips h{a} -> h{b}")
                break
        for line, safe in s.tables:
            if not safe:
                errors.append(f"{rel}:{line}: <table> can overflow on phones (use .table-wrap, "
                              ".comparison-table or .mini-table)")
        for block in s.jsonld:
            try:
                json.loads(block)
            except ValueError as e:
                errors.append(f"{rel}: invalid JSON-LD ({e})")
    # Budgets warn only for now; T10 trims titles/descriptions and tightens these.
    if long_t:
        warns.append(f"{len(long_t)} title(s) over 60 chars" + (": " + ", ".join(long_t) if args.verbose else ""))
    if long_d:
        warns.append(f"{len(long_d)} description(s) over 160 chars" + (": " + ", ".join(long_d) if args.verbose else ""))

    for w in warns:
        print("warning:", w)
    for e in errors:
        print("error:", e)
    print(f"check: {len(pages)} pages, {len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="rewrite marked regions and generated files")
    c = sub.add_parser("check", help="fail if build would change anything or a page has problems")
    c.add_argument("-v", "--verbose", action="store_true", help="list every over-budget page")
    args = ap.parse_args()
    return {"build": cmd_build, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
