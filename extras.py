"""Feed helpers for the mini app: salary range, time zone filter, merged duplicates,
the application tracker, the morning digest, AI scoring of a vacancy and the AI career plan.

Rules, no AI, except `score_prompt`/`plan_prompt` (run through resume_ai.Claude on the owner's
subscription, with their own daily caps).
"""

import html
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone

import resume
import resume_ai
import tg_collect as tc
import workmode

# ---------------------------------------------------------------- 💰 salary range

CUR = r"(?:\$|€|£|₽|₸|usdt|usd|eur|gbp|rub|kzt|руб(?:лей|\.)?|р\.|тенге|долл(?:аров|\.)?|евро)"
AMT = r"\d{1,3}(?:[  .,]\d{3})+|\d+(?:[.,]\d+)?"
MULT = r"(?:\s?(?:k|к|тыс\.?|thousand|млн))?"
ONE = rf"(?:{CUR}\s?)?(?:{AMT}){MULT}(?:\s?{CUR})?"
PER = r"(?:\s?(?:/|в|per|a)\s?(?:мес\w*|месяц|month|mo|час|hour|hr|h|год|year|yr|annum)\b)?"
SALARY_RE = re.compile(rf"(?<![\w.])(?:(?:от|до|from|up to)\s)?{ONE}(?:\s?(?:-|–|—|до|to)\s?{ONE})?{PER}", re.IGNORECASE)
CUR_RE = re.compile(CUR + r"|\d\s?(?:k|к|тыс)\b", re.IGNORECASE)
PAY_WORD = re.compile(r"(зарплат|з/п|\bзп\b|оклад|вилк|доход|salary|compensation|\bpay\b|\brate\b|ставк|budget|бюджет)", re.IGNORECASE)


def salary_in(text):
    """The pay mentioned in a vacancy text, as written («$3000–4000», «от 150 000 ₽»), or ""."""
    best = ""
    for m in SALARY_RE.finditer(text or ""):
        frag = m.group(0).strip(" -–—,.:")
        if not re.search(r"\d", frag):
            continue
        digits = re.sub(r"\D", "", frag)
        has_cur = bool(re.search(CUR, frag, re.IGNORECASE))
        mult = bool(re.search(r"\d\s?(?:k|к|тыс|млн)", frag, re.IGNORECASE))
        near = PAY_WORD.search((text or "")[max(0, m.start() - 40):m.start()])
        if not (has_cur or (near and (mult or len(digits) >= 4))):
            continue
        if not has_cur and not mult and len(digits) == 4 and 1990 <= int(digits) <= 2035:
            continue  # a year
        if has_cur and len(digits) < 2:
            continue
        if has_cur:
            return frag[:40]
        best = best or frag[:40]
    return best


def salary_of(item):
    comp = (item.get("comp") or "").strip()
    if comp and not re.match(r"^не указан", comp, re.IGNORECASE):
        return comp[:60]
    return salary_in(" ".join([item.get("title") or "", item.get("text") or ""])[:6000])


# ---------------------------------------------------------------- 🕒 time zones

ABBR = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
        "CET": 1, "CEST": 2, "EET": 2, "EEST": 3, "WET": 0, "BST": 1, "MSK": 3, "МСК": 3, "IST": 5.5,
        "SGT": 8, "HKT": 8, "JST": 9, "KST": 9, "AEST": 10, "AEDT": 11, "NZST": 12, "ICT": 7, "WIB": 7}
ABBR_RE = re.compile(r"(?<![A-Za-zА-Яа-я])(" + "|".join(ABBR) + r")(?![A-Za-zА-Яа-я])")
OFFSET_RE = re.compile(r"\b(?:UTC|GMT)\s?([+\-−–]\s?\d{1,2}(?:[:.]\d{2})?)(?![\d±])", re.IGNORECASE)
REGIONS = [  # (pattern, offsets) — only when the text ties the region to time / location rules
    (r"(?:north america|americas|(?-i:\bUS\b)|\busa\b|u\.s\.|canada|latam|latin america)", [-8, -7, -6, -5, -4, -3]),
    (r"(?:europe|\beu\b|emea|(?-i:\bUK\b))", [0, 1, 2, 3]),
    (r"(?:apac|asia|asia[- ]pacific|australia)", [5.5, 7, 8, 9, 10]),
]
REGION_TIE = r"(?:only|time ?zones?|hours|based|overlap|residents?|located)"
MOSCOW_RE = re.compile(r"(по москве|\bмск\b|московск\w* времен)", re.IGNORECASE)


