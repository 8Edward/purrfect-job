"""Fake remote: a vacancy that sits in a remote feed but is really office / hybrid, or remote
only for people in one country. Phrases first (no AI); the original page behind a link in a
Telegram post is read when the post itself does not say.

Ported from freehire (MIT), Copyright (c) 2026 freehire contributors:
internal/dict/location/workmode.go (anchored phrases, RemoteContradicted with qualifiers),
title_restriction.go («[Remote-US]»), eligibility.go («US citizen», negation window),
internal/ingest/linksource (habr career, geekjob, greenhouse, lever, ashby, workable, generic
JSON-LD JobPosting). Russian phrases are our own.
"""

import html
import json
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

# -- work mode from prose: anchored phrases only (a bare «remote» / «гибрид» matches too much: «remote team»)
HYBRID = [
    "hybrid role", "hybrid working", "hybrid work ", "hybrid work.", "hybrid model", "hybrid schedule",
    "hybrid setup", "hybrid arrangement", "hybrid position", "days in the office", "days per week in the office",
    "days a week in the office", "days in office", "days onsite", "days on-site",
]
REMOTE = [
    "fully remote", "fully-remote", "100% remote", "100 % remote", "remote-first", "remote first",
    "work from anywhere", "work-from-anywhere", "remote position", "remote role", "remote job",
    "remote opportunity", "remote vacancy", "this position is remote", "role is remote", "position is remote",
    "work from home", "work-from-home", "home-based", "home based", "telecommute", "virtual position",
]
ONSITE = [
    "on-site only", "onsite only", "on site only", "fully on-site", "fully onsite", "100% on-site",
    "100% onsite", "must be on-site", "must be onsite", "on-site position", "onsite position",
    "in-office position", "work from our office", "based in our office", "based in the office",
    "on-site role", "onsite role",
]
RU_HYBRID = re.compile(
    r"гибридн\w*\s+(формат|график|режим)|формат\s*(работы)?\s*[:—–-]?\s*гибрид|\bгибрид\b(?!\w)|"
    r"\b\d\s*(?:-|–|или)?\s*\d?\s*(?:дн\w*|раз\w*)\s+(?:в\s+неделю\s+)?в\s+офисе|"
    r"(?:пару|несколько)\s+(?:дней|раз)\s+(?:в\s+неделю\s+)?в\s+офисе", re.I)
RU_REMOTE = re.compile(
    r"полн\w+\s+удал[её]н|100\s*%\s*удал[её]н|удал[её]нн\w+\s+(формат|работ)|формат\s*(работы)?\s*[:—–-]?\s*удал[её]н|"
    r"работа\s+из\s+любой\s+точки|\bудал[её]нка\b|удал[её]нно\b|дистанционн\w+\s+работ", re.I)
# denials: a posting saying it is NOT remote (overrules a «remote» label of the feed)
DENIAL = [
    "100% on-site", "100% onsite", "100 % on-site", "100 % onsite", "100 percent on-site", "100 percent onsite",
    "not a remote position", "not a remote role", "not a remote job", "not a remote opportunity",
    "position is not remote", "role is not remote", "job is not remote", "does not offer remote",
    "does not offer a remote", "do not offer remote", "do not offer a remote", "no remote work",
    "no remote option", "no remote options", "remote work is not available", "remote work is not an option",
    "remote work is not offered",
]
RU_DENIAL = re.compile(
    r"только\s+(?:в\s+)?офис\w*|работа\s+(?:только\s+)?в\s+офисе(?!\s+или)|формат\s*(?:работы)?\s*[:—–-]?\s*(?:в\s+)?офис\w*(?!\s*(?:или|/|,)\s*(?:удал|гибрид))|"
    r"офисн\w+\s+формат|удал[её]нн\w+\s+(?:работ\w+|формат\w*)\s+(?:не\s+(?:рассматрива|предусмотр|предлага)\w*|нет)|"
    r"без\s+(?:возможности\s+)?удал[её]нк\w*|не\s+удал[её]нн\w+|удал[её]нк\w*\s+(?:не\s+рассматрива\w+|нет)|"
    r"присутстви\w+\s+в\s+офисе\s+обязательн\w*|на\s+месте\s+работодателя|на\s+территории\s+работодателя", re.I)
# a denial scoped to something else: a trial period before remote, or the job after an internship
QUALIFIERS = ["for the first", "after the first", "for the initial", "after the initial", "after successful",
              "if hired", "once hired", "на время испытательного", "на испытательн", "первые", "на период адаптации",
              "в период адаптации", "на время адаптации", "на время обучения", "после испытательного"]
