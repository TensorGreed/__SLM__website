#!/usr/bin/env python3
"""Tiny build step for brewslm.com. Standard library only.

The hand-written HTML stays the source of truth. This script only:

  * rewrites marker-delimited regions inside pages
      <!-- site:head ... -->  ...  <!-- /site:head -->
    (canonical, robots, Open Graph, Twitter, feed link, deferred analytics);
  * writes whole generated files: sitemap.xml, feed.xml, 404.html.

Usage:
  python3 tools/site.py build    # rewrite regions + generated files
  python3 tools/site.py check    # exit 1 if build would change anything, or a
                                 # link is broken, a page lacks a title, ...

`build` is idempotent: running it twice gives a zero diff. Values inside the
site:head region (og:title, og:description, twitter:*, og:image, robots) are
kept when present, so edit them in place; canonical, og:url and image sizes
are always derived. The first run moves the existing tags into the region.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
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
CFG = tomllib.loads((ROOT / "tools" / "site.toml").read_text(encoding="utf-8"))
BASE = CFG["base_url"].rstrip("/") + "/"
NOTFOUND = CFG["notfound"]["path"]
SKIP_DIRS = {".git", ".github", "node_modules", "tools"}
RASTER = (".png", ".jpg", ".jpeg", ".gif", ".webp")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


# ── discovery + classification ─────────────────────────────────────────────

def discover() -> list[str]:
    out = []
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
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
        self.main_imgs: list[tuple[str, str]] = []

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
        if tag == "img" and a.get("src") and any(t == "main" for t, _ in self.stack):
            self.main_imgs.append((a["src"], a.get("alt", "")))
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


def build_head(rel: str, text: str) -> str:
    m = re.search(r"<head\b[^>]*>(.*?)</head>", text, re.S | re.I)
    if not m:
        return text
    h0, h1 = m.span(1)
    head = text[h0:h1]
    found: dict[str, str] = {}
    spans = []
    for t in TAG_RE.finditer(head):
        k = managed_key(t.group(0))
        if not k:
            continue
        if k != "marker":
            _, a = tag_attrs(t.group(0))
            v = a.get("src") if k == "analytics" else a.get("href") if k in ("canonical", "feed") else a.get("content", "")
            found.setdefault(k, v)
        spans.append(line_span(head, *t.span()))
    if not spans:
        return text
    indent = re.match(r"\n?([ \t]*)", head[spans[0][0]:]).group(1)

    s = scan(text)
    ptype = page_type(rel)
    title = s.title or ""
    desc = s.metas.get("description", "")
    canonical = url_of(rel)

    def get(k, default):
        v = found.get(k)
        return v if v else default

    image = found.get("og:image")
    image_alt_default = None
    if not image:
        for src, alt in s.main_imgs:
            if src.lower().split("?")[0].endswith(RASTER):
                image = urljoin(canonical, src)
                image_alt_default = alt if 0 < len(alt) <= 160 else None
                break
    if not image:
        image, image_alt_default = BASE + CFG["default_image"], CFG["default_image_alt"]
    if image == BASE + CFG["default_image"]:
        image_alt_default = CFG["default_image_alt"]
    og_title = get("og:title", title)
    og_desc = get("og:description", desc)
    image_alt = get("og:image:alt", image_alt_default or og_title)
    size = None
    lf = local_file_for_url(image)
    if lf is not None:
        size = image_size(lf)

    published = jsonld_field(s, "datePublished") or found.get("article:published_time")
    modified = jsonld_field(s, "dateModified") or found.get("article:modified_time")
    og_type = get("og:type", "article" if ptype in ("post", "lesson", "tutorial") else "website")

    lines = ["<!-- site:head: generated by tools/site.py; values are kept, structure is rebuilt -->"]
    lines.append(f'<meta name="robots" content="{esc(get("robots", "index,follow,max-image-preview:large,max-snippet:-1"))}" />')
    lines.append(f'<link rel="canonical" href="{esc(canonical)}" />')
    lines.append(f'<link rel="alternate" type="application/atom+xml" title="{esc(CFG["feed"]["title"])}" '
                 f'href="{esc(BASE + CFG["feed"]["path"])}" />')
    lines.append(f'<meta property="og:type" content="{esc(og_type)}" />')
    lines.append(f'<meta property="og:site_name" content="{esc(CFG["site_name"])}" />')
    lines.append(f'<meta property="og:title" content="{esc(og_title)}" />')
    lines.append(f'<meta property="og:description" content="{esc(og_desc)}" />')
    lines.append(f'<meta property="og:url" content="{esc(canonical)}" />')
    lines.append(f'<meta property="og:image" content="{esc(image)}" />')
    if size:
        lines.append(f'<meta property="og:image:width" content="{size[0]}" />')
        lines.append(f'<meta property="og:image:height" content="{size[1]}" />')
    lines.append(f'<meta property="og:image:alt" content="{esc(image_alt)}" />')
    if og_type == "article" and published:
        lines.append(f'<meta property="article:published_time" content="{esc(published)}" />')
    if og_type == "article" and modified:
        lines.append(f'<meta property="article:modified_time" content="{esc(modified)}" />')
    lines.append('<meta name="twitter:card" content="summary_large_image" />')
    lines.append(f'<meta name="twitter:title" content="{esc(get("twitter:title", og_title))}" />')
    lines.append(f'<meta name="twitter:description" content="{esc(get("twitter:description", og_desc))}" />')
    lines.append(f'<meta name="twitter:image" content="{esc(get("twitter:image", image))}" />')
    lines.append(f'<meta name="twitter:image:alt" content="{esc(get("twitter:image:alt", image_alt))}" />')
    # Any other og:/article:/twitter: tag written by hand is kept as-is.
    known = {"robots", "canonical", "feed", "analytics", "og:type", "og:site_name", "og:title",
             "og:description", "og:url", "og:image", "og:image:width", "og:image:height",
             "og:image:alt", "article:published_time", "article:modified_time", "twitter:card",
             "twitter:title", "twitter:description", "twitter:image", "twitter:image:alt"}
    for k in sorted(set(found) - known):
        attr = "name" if k.startswith("twitter:") else "property"
        lines.insert(-5, f'<meta {attr}="{esc(k)}" content="{esc(found[k])}" />')
    analytics = found.get("analytics") or rel_prefix(rel) + "assets/analytics.js"
    lines.append(f'<script defer src="{esc(analytics)}"></script>')
    lines.append("<!-- /site:head -->")

    new_head = head
    for a, b in reversed(spans):
        new_head = new_head[:a] + new_head[b:]
    ins = spans[0][0]
    block = "".join(f"\n{indent}{ln}" for ln in lines)
    new_head = new_head[:ins] + block + new_head[ins:]
    return text[:h0] + new_head + text[h1:]


# ── generated files ────────────────────────────────────────────────────────

def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def git_show(rev: str, rel: str):
    r = git("show", f"{rev}:{rel}")
    return r.stdout if r.returncode == 0 else None


def content_key(text: str) -> str:
    """Page text minus everything this script owns, whitespace-collapsed."""
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

    def absolutize(chunk: str) -> str:
        chunk = chunk.replace(' aria-current="page"', "")
        return re.sub(r'((?:href|src)=")\./', r"\1/", chunk)
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
    <!-- generated by tools/site.py build; edit the template there -->
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
    new = {p: build_head(p, t) for p, t in old.items()}
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
            errors.append(f"{rel}: out of date, run `python3 tools/site.py build`")
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
            continue
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