def tz_offsets(text):
    """Time zones a vacancy asks for, as UTC offsets; [] when it says nothing about it."""
    text = text or ""
    found = []
    for m in OFFSET_RE.finditer(text):
        raw = m.group(1).replace("−", "-").replace("–", "-").replace(" ", "")
        try:
            h, _, mins = raw.replace(".", ":").partition(":")
            value = int(h) + (0.5 if mins.startswith("3") else 0) * (1 if not h.startswith("-") else -1)
        except ValueError:
            continue
        if -12 <= value <= 14:
            found.append(value)
    found += [ABBR[m.group(1)] for m in ABBR_RE.finditer(text)]
    if MOSCOW_RE.search(text):
        found.append(3)
    for pat, offsets in REGIONS:
        if (re.search(pat + r"[\w\s,/()-]{0,25}?" + REGION_TIE, text, re.IGNORECASE)
                or re.search(REGION_TIE + r"[\w\s:,/()-]{0,15}?" + pat, text, re.IGNORECASE)):
            found += offsets
    return sorted(set(found))


def fmt_offset(o):
    sign = "+" if o >= 0 else "−"
    o = abs(o)
    return f"UTC{sign}{int(o)}" + (":30" if o % 1 else "")


def tz_label(item):
    offs = tz_offsets(" ".join([item.get("location") or "", item.get("text") or "", item.get("title") or ""])[:6000])
    if not offs:
        return ""
    return fmt_offset(offs[0]) if len(offs) == 1 else f"{fmt_offset(offs[0])}…{fmt_offset(offs[-1])}"


def tz_gap(a, b):
    d = abs(a - b) % 24
    return min(d, 24 - d)


def tz_settings(member):
    tz = member.load_search().get("tz") or {}
    mine = tz.get("mine", 7 if member.is_owner else 3)
    try:
        mine = max(-12, min(14, float(mine)))
    except (TypeError, ValueError):
        mine = 7
    return {"on": bool(tz.get("on", False)), "mine": mine, "spread": int(tz.get("spread", 4) or 4)}


def set_tz(member, **fields):
    search = member.load_search()
    tz = dict(search.get("tz") or {})
    if "on" in fields:
        tz["on"] = bool(fields["on"])
    if "mine" in fields:
        tz["mine"] = max(-12.0, min(14.0, round(float(fields["mine"]) * 2) / 2))
    if "spread" in fields and int(fields["spread"]) in (1, 2, 3, 4, 6, 8):
        tz["spread"] = int(fields["spread"])
    search["tz"] = tz
    member.save_search(search)


def tz_fits(item, tz):
    if not tz["on"]:
        return True
    offs = tz_offsets(" ".join([item.get("location") or "", item.get("text") or "", item.get("title") or ""])[:6000])
    return not offs or any(tz_gap(o, tz["mine"]) <= tz["spread"] for o in offs)


# ---------------------------------------------------------------- 🎚 grade (level) of a vacancy

GRADES = [("intern", "Стажёр"), ("junior", "Junior"), ("middle", "Middle"), ("senior", "Senior"), ("lead", "Lead")]
GRADE_RE = [  # every level the title names counts: «Middle/Senior» is both
    ("lead", re.compile(r"\b(lead|head of|principal|staff|team ?lead|тимлид|руководитель|начальник|директор|head)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|старший|ведущий|главный|сеньор)(?!\w)", re.I)),
    ("middle", re.compile(r"\b(middle|mid|миддл|мидл)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|младший|джуниор|джун)(?!\w)", re.I)),
    ("intern", re.compile(r"\b(intern|internship|trainee|стажёр|стажер|стажировка)\b", re.I)),
]


def grade_of(item):
    """Level named in the title, else '' (the vacancy says nothing). «Middle/Senior» counts as both."""
    title = item.get("title") or ""
    found = [g for g, rx in GRADE_RE if rx.search(title)]
    return ",".join(found) if found else ""


def grade_fits(item, wanted):
    """No filter, an unnamed level, or the wanted level among the named ones."""
    if not wanted:
        return True
    have = grade_of(item)
    return not have or wanted in have.split(",")


# ---------------------------------------------------------------- 🗓 how old a vacancy is

OLD_DAYS = 14


def is_old(item):
    """Posted more than two weeks ago (likely already filled)."""
    try:
        return (now() - datetime.fromisoformat(item["date"])).days > OLD_DAYS
    except (TypeError, ValueError, KeyError):
        return False


def age_cutoff(member, days):
    """Posted since: 1 = today (the person's calendar day), 3 / 7 / 14 = that many days back."""
    if days <= 1:
        local = now() + timedelta(hours=tz_settings(member)["mine"])
        return local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=tz_settings(member)["mine"])
    return now() - timedelta(days=days)


# ---------------------------------------------------------------- 🌏 which countries a vacancy hires from

