"""The real description of a vacancy, for roles.py's second look.

A link from a feed often goes through several hops: a tracking redirect, an aggregator page, then the
employer's own page. We follow the chain (HTTP redirects, <meta refresh>, the aggregator's own «apply /
перейти на сайт» link, rel=canonical), up to HOPS pages, and take the first page that has a real
description: an employer's ATS (via workmode.resolve), a schema.org JobPosting, or a long block of page text.
Read-only, free, stdlib only; every page is one plain GET.
"""

import html
import re
from urllib.parse import urljoin, urlparse

import workmode

HOPS = 3
MIN_TEXT = 400           # shorter than this is a stub, not a description
MAX_TEXT = 6000
SKIP = re.compile(r"(^|\.)(t\.me|telegram\.me|vk\.com|facebook\.com|instagram\.com|youtube\.com|google\.com|"
                  r"linkedin\.com/(?:company|in|feed))\b", re.I)
META_REFRESH = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\'>\s]+)', re.I)
CANONICAL = re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', re.I)
OUT_LINK = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]{0,80}(?:apply|откликнуть|перейти на сайт|на сайте компании|'
                      r'original|оригинал|подать заявку)[^<]{0,40})</a>', re.I)


def _page_text(page):
    """Visible text of the page's main part: scripts and chrome dropped."""
    body = re.sub(r"(?is)<(script|style|noscript|svg|nav|header|footer|form)\b.*?</\1>", " ", page)
    m = re.search(r"(?is)<(article|main)\b.*?</\1>", body)
    return workmode.fold(m.group(0) if m else body)[:MAX_TEXT]


def _ok(url):
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.hostname) and not SKIP.search(p.hostname + p.path)


def describe(url, hops=HOPS):
    """-> description text (≤ MAX_TEXT) or '' when no page in the chain has one. Network errors give ''."""
    seen = set()
    best = ""
    for _ in range(hops):
        if not url or url in seen or not _ok(url):
            break
        seen.add(url)
        try:
            page, final = workmode._get(url, limit=1_500_000)
        except Exception:
            break
        seen.add(final)
        got = None
        try:  # an employer's own ATS (its API) or a JSON-LD JobPosting on the page: the best sources
            if re.search(r"(greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com)$", workmode._host(final)):
                got = workmode.resolve(final)
            else:
                ps = workmode.ld_postings(page)
                got = workmode.from_ld(ps[0], final) if ps else None
        except Exception:
            got = None
        if got and len(got.get("text") or "") >= MIN_TEXT:
            return got["text"][:MAX_TEXT]
        text = _page_text(page)
        if len(text) > len(best):
            best = text
        nxt = None
        m = META_REFRESH.search(page)
        if m:
            nxt = m.group(1)
        else:
            o = OUT_LINK.search(page)
            if o:
                nxt = o.group(1)
            else:
                c = CANONICAL.search(page)
                if c and c.group(1) not in seen:
                    nxt = c.group(1)
        if not nxt:
            break
        url = urljoin(final, html.unescape(nxt))
    return best if len(best) >= MIN_TEXT else ""


def link_of(job):
    """The page to read for a feed job: a site vacancy's own link; for a Telegram post the first
    vacancy-looking link in it."""
    if job.get("source") == "tg":
        links = workmode.post_links(job.get("text"))
        return links[0] if links else ""
    return job.get("link") or ""