QUALIFIER_WINDOW = 60

# -- «remote, but only from …»
TITLE_BRACKETS = re.compile(r"\[([^\[\]]*)\]|\(([^()]*)\)")
TITLE_ANCHOR = re.compile(r"\b(remote|location|удал[её]нн?о?|удал[её]нка)\b", re.I)
GEO = {  # token -> label shown on the card
    "us": "США", "usa": "США", "u.s.": "США", "united states": "США", "america": "США", "us only": "США",
    "uk": "Великобритании", "united kingdom": "Великобритании", "gb": "Великобритании", "england": "Великобритании",
    "eu": "ЕС", "europe": "Европы", "emea": "Европы и Ближнего Востока", "canada": "Канады", "ca": "Канады",
    "latam": "Латинской Америки", "latin america": "Латинской Америки", "brazil": "Бразилии", "mexico": "Мексики",
    "india": "Индии", "germany": "Германии", "de": "Германии", "poland": "Польши", "spain": "Испании",
    "france": "Франции", "netherlands": "Нидерландов", "australia": "Австралии", "au": "Австралии",
    "philippines": "Филиппин", "apac": "Азии и Океании", "asia": "Азии", "north america": "Северной Америки",
    "na": "Северной Америки", "americas": "Америки", "israel": "Израиля", "ukraine": "Украины", "ua": "Украины",
    "portugal": "Португалии", "romania": "Румынии", "argentina": "Аргентины", "colombia": "Колумбии",
    "рф": "РФ", "россия": "РФ", "russia": "РФ", "ru": "РФ", "беларусь": "Беларуси", "рб": "Беларуси",
    "казахстан": "Казахстана", "kazakhstan": "Казахстана", "узбекистан": "Узбекистана",
}
ELIGIBILITY = [
    ("США", ["u.s. citizen", "us citizen", "united states citizen", "citizen of the united states",
             "u.s. citizenship", "us citizenship", "united states citizenship",
             "authorized to work in the united states", "authorised to work in the united states",
             "authorization to work in the united states", "authorized to work for any employer in the u.s.",
             "authorized to work for any employer in the us", "secret clearance", "ts/sci",
             "us only", "usa only", "u.s. only", "us-based only", "must be based in the us", "must reside in the us",
             "must be located in the us", "must be located in the united states", "must live in the us",
             "remote (us)", "remote - us", "remote – us", "remote, us", "remote us", "us remote", "remote-us",
             "within the united states", "anywhere in the us", "anywhere in the united states"]),
    ("ЕС", ["right to work in the eu", "right to work in the european union", "eu work authorisation",
            "eu work authorization", "valid eu work permit", "eu only", "europe only", "must be based in europe",
            "must be located in europe", "must be based in the eu"]),
    ("Великобритании", ["right to work in the uk", "right to work in the united kingdom", "eligible to work in the uk",
                        "authorised to work in the uk", "british citizen", "british citizenship", "uk only",
                        "must be based in the uk"]),
    ("Австралии", ["australian citizen", "australian citizenship", "must be based in australia"]),
    ("Канады", ["canadian citizen", "canadian citizenship", "eligible to work in canada", "canada only",
                "must be based in canada"]),
    ("Латинской Америки", ["latam only", "must be based in latin america", "must be located in latin america"]),
    ("Индии", ["india only", "must be based in india"]),
]
NEGATION_WINDOW = 80
NEGATIONS = {"not", "no", "cannot", "never", "without", "except", "unless", "excluding", "не", "без", "кроме"}
SPONSOR_TAIL = re.compile(r"without\s+(?:the\s+need\s+for\s+|any\s+|requiring\s+)?(?:current\s+or\s+future\s+)?"
                          r"(?:visa\s+|employment\s+|immigration\s+)?sponsor\w*")
RU_ONLY = re.compile(
    r"(?:только|исключительно)\s+(?:для\s+)?(?:граждан\w*|резидент\w*|кандидат\w*|соискател\w*|специалист\w*)?\s*"
    r"(?:из|в|на\s+территории|находящ\w+\s+в)?\s*(?P<a>рф|росси\w*|беларус\w*|рб|казахстан\w*|узбекистан\w*)|"
    r"(?:гражданств\w+|налогов\w+\s+резидент\w*|резидент\w*)\s+(?P<b>рф|росси\w*|рб|беларус\w*)\s+обязательн\w*|"
    r"(?:нахождение|находиться|проживание|проживать|быть)\s+(?:на\s+территории|в)\s+(?P<c>рф|росси\w*)\s*(?:обязательн\w*)?|"
    r"(?:удал[её]н\w*|удал[её]нка)\s+(?:только\s+)?(?:из|по|в\s+пределах)\s+(?P<d>рф|росси\w*)", re.I)