# key (Himalayas' own country name) -> (Russian name, other spellings, regions it belongs to)
COUNTRIES = {
    "Thailand": ("Таиланд", ["thailand", "bangkok", "phuket"], ["asia", "apac", "sea"]),
    "Russian Federation": ("Россия", ["russia", "russian federation", "россия", "рф"], ["europe", "emea", "cis"]),
    "Kazakhstan": ("Казахстан", ["kazakhstan", "казахстан"], ["asia", "cis"]),
    "Uzbekistan": ("Узбекистан", ["uzbekistan", "узбекистан"], ["asia", "cis"]),
    "Kyrgyzstan": ("Киргизия", ["kyrgyzstan", "киргизия", "кыргызстан"], ["asia", "cis"]),
    "Belarus": ("Беларусь", ["belarus", "беларусь"], ["europe", "emea", "cis"]),
    "Ukraine": ("Украина", ["ukraine", "украина"], ["europe", "emea"]),
    "Georgia": ("Грузия", ["georgia", "грузия"], ["europe", "emea"]),
    "Armenia": ("Армения", ["armenia", "армения"], ["europe", "emea", "cis"]),
    "Azerbaijan": ("Азербайджан", ["azerbaijan", "азербайджан"], ["europe", "emea", "cis"]),
    "Serbia": ("Сербия", ["serbia", "сербия"], ["europe", "emea"]),
    "Montenegro": ("Черногория", ["montenegro", "черногория"], ["europe", "emea"]),
    "Turkey": ("Турция", ["turkey", "türkiye", "турция"], ["europe", "emea"]),
    "Cyprus": ("Кипр", ["cyprus", "кипр"], ["europe", "emea", "eu"]),
    "Poland": ("Польша", ["poland", "польша"], ["europe", "emea", "eu"]),
    "Germany": ("Германия", ["germany", "германия"], ["europe", "emea", "eu"]),
    "Spain": ("Испания", ["spain", "испания"], ["europe", "emea", "eu"]),
    "Portugal": ("Португалия", ["portugal", "португалия"], ["europe", "emea", "eu"]),
    "United Arab Emirates": ("ОАЭ", ["united arab emirates", "uae", "dubai", "оаэ"], ["emea", "mena"]),
    "Indonesia": ("Индонезия", ["indonesia", "bali", "индонезия"], ["asia", "apac", "sea"]),
    "Vietnam": ("Вьетнам", ["vietnam", "вьетнам"], ["asia", "apac", "sea"]),
    "Malaysia": ("Малайзия", ["malaysia", "малайзия"], ["asia", "apac", "sea"]),
    "Philippines": ("Филиппины", ["philippines", "филиппины"], ["asia", "apac", "sea"]),
    "India": ("Индия", ["india", "индия"], ["asia", "apac"]),
}
EVERYWHERE = r"(?:worldwide|anywhere|global(?:ly)?|any (?:country|location)|work from anywhere|весь мир|любая страна|из любой страны)"
REGION_WORDS = {
    "asia": r"(?:asia)", "apac": r"(?:apac|asia[- ]?pacific)", "sea": r"(?:south[- ]?east asia|sea region)",
    "europe": r"(?:europe)", "emea": r"(?:emea)", "eu": r"(?:(?-i:\bEU\b)|european union)", "cis": r"(?:(?-i:\bCIS\b))",
    "mena": r"(?:mena|middle east)",
}
OTHER_PLACES = (r"(?:(?-i:\bUS\b)|\busa\b|u\.s\.|united states|canada|(?-i:\bUK\b)|united kingdom|latam|latin america|"
                r"americas|north america|south america|brazil|mexico|argentina|colombia|australia|new zealand|africa|"
                r"france|netherlands|ireland|italy|sweden|japan|china|singapore|south korea|israel)")


def country_settings(member):
    """{on, key, name}: the person's country; on = hide vacancies that clearly don't hire from there."""
    c = member.load_search().get("country") or {}
    key = c.get("key") if c.get("key") in COUNTRIES else ("Thailand" if member.is_owner else "Russian Federation")
    return {"on": bool(c.get("on", member.is_owner)), "key": key, "name": COUNTRIES[key][0]}


def set_country(member, **fields):
    search = member.load_search()
    c = dict(search.get("country") or {})
    if "on" in fields:
        c["on"] = bool(fields["on"])
    if "key" in fields:
        if fields["key"] not in COUNTRIES:
            raise ValueError(fields["key"])
        c["key"] = fields["key"]
    search["country"] = c
    member.save_search(search)


