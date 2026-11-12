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
                                 # link is broken, a page lacks a title, a
                                 # JSON-LD node lacks a required property or
                                 # disagrees with the visible page, ...
  python3 _build/site.py check -v # ... and list over-budget pages and the
                                 # JSON-LD node count per type

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
        meta = first(r'<div class="lesson-meta"[^>]*>(.*?)</div>', body) or ""
        self.level = text_of(first(r"<strong>Level:</strong>(.*?)</span>", meta) or "") or None
        self.time = text_of(first(r"<strong>(?:Read time|Time):</strong>(.*?)</span>", meta) or "") or None
        self.outcome = text_of(first(r'class="lesson-header__outcome"[^>]*>(.*?)</p>', body) or "") or None
        self.published = first(r'class="blog-post-meta"[^>]*>\s*<time datetime="([^"]+)"', body)
        q = first(r"<section\b[^>]*\bdata-quiz\b[^>]*>.*?<script type=\"application/json\">(.*?)</script>", body)
        try:
            self.quiz = json.loads(q) if q else None
        except ValueError:
            self.quiz = None   # check reports it (Visible parses the same data)
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


class Visible(HTMLParser):
    """A second, independent reading of what a page shows, for `check`.

    The generator reads pages with the regexes in Facts; this walks the DOM
    with html.parser instead, so an extraction bug in one is not repeated in
    the other. It collects the h1, headings by id, the breadcrumb, lesson
    meta, quiz data, FAQ entries, glossary terms, blog cards, the post date,
    links inside <main> and the page's visible words.
    """

    BLOCK = {"br", "p", "li", "ul", "ol", "div", "dd", "dt", "tr", "td", "th", "h1", "h2", "h3", "h4",
             "h5", "h6", "section", "article", "pre", "table", "summary", "details", "blockquote", "nav",
             "header", "footer", "main"}

    def __init__(self, rel: str, text: str):
        super().__init__(convert_charrefs=True)
        self.rel = rel
        self.h1 = None
        self.headings: dict[str, str] = {}
        self.crumbs: list[tuple[str, str | None]] = []
        self.level = self.time = self.published = None
        self.quiz_raw: str | None = None
        self.faq: list[tuple[str, str]] = []
        self.terms: list[tuple[str, str, str]] = []
        self.cards: list[tuple[str, str | None]] = []
        self.main_links: list[str] = []
        self.words: list[str] = []
        self._stack: list[tuple[str, dict]] = []
        self._caps: list[dict] = []      # open captures: {kind, depth, buf, ...}
        self._in_body = self._in_main = False
        self._skip = 0                   # inside <script>/<style> (not visible)
        self._quiz_depth = None
        self._card = None
        # Pages without <main> (lessons, glossary): the whole body counts.
        self._has_main = re.search(r"<main\b", text) is not None
        self.feed(text)
        self.close()

    def _open(self, kind, **kw):
        self._caps.append({"kind": kind, "depth": len(self._stack), "buf": [], **kw})

    @staticmethod
    def _t(buf):
        return " ".join("".join(buf).split())

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        cls = set(a.get("class", "").split())
        if tag == "body":
            self._in_body = True
            self._in_main = not self._has_main
        if tag in self.BLOCK:
            for c in self._caps:
                c["buf"].append(" ")
        if tag in VOID:
            return
        self._stack.append((tag, a))
        depth = len(self._stack)
        if tag == "main":
            self._in_main = True
        if tag in ("script", "style"):
            if tag == "script" and self._quiz_depth is not None and a.get("type") == "application/json":
                self._open("quiz")
            else:
                self._skip += 1
            return
        if tag == "section" and "data-quiz" in a:
            self._quiz_depth = depth
        if re.fullmatch(r"h[1-6]", tag):
            self._open("heading", tag=tag, id=a.get("id"))
        if tag == "nav" and "breadcrumb" in cls:
            self._open("crumbs")
        if tag == "li" and any(c["kind"] == "crumbs" for c in self._caps):
            self._open("crumb", href=None)
        if tag == "a":
            for c in self._caps:
                if c["kind"] == "crumb" and c["href"] is None:
                    c["href"] = a.get("href")
            if self._in_main and a.get("href"):
                self.main_links.append(a["href"])
            if self._card is not None:
                self._open("card_a", href=a.get("href"))
        if tag == "span" and any(c["kind"] == "meta" for c in self._caps):
            self._open("meta_span")
        if tag == "div" and "lesson-meta" in cls:
            self._open("meta")
        if tag == "time" and any(c["kind"] == "postmeta" for c in self._caps) and self.published is None:
            self.published = a.get("datetime")
        if tag == "p" and "blog-post-meta" in cls:
            self._open("postmeta")
        if self._in_main:
            if tag == "details":
                self._open("details")
            if tag == "summary":
                self._open("summary")
            if tag == "dt" and a.get("id"):
                self._open("dt", id=a["id"])
            if tag == "dd" and self.terms and self.terms[-1][2] is None:
                self._open("dd")
            if tag == "article" and "blog-card" in cls:
                self._card = {"h3": None, "href": None, "depth": depth}
            if tag == "h3" and self._card is not None:
                self._open("card_h3")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self._stack and self._stack[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in (t for t, _ in self._stack):
            return
        while self._stack:
            depth = len(self._stack)
            t, _ = self._stack[-1]
            done = [c for c in self._caps if c["depth"] == depth]   # opened by this element
            for c in done:
                self._caps.remove(c)
                self._close(c)
            self._stack.pop()
            if t in ("script", "style") and not any(c["kind"] == "quiz" for c in done):
                self._skip = max(0, self._skip - 1)
            if t == "main":
                self._in_main = False
            if t == "section" and self._quiz_depth == depth:
                self._quiz_depth = None
            if t == "article" and self._card is not None and self._card["depth"] == depth:
                self.cards.append((self._card["h3"], self._card["href"]))
                self._card = None
            if t == tag:
                break
        if tag in self.BLOCK:
            for c in self._caps:
                c["buf"].append(" ")

    def _close(self, c):
        k, text = c["kind"], self._t(c["buf"])
        raw = "".join(c["buf"])
        if k == "heading":
            if c["tag"] == "h1" and self.h1 is None:
                self.h1 = text
            if c["id"]:
                self.headings[c["id"]] = text
        elif k == "crumb":
            self.crumbs.append((text, c["href"]))
        elif k == "meta_span":
            m = re.match(r"(Level|Read time|Time):\s*(.*)", text)
            if m and m.group(1) == "Level":
                self.level = m.group(2)
            elif m:
                self.time = m.group(2)
        elif k == "quiz":
            self.quiz_raw = raw
        elif k == "summary":
            self._summary = text
        elif k == "details":
            q = getattr(self, "_summary", "")
            self.faq.append((q, text[len(q):].strip() if text.startswith(q) else text))
        elif k == "dt":
            self.terms.append((c["id"], text, None))
        elif k == "dd":
            i, t, _ = self.terms[-1]
            self.terms[-1] = (i, t, text)
        elif k == "card_h3" and self._card is not None:
            self._card["h3"] = text
        elif k == "card_a" and self._card is not None and text.startswith("Read"):
            self._card["href"] = c["href"]

    def handle_data(self, data):
        if self._skip:
            return
        for c in self._caps:
            c["buf"].append(data)
        if self._in_body and not any(c["kind"] == "quiz" for c in self._caps):
            self.words.append(data)

    def quiz(self):
        return json.loads(self.quiz_raw) if self.quiz_raw is not None else None

    def word_set(self) -> set[str]:
        return set(re.findall(r"[a-z0-9][a-z0-9+./-]*[a-z0-9]|[a-z0-9]", " ".join(self.words).lower()))


# ── JSON-LD check (shape + agreement with the visible page) ────────────────

# Properties each top-level node must carry (Google's required/recommended set
# for the rich result where there is one; schema.org essentials otherwise).
LD_REQUIRED = {
    "Organization": ["name", "url", "logo"],
    "WebSite": ["name", "url"],
    "SoftwareApplication": ["name", "applicationCategory", "offers"],
    "BlogPosting": ["headline", "description", "datePublished", "dateModified", "author", "publisher",
                    "image", "mainEntityOfPage"],
    "Blog": ["name", "url", "blogPost"],
    "Course": ["name", "description", "provider", "url"],
    "LearningResource": ["name", "url", "learningResourceType", "isPartOf"],
    "Article": ["headline", "author"],
    "Quiz": ["about", "hasPart"],
    "FAQPage": ["mainEntity"],
    "BreadcrumbList": ["itemListElement"],
    "HowTo": ["name", "step"],
    "DefinedTermSet": ["name", "hasDefinedTerm"],
    "WebPage": ["name", "url"],
    "CollectionPage": ["name", "url"],
}
# Types each page type must carry.
LD_EXPECTED = {
    "home": {"Organization", "WebSite", "SoftwareApplication"},
    "post": {"BlogPosting", "BreadcrumbList"},
    "blog_index": {"Blog", "BreadcrumbList"},
    "academy_index": {"Course", "BreadcrumbList"},
    "track_index": {"BreadcrumbList"},
    "lesson": {"LearningResource", "Article", "BreadcrumbList"},
    "tutorial": {"LearningResource", "Article", "HowTo", "BreadcrumbList"},
    "glossary": {"DefinedTermSet", "BreadcrumbList"},
    "certificate": {"WebPage", "BreadcrumbList"},
    "roadmap": {"WebPage", "BreadcrumbList"},
    "academy_page": {"BreadcrumbList"},
    "top_level": {"BreadcrumbList"},
}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?")


def ld_nodes(text: str) -> tuple[list[dict], list[str]]:
    """Top-level JSON-LD nodes of a page (blocks, lists and @graph flattened)."""
    nodes, errs = [], []
    for raw in LD_SCRIPT_RE.findall(text):
        try:
            o = json.loads(raw)
        except ValueError as e:
            errs.append(f"does not parse ({e})")
            continue
        for x in (o if isinstance(o, list) else [o]):
            if not isinstance(x, dict):
                errs.append("block is not an object")
                continue
            ctx = x.get("@context")
            if ctx not in ("https://schema.org", "https://schema.org/", "http://schema.org"):
                errs.append(f"{x.get('@type')}: @context is {ctx!r}, want https://schema.org")
            nodes += [{**g, "@context": ctx} for g in x["@graph"]] if "@graph" in x else [x]
    return nodes, errs


def ids_on(text: str) -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', text))


def words_of(s: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9+./-]*[a-z0-9]|[a-z0-9]", s.lower())


def on_page(phrase: str, page_words: set[str], page_text: str) -> bool:
    """A hand-written phrase (HowTo supply/tool/step text) is backed by the
    page: at least 60% of its words of 4+ letters appear there, or, for a
    phrase without such words, the phrase itself does."""
    w = [x for x in words_of(phrase) if len(x) >= 4]
    if not w:
        return phrase.lower() in page_text.lower()
    return sum(x in page_words for x in w) / len(w) >= 0.6


def check_jsonld(rel: str, text: str, vis: dict, pages: set[str], texts: dict) -> tuple[list[str], list[str]]:
    """Errors for one page's JSON-LD, and the types it carries.

    Agreement is checked against Visible (html.parser), not against the
    Facts regexes the generator used, so a generator bug cannot pass itself."""
    v, pt = vis[rel], page_type(rel)
    url = url_of(rel)
    description = scan(text).metas.get("description", "")
    errs: list[str] = []
    try:
        vquiz = v.quiz()
    except ValueError as e:
        vquiz = None
        errs.append(f"the visible quiz data does not parse ({e})")

    def kids_of(r: str, want) -> list[str]:
        out = []
        for href in vis[r].main_links:
            t = resolve_link(r, html.unescape(href))
            if t in pages and t != r and want(t) and t not in out:
                out.append(t)
        return out
    nodes, perr = ld_nodes(text)
    errs += perr
    page_ids = ids_on(text)

    def err(msg):
        errs.append(msg)

    def need(node, keys, what):
        for k in keys:
            v = node.get(k)
            if v is None or v == "" or v == []:
                err(f"{what}: missing {k}")

    def internal(u, what, anchors=False):
        """An absolute brewslm.com URL must point at a real page (and anchor)."""
        if not isinstance(u, str):
            return
        if not u.startswith(BASE):
            if not urlparse(u).scheme:
                err(f"{what}: {u!r} is not an absolute URL")
            return
        target = resolve_link(rel, u)
        if target and not (ROOT / target).is_file():
            err(f"{what}: {u} has no page")
        frag = urlparse(u).fragment
        if anchors and frag and target:
            ids = page_ids if target == rel else ids_on(texts.get(target, ""))
            if frag not in ids:
                err(f"{what}: {u} has no #{frag} on the page")

    def walk_urls(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("url", "item", "mainEntityOfPage", "image") and isinstance(v, str):
                    internal(v, f"{path}.{k}")
                elif k in ("datePublished", "dateModified") and not (isinstance(v, str) and DATE_RE.fullmatch(v)):
                    err(f"{path}.{k}: {v!r} is not an ISO 8601 date")
                elif k in ("timeRequired", "totalTime") and duration_minutes(v) is None:
                    err(f"{path}.{k}: {v!r} is not an ISO 8601 duration")
                else:
                    walk_urls(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk_urls(v, f"{path}[{i}]")

    def dup_at_ids(o, path):
        """@ids must be unique among top-level nodes and among the members of any list."""
        if isinstance(o, dict):
            for k, v in o.items():
                dup_at_ids(v, f"{path}.{k}")
        elif isinstance(o, list):
            seen = [x["@id"] for x in o if isinstance(x, dict) and "@id" in x]
            for d in sorted({i for i in seen if seen.count(i) > 1}):
                err(f"{path}: @id {d} used by {seen.count(d)} nodes")
            for i, v in enumerate(o):
                dup_at_ids(v, f"{path}[{i}]")
    dup_at_ids(nodes, "nodes")

    found: list[str] = []
    for n in nodes:
        types = ld_types(n)
        if not types:
            err("node without @type")
            continue
        found += sorted(types)
        name = "+".join(sorted(types))
        for t in types:
            if t not in LD_REQUIRED:
                err(f"{name}: no shape rule for @type {t} (add one to LD_REQUIRED)")
            need(n, LD_REQUIRED.get(t, []), name)
        walk_urls(n, name)
        for k in ("author", "publisher", "provider"):
            if isinstance(n.get(k), dict) and set(n[k]) != {"@id"} and not n[k].get("name"):
                err(f"{name}.{k}: missing name")

        # ── agreement with the visible page ──
        main_url = n.get("url")
        if types & {"BlogPosting", "Blog", "LearningResource", "HowTo", "DefinedTermSet", "WebPage",
                    "CollectionPage", "FAQPage", "WebSite", "SoftwareApplication"} or \
                (types == {"Course"} and pt in ("academy_index", "track_index")):
            if main_url != url:
                err(f"{name}.url {main_url!r} is not this page ({url})")
            if "description" in n and n["description"] != description and not types & {"SoftwareApplication"}:
                err(f"{name}.description differs from the meta description")
        heading = n.get("headline") if types & {"BlogPosting", "Article"} else n.get("name")
        if (types & {"BlogPosting", "LearningResource", "HowTo", "WebPage", "CollectionPage", "FAQPage"}
                or (types == {"Course"} and pt == "track_index")) and heading != v.h1:
            err(f"{name} name/headline {heading!r} is not the h1 {v.h1!r}")
        if "Article" in types and n.get("headline") != v.h1:
            err(f"{name}.headline {n.get('headline')!r} is not the h1")
        if "BlogPosting" in types and pt == "post":
            if n.get("datePublished") != v.published:
                err(f"BlogPosting.datePublished {n.get('datePublished')!r} is not the visible date {v.published!r}")
            if str(n.get("dateModified", "")) < str(n.get("datePublished", "")):
                err("BlogPosting.dateModified is before datePublished")
        if "LearningResource" in types:
            if v.level and str(n.get("educationalLevel", "")).lower() != v.level.lower():
                err(f"educationalLevel {n.get('educationalLevel')!r} is not the visible level {v.level!r}")
            if duration_minutes(n.get("timeRequired")) != duration_minutes(v.time):
                err(f"timeRequired {n.get('timeRequired')!r} is not the visible time {v.time!r}")
        if "HowTo" in types:
            if duration_minutes(n.get("totalTime")) != duration_minutes(v.time):
                err(f"HowTo.totalTime {n.get('totalTime')!r} is not the visible time {v.time!r}")
            page_text = " ".join(" ".join(v.words).split())
            page_words = set(words_of(page_text))
            for i, s in enumerate(n.get("step", [])):
                if not (s.get("name") or s.get("text")):
                    err(f"HowTo.step[{i}]: missing name/text")
                internal(s.get("url"), f"HowTo.step[{i}].url", anchors=True)
                frag = urlparse(s.get("url", "")).fragment
                if s.get("name") and s["name"] != v.headings.get(frag):
                    err(f"HowTo.step[{i}].name {s['name']!r} is not the heading at #{frag} "
                        f"({v.headings.get(frag)!r})")
                for k in ("text", "description"):
                    if s.get(k) and not on_page(s[k], page_words, page_text):
                        err(f"HowTo.step[{i}].{k} is not backed by the page: {s[k][:60]!r}")
            for k in ("supply", "tool"):
                for x in n.get(k, []):
                    nm = x.get("name", "") if isinstance(x, dict) else str(x)
                    if not on_page(nm, page_words, page_text):
                        err(f"HowTo.{k} {nm!r} is not backed by the page")
        if "Quiz" in types:
            want = [(q["q"], q["options"][q["answer"]],
                     sorted(o for i, o in enumerate(q["options"]) if i != q["answer"])) for q in vquiz or []]
            got = []
            for q in n.get("hasPart", []):
                if not (q.get("text") and q.get("eduQuestionType")):
                    err(f"Quiz question {q.get('name')!r}: missing text/eduQuestionType")
                got.append((q.get("text"), (q.get("acceptedAnswer") or {}).get("text"),
                            sorted(a.get("text") for a in q.get("suggestedAnswer", []))))
            if got != want:
                err(f"Quiz ({len(got)} questions) differs from the visible quiz ({len(want)})")
        if "FAQPage" in types:
            got = [(q.get("name"), (q.get("acceptedAnswer") or {}).get("text")) for q in n.get("mainEntity", [])]
            if not got or any(not a or not b for a, b in got):
                err("FAQPage: a question without name or answer text")
            if got != v.faq:
                err(f"FAQPage ({len(got)} Q&As) differs from the visible FAQ ({len(v.faq)})")
        if "DefinedTermSet" in types:
            got = []
            for t in n.get("hasDefinedTerm", []):
                if not (t.get("name") and t.get("description")):
                    err(f"DefinedTerm {t.get('name')!r}: missing name/description")
                internal(t.get("url"), f"DefinedTerm {t.get('name')!r}.url", anchors=True)
                got.append((urlparse(t.get("url", "")).fragment, t.get("name"), t.get("description")))
            if got != v.terms:
                err(f"DefinedTermSet ({len(got)} terms) differs from the visible glossary ({len(v.terms)})")
        if "Blog" in types:
            got = [(p.get("headline"), p.get("url")) for p in n.get("blogPost", [])]
            if got != [(h, abs_url(rel, u or "")) for h, u in v.cards]:
                err(f"Blog.blogPost ({len(got)}) differs from the visible post cards ({len(v.cards)})")
            posts = {url_of(p) for p in pages if page_type(p) == "post"}
            if {u for _, u in got} != posts:
                err("Blog.blogPost does not list every post")
        if types == {"Course"} and pt == "track_index":
            d = rel.rsplit("/", 1)[0] + "/"
            kids = kids_of(rel, lambda t: t.startswith(d) and page_type(t) in ("lesson", "tutorial"))
            if [(p.get("name"), p.get("url")) for p in n.get("hasPart", [])] != \
                    [(vis[k].h1, url_of(k)) for k in kids]:
                err("Course.hasPart differs from the lessons the track page lists")
        if types == {"Course"} and pt == "academy_index":
            linked = {url_of(t) for t in kids_of(rel, lambda t: page_type(t) == "track_index")}
            for p in n.get("hasPart", []):
                if p.get("url") not in linked:
                    err(f"Course.hasPart {p.get('url')} is not linked from the academy page")
        if "BreadcrumbList" in types:
            items = n.get("itemListElement", [])
            if [it.get("position") for it in items] != list(range(1, len(items) + 1)):
                err("BreadcrumbList positions are not 1..n")
            if len(items) < 2:
                err("BreadcrumbList has fewer than 2 items")
            want = [(n_, norm_url(urljoin(url, html.unescape(h))) if h is not None else url) for n_, h in v.crumbs]
            if want and want[0][1] != BASE:
                want.insert(0, ("Home", BASE))
            if not v.crumbs:
                err("BreadcrumbList but no visible .breadcrumb on the page")
            elif [(i.get("name"), i.get("item")) for i in items] != want:
                err("BreadcrumbList differs from the visible breadcrumb")

    have = set(found)
    want = set(LD_EXPECTED.get(pt, set()))
    if rel == CFG["faq"]["path"]:
        want.add("FAQPage")
    if pt == "track_index":
        want.add("Course" if rel.split("/")[1] in CFG["academy"]["course_tracks"] else "CollectionPage")
    if vquiz:
        want.add("Quiz")
    if v.crumbs:
        want.add("BreadcrumbList")
    for t in sorted(want - have):
        err(f"missing {t} for a {pt} page")
    for t in ("BreadcrumbList", "Organization", "WebSite", "FAQPage", "Quiz", "DefinedTermSet"):
        if found.count(t) > 1:
            err(f"{found.count(t)} {t} nodes (want 1)")
    return [f"{rel}: JSON-LD {e}" for e in errs], found


# ── build / check ──────────────────────────────────────────────────────────

def render() -> tuple[dict[str, str], dict[str, str], dict[str, "Facts"]]:
    """Return ({rel: new text} for pages, {rel: new text} for generated files,
    {rel: visible facts} for pages)."""
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
    return new, gen, facts


def write_all(files: dict[str, str]) -> list[str]:
    written = []
    for rel, text in files.items():
        p = ROOT / rel
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            written.append(rel)
    return written


def cmd_build(_args) -> int:
    pages, gen, _ = render()
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
    pages, gen, facts = render()
    ld_types_seen: dict[str, int] = {}
    vis = {rel: Visible(rel, text) for rel, text in pages.items()}
    for rel, text in pages.items():
        e, found = check_jsonld(rel, text, vis, set(pages), pages)
        errors += e
        for t in found:
            ld_types_seen[t] = ld_types_seen.get(t, 0) + 1
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
        all_ids = re.findall(r'\bid="([^"]+)"', text)
        dup_ids = sorted({i for i in all_ids if all_ids.count(i) > 1})
        if dup_ids:
            errors.append(f"{rel}: duplicate HTML id(s): {', '.join(dup_ids)}")
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
    if args.verbose:
        print("JSON-LD nodes:", ", ".join(f"{k} {v}" for k, v in sorted(ld_types_seen.items())))
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