def fold(text):
    """Visible text: tags out, entities decoded, lower case, single spaces."""
    text = html.unescape(re.sub(r"<[^>]*>", " ", str(text or "")))
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е").replace("\xa0", " ")).strip()


def _has(text, phrases):
    return next((p for p in phrases if p in text), None)


def remote_contradicted(text):
    """The phrase with which a posting denies remote work outright, or None. A denial followed by a
    qualifier («for the first 90 days», «на время испытательного срока») is not the last word."""
    t = fold(text)
    for p in DENIAL:
        for m in re.finditer(re.escape(p), t):
            if not any(q in t[m.end():m.end() + QUALIFIER_WINDOW] for q in QUALIFIERS):
                return p
    for m in RU_DENIAL.finditer(t):
        around = t[max(0, m.start() - 40):m.end() + QUALIFIER_WINDOW]
        if re.search(r"(возможн\w*|по\s+желанию|при\s+желании|или|либо|можно|\bне)\s*$", t[max(0, m.start() - 25):m.start()]):
            continue  # «возможна работа в офисе», «удалённо или работа в офисе»: an option, not a rule
        if not any(q in around for q in QUALIFIERS):
            return m.group(0).strip()
    return None


def mode_from_text(text):
    """remote / hybrid / onsite from anchored phrases, or "" (never a guess). Hybrid beats remote,
    remote beats onsite, as in freehire; «удалёнка или гибрид» reads as remote."""
    t = fold(text)
    hyb = _has(t, HYBRID) or (RU_HYBRID.search(t) and RU_HYBRID.search(t).group(0))
    rem = _has(t, REMOTE) or (RU_REMOTE.search(t) and RU_REMOTE.search(t).group(0))
    if hyb and rem and re.search(r"удал[её]н\w*\s*(?:или|/|,)\s*гибрид|гибрид\w*\s*(?:или|/|,)\s*удал[её]н|remote or hybrid|hybrid or remote", t):
        return "remote"
    if hyb:
        return "hybrid"
    if rem:
        return "remote"
    if _has(t, ONSITE) or RU_DENIAL.search(t):
        return "onsite"
    return ""


def _negated(t, start, end):
    lo = max(0, start - NEGATION_WINDOW)
    cut = max(t.rfind(c, lo, start) for c in ".!?\n")
    lo = cut + 1 if cut >= 0 else lo
    hi = min(len(t), end + NEGATION_WINDOW)
    stops = [i for i in (t.find(c, end, hi) for c in ".!?\n") if i >= 0]
    sentence = SPONSOR_TAIL.sub(" ", t[lo:min(stops) if stops else hi])
    for w in sentence.split():
        w = w.strip(".,;:!?()\"'")
        if w in NEGATIONS or w.endswith("n't") or w.startswith("non-"):
            return True
    return False


def _geo(token):
    token = token.strip().strip(".").replace("ё", "е")
    if token in GEO:
        return GEO[token]
    for stem, label in (("росси", "РФ"), ("беларус", "Беларуси"), ("казахстан", "Казахстана"), ("узбекистан", "Узбекистана")):
        if token.startswith(stem):
            return label
    return None


def only_from(title, text):
    """Where the remote work is limited to, as labels («США», «РФ»): from a title like
    «Data Analyst [Remote-US]» / «(Remote, LATAM)» and from eligibility phrases in the text."""
    out = []
    for m in TITLE_BRACKETS.finditer(str(title or "")):
        inner = (m.group(1) or m.group(2) or "").lower().replace("-", " ")
        if not TITLE_ANCHOR.search(inner):
            continue
        rest = TITLE_ANCHOR.sub(" ", inner)
        for tok in re.split(r"[,/|&]|\bor\b|\band\b|\bonly\b|\bили\b", rest):
            label = _geo(re.sub(r"\s+", " ", tok))
            if label:
                out.append(label)
    t = fold(text)
    for label, phrases in ELIGIBILITY:
        for p in phrases:
            for m in re.finditer(re.escape(p), t):
                s, e = m.start(), m.end()
                whole = (s == 0 or not t[s - 1].isalnum()) and (e == len(t) or not t[e].isalnum() or t[e] == "s")
                if whole and not _negated(t, s, e):
                    out.append(label)
                    break
            if label in out:
                break
    for m in RU_ONLY.finditer(t):
        start = m.start()
        if re.search(r"(кроме|не\s+только|в\s+том\s+числе|а\s+также|или\s+из\s+других)\s*$", t[max(0, start - 25):start]):
            continue
        label = _geo(next(g for g in m.groups() if g))
        if label:
            out.append(label)
    return list(dict.fromkeys(out))