def hires_from(item, key):
    """True / False when the vacancy says who it hires (Himalayas' country list, or a location naming countries or
    regions); None when it says nothing that settles it."""
    countries = item.get("countries")
    if isinstance(countries, list):  # Himalayas: [] = anyone, else the exact list
        return not countries or key in countries
    listed = (item.get("orig") or {}).get("countries")  # the original page's own list (RVC and other JSON-LD pages)
    if isinstance(listed, list) and listed:
        _, names, _ = COUNTRIES[key]
        return any(str(c).strip().lower() in names or str(c).strip().lower() == COUNTRIES[key][0].lower() for c in listed)
    loc =" ".join(str(item.get(k) or "") for k in ("location",))[:600]
    if not loc.strip():
        return None
    _, names, regions = COUNTRIES[key]
    mine = [re.escape(n) for n in names] + [REGION_WORDS[r] for r in regions]
    if re.search(EVERYWHERE, loc, re.IGNORECASE) or re.search(r"(?<!\w)(?:" + "|".join(mine) + r")(?!\w)", loc, re.IGNORECASE):
        return True
    # only Latin names say «not from here»: Russian sites write «Россия» for where the company is, not who it hires
    others = [OTHER_PLACES] + [re.escape(n) for k, (_, ns, _) in COUNTRIES.items() if k != key for n in ns if n.isascii()] + \
             [w for r, w in REGION_WORDS.items() if r not in regions]
    if re.search(r"(?<!\w)(?:" + "|".join(others) + r")(?!\w)", loc, re.IGNORECASE):
        return False
    return None


ASIA_LABELS = {"Азии", "Азии и Океании"}  # «only for …» labels of workmode that include Thailand and its neighbours


def unfit_for(item, key):
    """Office / hybrid, or remote only for another country: the vacancy cannot be taken from `key`'s country.
    A starred or responded one is never unfit. Uses the original page's country list when it was read."""
    if item.get("fav") or (item.get("track") or {}).get("stage"):
        return False
    orig = item.get("orig") or {}
    if workmode.not_remote(item, orig):
        return True
    if orig.get("countries") and hires_from(item, key) is False:
        return True
    labels = [x for f in workmode.flags(item, orig) if f.startswith("🌍 только ") for x in f[len("🌍 только "):].split(", ")]
    return bool(labels) and not (set(labels) & ASIA_LABELS)


def country_fits(item, cs):
    return not cs["on"] or hires_from(item, cs["key"]) is not False


# ---------------------------------------------------------------- 🔗 one vacancy from several sources

def _norm(s):
    return re.sub(r"[^\w]+", " ", str(s or "").lower().replace("ё", "е")).strip()


def link_key(link):
    """A link without tracking parameters. The query stays: on some career pages it IS the vacancy
    (plata.careers/vacancy/details?id=…), and cutting it merged all 179 Plata vacancies into one."""
    base, _, query = link.partition("?")
    kept = sorted(p for p in query.split("&") if p and not re.match(r"(utm_|ref=|source=|fbclid|gclid|_ga)", p, re.I))
    return (base.rstrip("/") + ("?" + "&".join(kept) if kept else "")).lower()


# Ported from freehire (MIT), Copyright (c) 2026 freehire contributors
# (internal/job/jobhash: rolekey.go, rolefingerprint.go, descsimilarity.go).
# «Senior Analyst, Berlin» / «Senior Analyst — Remote» -> «senior analyst»: the last clause after a comma or a
# spaced dash / pipe / @ goes, unless that leaves under two words; brackets are unwrapped, their words kept.
TRAILING_CLAUSE = re.compile(r"(\s*,\s*|\s+[|@]\s*|\s+[-–—]\s*)[^,|@\-–—]*$")
DESC_SAME = 0.95  # Jaccard of description words: per-city copies of one role score above, different roles far below


def role_title(title):
    t = re.sub(r"[()\[\]]", " ", str(title or ""))
    stripped = TRAILING_CLAUSE.sub("", t)
    return _norm(stripped if len(stripped.split()) >= 2 else t)


def desc_words(text):
    """The set of distinct words longer than 3 letters (a set: repeating a word says nothing more)."""
    return {w for w in re.findall(r"\w+", re.sub(r"<[^>]*>", " ", html.unescape(str(text or ""))).lower().replace("ё", "е"))
            if len(w) > 3}


def desc_similarity(a, b):
    """Jaccard index; an empty description is never similar to anything, itself included."""
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return shared / (len(a) + len(b) - shared)


def same_role(a, b):
    """Two items with the same company + role title: the same vacancy when they come from different
    sources (one role cross-posted). From ONE source they are separate postings (a company can open
    three «Data Analyst» roles in different teams) unless their texts match ≥95% (per-city copies)."""
    if origin(a) != origin(b) or a.get("source") != b.get("source"):
        return True
    if not (a.get("text") or b.get("text")) and a.get("date") and str(a.get("date")) == str(b.get("date")):
        return True  # hh.ru lists one role once per city with no text, all published at the same second
    return desc_similarity(desc_words(a.get("text")), desc_words(b.get("text"))) >= DESC_SAME


def twin_keys(item):
    """Things that make two inbox items the same vacancy: same role title + company (see same_role),
    same link, same post text (a repost into another channel)."""
    keys = []
    title, company = role_title(item.get("title")), _norm(re.split(r"[.|(]\s", str(item.get("company") or ""))[0])  # «Альфа-Банк. Центральный офис» = «Альфа-Банк»
    if title and company:
        keys.append("tc:" + title + "|" + company)
    if item.get("link"):
        keys.append("l:" + link_key(item["link"]))
    if item.get("source", "tg") == "tg" and len(item.get("text") or "") >= 120:
        keys.append("f:" + tc.fingerprint(item["text"]))
    return keys


def richness(item):
    return (bool(salary_of(item)), item.get("source") == "web", len(item.get("text") or ""))


def groups(pairs):
    """[(key, item)] -> [[(key, item), ...]] with duplicates together, the richest first.
    Input order (newest first) is kept for the group leaders."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner, bucket = {}, {}
    for key, item in pairs:
        find(key)
        for t in twin_keys(item):
            if t.startswith("tc:"):  # company + role title: merged only where same_role says so
                for k2, it2 in bucket.setdefault(t, []):
                    if same_role(item, it2):
                        parent[find(key)] = find(k2)
                bucket[t].append((key, item))
            elif t in owner:
                parent[find(key)] = find(owner[t])
            else:
                owner[t] = key
    out, at = [], {}
    for key, item in pairs:
        root = find(key)
        if root not in at:
            at[root] = len(out)
            out.append([])
        out[at[root]].append((key, item))
    for g in out:
        g.sort(key=lambda kv: richness(kv[1]), reverse=True)
    return out


def twins_of(inbox, key):
    """Other inbox keys that are the same vacancy as `key`."""
    for g in groups(list(inbox.items())):
        keys = [k for k, _ in g]
        if key in keys:
            return [k for k in keys if k != key]
    return []


def origin(item):
    return item.get("site") if item.get("source") == "web" else item.get("chat", "")


# ---------------------------------------------------------------- 📋 application tracker

STAGES = {"applied": "Откликнулся", "test": "Тестовое", "interview": "Собеседование", "offer": "Оффер", "rejected": "Отказ"}
ACTIVE = ("applied", "test", "interview")
SILENCE = timedelta(days=7)


def now():
    return datetime.now(timezone.utc)


def set_stage(item, stage, note=None):
    """Move one vacancy along the tracker; stage None/"" takes it off the tracker."""
    if not stage:
        item.pop("track", None)
        return
    if stage not in STAGES:
        raise ValueError("Неизвестный этап")
    track = item.get("track") or {"log": []}
    if track.get("stage") != stage:
        stamp = now().isoformat(timespec="seconds")
        track.update(stage=stage, at=stamp)
        track["log"] = (track.get("log") or []) + [[stage, stamp]]
        track.pop("reminded", None)
    if note is not None:
        track["note"] = str(note).strip()[:500]
    item["track"] = track


def silent_days(item):
    track = item.get("track") or {}
    if track.get("stage") not in ACTIVE or not track.get("at"):
        return 0
    return (now() - datetime.fromisoformat(track["at"])).days


def manual_item(title, company="", link="", note=""):
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:120]
    if len(title) < 2:
        raise ValueError("Напишите, на какую должность отклик")
    link = str(link or "").strip()[:500]
    if link and not re.match(r"^https?://", link):
        link = ""
    return "manual_" + secrets.token_hex(5), {
        "source": "web", "site": "вручную", "title": title, "company": re.sub(r"\s+", " ", str(company or "")).strip()[:80],
        "link": link, "date": now().isoformat(), "status": "taken", "fav": False, "tags": [], "text": str(note or "")[:2000],
        "location": "", "comp": "", "via": "manual"}


def due_reminders(inbox):
    """Tracked vacancies that have been silent for a week and weren't reminded about yet."""
    out = []
    for key, item in inbox.items():
        track = item.get("track") or {}
        if silent_days(item) >= SILENCE.days and track.get("reminded") != track.get("at"):
            out.append((key, item))
    return out


# ---------------------------------------------------------------- ☀️ morning digest

DIGEST_HOUR = 9     # local time of the person (their «мой часовой пояс», UTC+7 for the owner)
DIGEST_SIZE = 5
DIGEST_ENABLED = False  # in the backlog for now (2026-10-03): the code stays, nothing is sent


def local_now(member):
    return now() + timedelta(hours=tz_settings(member)["mine"])


def digest_due(member, state):
    if not DIGEST_ENABLED or state.get("digest_off"):
        return False
    local = local_now(member)
    return DIGEST_HOUR <= local.hour < 23 and state.get("digest_date") != local.date().isoformat()


def rank(item, role_words):
    title = (item.get("title") or "").lower()
    score = 0.0
    if (item.get("ai") or {}).get("score"):
        score += item["ai"]["score"] * 2
    if any(re.search(w, title, re.IGNORECASE) for w in role_words):
        score += 3
    if "ru-company" in (item.get("tags") or []):
        score += 1
    if salary_of(item):
        score += 1
    return score