# -- the original page behind a link in a Telegram post

LINK_RE = re.compile(r"https?://[^\s<>()\"'«»]+")
SKIP_HOSTS = ("t.me", "telegram.me", "telegra.ph", "wa.me", "vk.com", "instagram.com", "youtube.com",
              "youtu.be", "forms.gle", "docs.google.com", "calendly.com", "linkedin.com")


def _host(url):
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def post_links(text):
    """Links in a post that may be the vacancy's own page (no chat / social / form links)."""
    out = []
    for u in LINK_RE.findall(str(text or "")):
        u = u.rstrip(".,;:!?)»")
        h = _host(u)
        if h and not any(h == s or h.endswith("." + s) for s in SKIP_HOSTS):
            out.append(u)
    return list(dict.fromkeys(out))[:3]


def _get(url, limit=3_000_000):
    with urlopen(Request(url, headers=UA), timeout=20) as r:
        return r.read(limit).decode("utf-8", "replace"), r.geturl()


def _get_json(url):
    return json.loads(_get(url)[0])


def ld_postings(page):
    """Every schema.org JobPosting in a page's ld+json blocks."""
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack += d
            elif isinstance(d, dict):
                if d.get("@type") == "JobPosting" or (isinstance(d.get("@type"), list) and "JobPosting" in d["@type"]):
                    out.append(d)
                stack += [v for k, v in d.items() if k == "@graph"]
    return out


def _places(value):
    """Country / region names out of schema.org applicantLocationRequirements / jobLocation."""
    out = []
    for v in value if isinstance(value, list) else [value]:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            addr = v.get("address") if isinstance(v.get("address"), dict) else v
            for k in ("name", "addressCountry", "addressRegion", "addressLocality"):
                x = addr.get(k)
                if isinstance(x, dict):
                    x = x.get("name")
                if isinstance(x, str) and x.strip():
                    out.append(x.strip())
                    break
    return out


def from_ld(p, url):
    remote = str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    only = _places(p.get("applicantLocationRequirements"))
    org = p.get("hiringOrganization") or {}
    return {"url": url, "title": str(p.get("title") or "")[:200], "company": str(org.get("name") if isinstance(org, dict) else org or "")[:120],
            "location": ", ".join(_places(p.get("jobLocation")))[:200], "remote": remote or None,
            "only": only[:5] if remote else [], "text": fold(p.get("description"))[:20000]}