def digest_items(member, inbox):
    """Top new vacancies of the last 24 hours, duplicates merged, time zone filter applied."""
    since, tz = now() - timedelta(days=1), tz_settings(member)
    fresh = [(k, v) for k, v in inbox.items()
             if v.get("status") == "new" and datetime.fromisoformat(v["date"]) >= since and tz_fits(v, tz)]
    fresh.sort(key=lambda kv: kv[1]["date"], reverse=True)
    leaders = [g[0] for g in groups(fresh)]
    words = [w for w in (member.cfg().get("keywords", {}).get("role") or []) if isinstance(w, str)]
    safe = []
    for w in words:
        try:
            re.compile(w)
            safe.append(w)
        except re.error:
            safe.append(re.escape(w))
    leaders.sort(key=lambda kv: rank(kv[1], safe), reverse=True)
    return leaders[:DIGEST_SIZE], len(leaders)


# ---------------------------------------------------------------- ✨ AI: vacancy score

SCORE_DAILY = {"owner": 40, "guest": 10}


def scores_left(member):
    cap = SCORE_DAILY["owner" if member.is_owner else "guest"]
    used = member.load_state().get("ai_scores") or {}
    return cap - (used.get("n", 0) if used.get("date") == date.today().isoformat() else 0)


def count_score(member):
    used = member.load_state().get("ai_scores") or {}
    n = used.get("n", 0) if used.get("date") == date.today().isoformat() else 0
    member.update_state(ai_scores={"date": date.today().isoformat(), "n": n + 1})


def candidate_text(member):
    """The person's résumé: the active version in «Резюме», else the uploaded file's text."""
    try:
        _, md = resume.get_version(member)
        if len(md.strip()) >= 200:
            return md[:12000]
    except ValueError:
        pass
    path = (tc.ROOT / "data" / "tg-cv.txt") if member.is_owner else member.dir / "cv.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")[:12000]
    return ""


SCORE_RULES = """You are a careful career advisor scoring how well ONE vacancy fits ONE candidate.
Rules: judge only from the résumé and the vacancy text given; never assume skills, years or facts the résumé
doesn't state; if the vacancy text is thin, say so and score cautiously. Write in Russian. Output ONLY JSON."""


def score_prompt(member, item, vacancy, thin):
    tz = tz_settings(member)
    words = ", ".join(tc_plain(w) for w in (member.cfg().get("keywords", {}).get("role") or [])[:15])
    return (f"""Score the fit of this vacancy for the candidate on a 1.0–5.0 scale (one decimal):
5 = apply today, 4 = good fit, 3 = so-so, 2 = weak, 1 = not for them.
Consider: role and seniority match, required skills vs résumé, location/time zone/work-permit limits
(candidate works remotely, lives in {fmt_offset(tz['mine'])}), language, red flags.
Return ONE JSON object: {{"score": 4.2, "verdict": "<one short sentence, max 14 words>",
"pros": ["<max 3 short points, facts from both texts>"], "cons": ["<max 3 short points: gaps, limits, risks>"]}}
{"NOTE: only the title/company of the vacancy is known, the full text is not available." if thin else ""}
Candidate is looking for: {words or "see résumé"}.

VACANCY:
{vacancy[:6000]}

RÉSUMÉ:
{candidate_text(member)}""")


def tc_plain(pattern):
    return re.sub(r"\\(.)", r"\1", str(pattern))


def parse_score(text):
    data = resume_ai.parse_json(text)
    try:
        score = round(max(1.0, min(5.0, float(data.get("score")))), 1)
    except (TypeError, ValueError):
        raise RuntimeError("bad-answer")
    clip = lambda xs: [str(x).strip()[:160] for x in (xs or []) if str(x).strip()][:3]  # noqa: E731
    return {"score": score, "verdict": str(data.get("verdict") or "").strip()[:200],
            "pros": clip(data.get("pros")), "cons": clip(data.get("cons")), "at": now().isoformat(timespec="seconds")}


# ---------------------------------------------------------------- ✨ AI: career plan

GOALS = {"grade": "поднять грейд в той же роли", "role": "сменить роль или сферу (другая профессия и/или отрасль)", "field": "сменить направление / отрасль"}  # field: old plans only

PLAN_RULES = """You are a pragmatic career coach. You build a step-by-step transition plan for one person.
HARD RULES: describe the person's CURRENT position ONLY from facts in the résumé — never invent employers,
years, numbers, tools or achievements. Things they should LEARN or DO are fine to name, but mark clearly that
they are new. If a key fact is missing (years, level, salary), ask in "questions" instead of guessing.
Write in Russian, concrete and short. Output ONLY JSON."""


def plan_prompt(md, goal, target):
    return f"""Goal: {GOALS.get(goal, goal)}{(" → " + target) if target else ""}.
Return ONE JSON object:
{{"now": "<2–3 sentences: where the person is today, only from the résumé>",
 "target": "<the target role/grade in one line>",
 "strengths": ["<up to 4: what from the résumé already helps for this goal>"],
 "gaps": ["<up to 5: what is missing for the goal>"],
 "steps": [{{"title": "<step>", "what": "<what exactly to do>", "weeks": "<rough time, e.g. 2–4 нед.>"}}],
 "resume": ["<up to 3: what to change in the résumé for this goal, without inventing facts>"],
 "questions": ["<up to 4: facts to ask the person>"]}}
5–8 steps in order, each realistic for someone job-hunting at the same time.

RÉSUMÉ:
{md[:12000]}"""


def parse_plan(text, md):
    data = resume_ai.parse_json(text)
    lst = lambda xs, n: [str(x).strip()[:300] for x in (xs or []) if isinstance(x, str) and x.strip()][:n]  # noqa: E731
    steps = []
    for s in (data.get("steps") or [])[:10]:
        if isinstance(s, dict) and s.get("title"):
            steps.append({k: str(s.get(k) or "").strip()[:400] for k in ("title", "what", "weeks")})
    if not steps:
        raise RuntimeError("bad-answer")
    now_ = str(data.get("now") or "").strip()[:600]
    strengths = lst(data.get("strengths"), 4)
    # the «today» part may only use what the résumé says: drop lines that bring new numbers or tools
    flagged = [x for x in [now_] + strengths if resume_ai.invented(x, md)]
    if now_ in flagged:
        now_ = ""
    strengths = [x for x in strengths if x not in flagged]
    return {"now": now_, "target": str(data.get("target") or "").strip()[:200], "strengths": strengths,
            "gaps": lst(data.get("gaps"), 5), "steps": steps, "resume": lst(data.get("resume"), 3),
            "questions": lst(data.get("questions"), 4), "dropped": len(flagged)}


def dumps(x):
    return json.dumps(x, ensure_ascii=False)


# ---------------------------------------------------------------- 🔢 sorting by pay, 📝 short summary, 📊 funnel

RATE = [  # rough conversion to USD per month, only to put cards in order (never shown)
    (r"₽|руб|rub|\bр\.", 1 / 90), (r"€|eur|евро", 1.1), (r"£|gbp", 1.27), (r"₸|kzt|тенге", 1 / 480),
    (r"so'?m|сум|uzs", 1 / 12700), (r"\$|usd|долл", 1.0),
]


def salary_value(item):
    """Upper pay figure of a vacancy in ~USD a month, or 0 when there is none (for sorting only)."""
    s = salary_of(item)
    if not s:
        return 0.0
    low = s.lower()
    nums = []
    for m in re.finditer(r"(\d{1,3}(?:[  .,]\d{3})+|\d+(?:[.,]\d+)?)\s?(k|к|тыс\.?|млн)?", low):
        raw = m.group(1)
        v = float(re.sub(r"[  .,](?=\d{3}\b)", "", raw).replace(",", "."))
        v *= {"k": 1e3, "к": 1e3, "тыс": 1e3, "тыс.": 1e3, "млн": 1e6}.get(m.group(2) or "", 1)
        nums.append(v)
    if not nums:
        return 0.0
    value = max(nums)
    rate = next((r for pat, r in RATE if re.search(pat, low)), 1 / 90 if value > 20000 else 1.0)
    value *= rate
    if re.search(r"/\s?(h|hr|hour|час)|в час|per hour|hourly", low):
        value *= 160
    elif re.search(r"year|год|annum|annual|/\s?yr", low) or value > 30000:
        value /= 12
    return round(value, 2)


STACK = ["SQL", "Python", "R", "Excel", "Google Sheets", "Power BI", "Tableau", "Looker", "Metabase", "Superset", "DataLens",
         "Airflow", "dbt", "Spark", "Hadoop", "Kafka", "ClickHouse", "PostgreSQL", "Greenplum", "MySQL", "BigQuery",
         "Snowflake", "Redshift", "Pandas", "NumPy", "scikit-learn", "PyTorch", "TensorFlow", "AWS", "GCP", "Azure",
         "Docker", "Kubernetes", "Git", "Linux", "Java", "Kotlin", "Go", "JavaScript", "TypeScript", "React", "Node.js",
         "C++", "C#", ".NET", "PHP", "1С", "Figma", "Jira", "Confluence", "A/B", "ETL", "DWH", "ROS", "MATLAB"]
STACK_RE = [(name, re.compile(r"(?<![\w+#.])" + re.escape(name) + r"(?![\w+#])", 0 if len(name) <= 2 else re.IGNORECASE))
            for name in STACK]
FORMAT = [(r"удал[её]н|remote|из любой точки|anywhere", "удалёнка"), (r"гибрид|hybrid", "гибрид"),
          (r"\bофис\b|on-?site|in office", "офис"), (r"полн\w* (день|занятост)|full[- ]time", "полный день"),
          (r"частичн\w* занятост|неполн\w* (день|занятост)|part[- ]time|\d{2}\s?(ч|час\w*)\s*/?\s*(в\s*)?нед", "частичная"),
          (r"\bконтракт\b|contract|\bb2b\b|фриланс|freelance|проектн\w* работ", "контракт / проект")]