def resolve(url):
    """The vacancy behind a link, read from the employer's ATS or the page's JSON-LD:
    {url, title, company, location, remote (True / None), only, text}; None if it is not a vacancy page.
    Raises on network errors (the caller then says «не смогли проверить», not «нет вакансии»)."""
    h, path = _host(url), urlparse(url).path
    m = re.match(r"^/([^/]+)/jobs/(\d+)/?$", path)
    if h.endswith("greenhouse.io") and m:
        api = "boards-api.eu.greenhouse.io" if "eu.greenhouse.io" in h else "boards-api.greenhouse.io"
        j = _get_json(f"https://{api}/v1/boards/{m[1]}/jobs/{m[2]}?content=true")
        if not j.get("id"):
            return None
        loc = (j.get("location") or {}).get("name") or ""
        return {"url": j.get("absolute_url") or url, "title": j.get("title", ""), "company": j.get("company_name") or m[1],
                "location": loc, "remote": True if re.search(r"remote", loc, re.I) else None, "only": [],
                "text": fold(html.unescape(j.get("content") or ""))[:20000]}
    m = re.match(r"^/([^/]+)/([0-9a-fA-F-]{36})(?:/apply)?/?$", path)
    if h == "jobs.lever.co" and m:
        p = _get_json(f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}?mode=json")
        if not p.get("id"):
            return None
        loc = (p.get("categories") or {}).get("location") or ""
        body = (p.get("description") or "") + "".join((x.get("text") or "") + (x.get("content") or "") for x in p.get("lists") or []) + (p.get("additional") or "")
        wt = str(p.get("workplaceType") or "").lower()
        return {"url": p.get("hostedUrl") or url, "title": p.get("text", ""), "company": m[1], "location": loc,
                "remote": True if wt == "remote" or re.search(r"remote", loc, re.I) else None, "only": [],
                "mode": {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}.get(wt, ""),
                "text": fold(body)[:20000]}
    if h == "jobs.ashbyhq.com" and m:
        data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{m[1]}")
        for j in data.get("jobs") or []:
            if j.get("id") == m[2]:
                wt = str(j.get("workplaceType") or "").lower()
                return {"url": j.get("jobUrl") or url, "title": j.get("title", ""), "company": m[1], "location": j.get("location") or "",
                        "remote": True if j.get("isRemote") or wt == "remote" else None, "only": [],
                        "mode": {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(wt, ""),
                        "text": fold(j.get("descriptionHtml") or j.get("descriptionPlain") or "")[:20000]}
        return None
    m = re.match(r"^/([^/]+)/j/([0-9A-Za-z]+)(?:/apply)?/?$", path)
    if h == "apply.workable.com" and m:
        data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{m[1]}?details=true")
        for j in data.get("jobs") or []:
            if j.get("shortcode") == m[2]:
                return {"url": j.get("url") or url, "title": j.get("title", ""), "company": m[1],
                        "location": ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x),
                        "remote": True if j.get("telecommuting") else None, "only": [], "text": fold(j.get("description"))[:20000]}
        return None
    # Habr Career, GeekJob and any other page that carries a schema.org JobPosting
    page, final = _get(url)
    ps = ld_postings(page)
    if not ps:
        return None
    got = from_ld(ps[0], final)
    if _host(final) == "geekjob.ru":  # geekjob's chips: «удаленная работа» / «работа в офисе»
        info = re.search(r'class="jobinfo"(.*?)</div>', page, re.S)
        info = fold(info.group(1)) if info else ""
        got["mode"] = "remote" if "удаленная работа" in info else "onsite" if "работа в офисе" in info else ""
    return got


ORIG_PER_PASS = 12  # originals read per person per pass (each is one request to someone's site)
ORIG_MAX_AGE_DAYS = 3


def pending(inbox, limit=ORIG_PER_PASS, now=None):
    """[(key, url)]: fresh Telegram posts with a vacancy link whose original was not read yet."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    out = []
    for key, item in sorted(inbox.items(), key=lambda kv: str(kv[1].get("date") or ""), reverse=True):
        if item.get("source", "tg") != "tg" or "orig" in item or item.get("status") == "hidden":
            continue
        try:
            if (now - datetime.fromisoformat(str(item.get("date")))).days > ORIG_MAX_AGE_DAYS:
                continue
        except (TypeError, ValueError):
            continue
        links = post_links(item.get("text"))
        if links:
            out.append((key, links[0]))
        if len(out) >= limit:
            break
    return out


def slim(found):
    """What is kept in the inbox about an original: the facts for the flags and a short text."""
    if not found:
        return {"none": True}
    return {"url": found.get("url", "")[:500], "title": found.get("title", "")[:200], "mode": found.get("mode", ""),
            "remote": found.get("remote"), "only": (found.get("only") or [])[:5], "text": (found.get("text") or "")[:3000]}


def flags(item, orig=None):
    """Card warnings for a vacancy: «⚠️ в оригинале офис», «⚠️ гибрид», «🌍 только США».
    orig = the resolved original page ({remote, mode, only, text}) when a TG post linked to one."""
    title, text, loc = item.get("title") or "", item.get("text") or "", item.get("location") or ""
    out = []
    if orig and (orig.get("none") or orig.get("fail")):
        orig = None
    only = []
    if orig:  # the original page wins over what the repost says
        mode = orig.get("mode") or ("remote" if orig.get("remote") else "") or mode_from_text(orig.get("text"))
        if remote_contradicted(orig.get("text")) or mode == "onsite":
            out.append("⚠️ в оригинале офис")
        elif mode == "hybrid":
            out.append("⚠️ в оригинале гибрид")
        only = [_geo(x.lower()) or x for x in orig.get("only") or []] + only_from(orig.get("title"), orig.get("text"))
    if not out and text:
        if remote_contradicted(text):
            out.append("⚠️ не удалёнка: офис")
        elif mode_from_text(f"{loc}\n{text}") == "hybrid":
            out.append("⚠️ гибрид")
    only = list(dict.fromkeys(only + only_from(title, f"{loc}\n{text}")))
    if only:
        out.append("🌍 только " + ", ".join(only[:3]))
    return out