DUTY_HEAD = re.compile(r"(обязанност\w*|задачи|чем (предстоит|нужно|будешь|будете) заниматься|что (нужно )?делать|"
                       r"responsibilit\w*|what you.?ll do|your role|you will)\s*[:—–-]?\s*", re.IGNORECASE)


def summary(item, size=2):
    """2–3 short lines about a vacancy, taken from its own text (no AI): stack, format, the first duties."""
    title, text = item.get("title") or "", item.get("text") or ""
    blob = f"{title}\n{text}"
    lines = []
    warn = workmode.flags(item, item.get("orig"))  # fake remote: office / hybrid / one country only
    if warn:
        lines.append(" · ".join(warn))
    real = item.get("reality")  # set by reality.apply: ♾ evergreen / 🪦 vanished from its site
    if real:
        head = {"evergreen": "♾ похоже на вечную вакансию: ", "stale": "🕰 висит больше 30 дней: ", "reposted": "🔁 перевыложена: "}.get(
            real.get("kind"), "🪦 похоже, уже закрыта: ")
        lines.append(head + str(real.get("why") or ""))
    stack = [name for name, rx in STACK_RE if rx.search(blob)][:7]
    if stack:
        lines.append("🛠 " + ", ".join(stack))
    fmt = list(dict.fromkeys(label for pat, label in FORMAT if re.search(pat, blob, re.IGNORECASE)))
    if fmt:
        lines.append("🏠 " + " · ".join(fmt[:3]))
    m = DUTY_HEAD.search(text)
    if m:
        rest = text[m.end():m.end() + 600]
        parts = [re.sub(r"^[\s\-–—•*·▪●◦✓✔️▫️🔹🔸]+", "", p).strip() for p in re.split(r"\n|;|•|(?<=\.)\s", rest)]
        parts = [p for p in parts if len(p) > 12 and not re.match(r"(требовани|мы предлагаем|условия|requirements|резюме|контакт|пиши|отклик|apply|send|зарплат|salary)", p, re.I)][:size]
        if parts:
            lines.append("📌 " + "; ".join(p[:90].rstrip(" ,.;") for p in parts))
    return lines[:3]


def funnel(inbox, fits=None, jobs=True):
    """Numbers for the feed header: vacancies found (duplicates merged), responses, interviews, offers.
    «Found» counts what fits the person's current words, channels and sites (fits), whatever its status.
    A stage counts once it has been reached, so an interview is also a response."""
    reached = {"applied": 0, "interview": 0, "offer": 0}
    for item in inbox.values():
        log = {s for s, _ in (item.get("track") or {}).get("log", [])} | {(item.get("track") or {}).get("stage")}
        log.discard(None)
        if log:
            reached["applied"] += 1
        if log & {"interview", "offer"}:
            reached["interview"] += 1
        if "offer" in log:
            reached["offer"] += 1
    # «вакансий» = the pool still to go through: what fits the settings and has not been decided on (not starred, hidden,
    # archived or responded to). Every decision except the AI score takes a vacancy out of it.
    if not jobs:  # the caller counts the pool itself (it already has it)
        return {"jobs": 0, **reached}
    shown = [(k, v) for k, v in inbox.items() if (fits is None or fits(v)) and v.get("status", "new") == "new"
             and not v.get("archived") and not v.get("fav") and not (v.get("track") or {}).get("stage")]
    return {"jobs": len(groups(shown)), **reached}


# ---------------------------------------------------------------- 📎 the résumé file as it was uploaded

ORIGINAL_EXT = (".pdf", ".docx", ".doc", ".rtf", ".txt", ".md", ".zip")


def original_paths(member):
    import guests
    folder = guests.resume_dir(member)
    return folder, folder / "original.txt"


def save_original(member, filename, data, text):
    """Keep the last uploaded résumé file byte for byte (to send back or read), plus its text."""
    folder, txt = original_paths(member)
    folder.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(filename or "")[1].lower()
    ext = ext if ext in ORIGINAL_EXT else ".bin"
    for old in folder.glob("original.*"):
        old.unlink()
    (folder / ("original" + ext)).write_bytes(data)
    txt.write_text(text or "", encoding="utf-8")
    member.update_state(cv_original={"file": os.path.basename(filename or "резюме")[:120], "ext": ext,
                                     "at": now().isoformat(timespec="seconds"), "size": len(data)})


def original_info(member):
    info = member.load_state().get("cv_original")
    folder, txt = original_paths(member)
    if not info or not (folder / ("original" + info["ext"])).exists():
        return None, None, ""
    return info, folder / ("original" + info["ext"]), txt.read_text(encoding="utf-8") if txt.exists() else ""
