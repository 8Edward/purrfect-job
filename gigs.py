"""🧩 Подработки (owner only): gigs and side jobs with a low barrier — freelance tasks, short projects, part-time.

Rules, no AI. A post or vacancy is a gig when its words say so (фриланс, разовая задача, part-time, проект на
2 недели, оплата за задачу…). Then `assess` gives it an «ease» score: + low barrier (без опыта, junior, простая
задача, на пару дней), + the owner's skills (Excel, SQL, Python, дашборды…), + fresh; − senior / «опыт от 3 лет»,
big «под ключ» projects, office work. Scam patterns (пассивный доход, +3000 в день, подписки, накрутки) drop it.

Sources: the pool channels and the owner's feed (already read by the bot), titles in the site feeds, and a few free
feeds read here every few hours: hh.ru (part-time / project, remote), FL.ru and Freelancer.com RSS, Remotive API.
"""

import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import guests
import tg_collect as tc

log = logging.getLogger("job-radar.gigs")

PATH = tc.ROOT / "data" / "tg-gigs.json"
DAYS = 7                 # how old a gig may be
KEEP_MAX = 400           # market gigs kept on disk
REFRESH = timedelta(hours=3)
REMOTIVE_EVERY = timedelta(hours=6)  # Remotive asks for a few calls a day at most
UA = {"User-Agent": "Mozilla/5.0 (JobRadar personal job alerts)"}

EASY, MAYBE = 60, 40     # ease ≥ EASY: 🟢 «легко зайти»; ≥ MAYBE: 🟡 «можно попробовать»; lower is not shown


def rx(*parts):
    return re.compile("|".join(parts), re.IGNORECASE)


GIG = rx(r"фриланс", r"freelanc", r"подработ", r"\bразов\w*", r"частичн\w* занят", r"неполн\w* (?:день|занят)",
         r"part[- ]?time", r"проектн\w* (?:работ|занят)", r"небольш\w* (?:задач|проект)", r"задач[ау] на\b",
         r"(?:ищу|ищем|нужен|нужна|требуется) (?:исполнител|фрилансер|помощ)", r"\bgig\b", r"one[- ]?(?:time|off)",
         r"short[- ]term", r"сдельн", r"оплата за (?:задач|проект|час|ролик|стать|штук|единиц)", r"бюджет[:\s]+(?:до\s)?\d",
         r"почасов", r"hourly", r"per hour", r"\d+\s?(?:-\s?\d+\s?)?(?:ч|час\w*)\s*(?:в|/)\s*нед", r"\d+\s?(?:-\s?\d+\s?)?h(?:ours)?\s*(?:/|per|a)\s*week",
         r"на (?:\d+|одн\w*|две|два|пару|несколько) (?:дн|недел|месяц)", r"\bcontract(?:or)?\b", r"контракт")
WEAK = rx(r"\bcontract(?:or)?\b", r"контракт")  # alone it is mostly a full-time B2B contract: no bonus for it
LOW = [  # a low barrier: +18 each (at most 2 count)
    rx(r"без опыта", r"опыт не (?:нужен|требуется|обязател)", r"no experience", r"entry[- ]level", r"beginner", r"начинающ", r"новичк"),
    rx(r"обучим", r"обучение (?:с нуля|за счёт|бесплатн)", r"\bjunior\b", r"джун", r"стаж[её]р", r"\bintern(?:ship)?\b"),
    rx(r"прост\w+ (?:задач|работ)", r"несложн", r"небольш\w* (?:задач|проект)", r"small (?:task|project|job)", r"quick (?:task|job|fix)",
       r"\beasy\b", r"займ[её]т (?:\d+|пару|несколько) (?:минут|час)", r"на (?:1|2|3|один|два|пару|несколько) (?:дн|час)", r"\bразов"),
]
LOW_LABELS = ["без опыта", "для новичков", "небольшая задача"]
PART = rx(r"part", r"частичн", r"неполн", r"подработ", r"час", r"h(?:ours)?\b")
SKILLS = [  # what the owner does (cv.md) -> chip name; only a matcher, never a claim about him
    ("Excel", rx(r"\bexcel\b", r"эксел", r"xlsx")), ("Google Sheets", rx(r"google sheets", r"гугл[- ]?таблиц", r"google[- ]?таблиц")),
    ("SQL", rx(r"\bsql\b", r"postgres", r"clickhouse", r"mysql")), ("Python", rx(r"\bpython\b", r"пайтон", r"pandas")),
    ("Дашборды", rx(r"дашборд", r"dashboard", r"tableau", r"power ?bi", r"datalens", r"looker", r"qlik", r"визуализ", r"visuali[sz]")),
    ("Парсинг", rx(r"парс", r"scrap", r"crawl", r"сбор данных", r"data collection")),
    ("Данные", rx(r"анализ данных", r"data analy", r"аналитик", r"analyst", r"отч[её]т", r"report", r"data entry", r"ввод данных",
                  r"внесени\w* данных", r"очист\w* данных", r"data clean", r"\betl\b", r"таблиц", r"spreadsheet", r"статистик")),
    ("Автоматизация", rx(r"автоматизац", r"automat", r"телеграм[- ]?бот", r"telegram[- ]?bot", r"\bбот[ауе]?\b", r"\bbot\b", r"\bскрипт", r"\bscript")),
]
HARD = [  # a high barrier: −25 each
    rx(r"\bsenior\b", r"сеньор", r"синьор", r"\blead\b", r"\bлид\b", r"тимлид", r"head of", r"руководител", r"ведущ\w+ (?:специалист|аналитик|разработ)",
       r"principal", r"architect", r"архитектор"),
    rx(r"опыт\w* (?:работы )?(?:\w+ )?(?:от|более|не менее|свыше) (?:[3-9]|тр[её]х|пяти|четыр)", r"\b(?:[3-9]|1\d)\+?\s?(?:years|yrs|лет|года)\b"),
    rx(r"\bnative\b", r"носител\w* язык", r"fluent (?:german|french|spanish|chinese|japanese)"),
]
BIG = rx(r"под ключ", r"с нуля (?:разработ|созда)", r"платформ", r"маркетплейс", r"marketplace", r"мобильн\w* приложени", r"mobile app",
         r"full[- ]?stack", r"\bsaas\b", r"\bmvp\b", r"интернет[- ]магазин")
ONSITE = rx(r"\bв офис", r"\bофис\w*\b", r"on-?site", r"курьер", r"склад", r"грузчик", r"промоутер", r"водител", r"in[- ]person", r"site visit", r"на объект")
REMOTE = rx(r"удал[её]н", r"remote", r"онлайн", r"online", r"из любой точки", r"anywhere", r"из дома")
SCAM = rx(r"пассивн\w* доход", r"без вложений", r"заработ\w* (?:в интернете|онлайн|с телефона|на телефоне|дома)",
          r"(?:доход|заработ\w*|получа\w*) (?:от )?\+?\d[\d\s.,]*(?:к|k|тыс\w*)?\s*(?:₽|р\b|руб\w*|\$)?\s*(?:в|за) (?:день|сутки|час)",
          r"\+\s?\d[\d\s.,]*(?:₽|р\b|руб\w*)", r"на карт[уе]", r"пошагов\w* инструкц", r"вложени\w", r"крипт", r"арбитраж", r"\bдроп",
          r"обнал", r"ставк\w* на спорт", r"казино", r"\bp2p\b", r"сим[- ]?карт", r"оформ\w* (?:банковск\w* )?карт", r"сетев\w* маркетинг",
          r"\bmlm\b", r"подпис\w+ на (?:telegram|телеграм|канал|instagram|инстаграм|youtube|ютуб|аккаунт)", r"накрут", r"отзыв\w* (?:на|о|за)\b",
          r"\bлайк", r"предоплат", r"оплат\w* (?:обучени|курс)", r"выкуп\w* товар", r"самовыкуп", r"пиши\w* (?:\+|«|\"|слово)",
          r"в лс (?:\+|слово)", r"инвестиц", r"трейд", r"форекс", r"forex", r"captcha", r"капч")
BUDGET = re.compile(r"бюджет[:\s]+(?:до\s)?([\d\s  ]{3,})\s*(₽|руб|р\b|&#8381;)|\$\s?([\d,]{3,})", re.IGNORECASE)
LEVELS = {"easy": "🟢 легко зайти", "maybe": "🟡 можно попробовать"}


def assess(title, text, market=False, date=None, by_source=False):
    """-> {ease, level, skills, why} for a gig worth showing, or None (not a gig / scam / too hard).
    `market`: it comes from a freelance marketplace, so it is a task by definition; `by_source`: the source itself
    filtered for part-time / project / freelance (hh.ru, Remotive)."""
    title, text = str(title or ""), str(text or "")
    blob = f"{title}\n{text}"[:6000]
    kind = [m.group(0).lower() for m in GIG.finditer(blob)][:6]
    if not (market or by_source) and not kind:
        return None
    if SCAM.search(blob):
        return None
    strong = market or by_source or any(not WEAK.fullmatch(k) for k in kind)
    ease, why = (35 if market else 30) + (10 if strong else 0), []
    if market:
        why.append("задача с биржи")
    elif by_source:
        why.append(by_source if isinstance(by_source, str) else "неполная занятость")
    elif strong:
        why.append("неполная занятость" if PART.search(" ".join(kind)) else "задача / фриланс")
    low = [label for p, label in zip(LOW, LOW_LABELS) if p.search(blob)]
    ease += 18 * min(2, len(low))
    why += low[:2]
    skills = [name for name, p in SKILLS if p.search(blob)]
    ease += min(30, 12 * len(skills))
    hard = [p.search(blob).group(0).lower() for p in HARD if p.search(blob)]
    ease -= 25 * len(hard)
    if BIG.search(blob):
        ease -= 20
        hard.append(BIG.search(blob).group(0).lower())
    budget = budget_value(blob)
    if budget and budget > 3000:
        ease -= 15
        hard.append("большой бюджет")
    if ONSITE.search(blob) and not REMOTE.search(blob):
        ease -= 30
        hard.append("офис / на месте")
    if date:
        age = datetime.now(timezone.utc) - date
        ease += 10 if age <= timedelta(days=1) else 5 if age <= timedelta(days=3) else 0
    level = "easy" if ease >= EASY else "maybe" if ease >= MAYBE else None
    if not level:
        return None
    return {"ease": max(0, min(100, ease)), "level": level, "label": LEVELS[level], "skills": skills,
            "why": why[:3], "minus": hard[:2]}


def budget_value(blob):
    """A budget named in the text, in dollars (rough), or 0."""
    m = BUDGET.search(blob)
    if not m:
        return 0
    try:
        if m.group(3):
            return int(m.group(3).replace(",", ""))
        return int(re.sub(r"\D", "", m.group(1))) / 90
    except ValueError:
        return 0


# ---------------------------------------------------------------- free feeds read here

def _get(url, timeout=25, size=3_000_000, headers=None):
    with urlopen(Request(url, headers=headers or UA), timeout=timeout) as resp:
        return resp.read(size)


def _clean(text, limit=4000):
    text = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(str(text or ""))))
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()[:limit]


def _date(value):
    try:
        d = parsedate_to_datetime(value)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return guests.parse_posted(value)


def _rss(url, site):
    root = ET.fromstring(_get(url))
    out = []
    for it in root.findall(".//item"):
        link = (it.findtext("link") or "").strip()
        if not link.startswith("https://"):
            continue
        title = _clean(it.findtext("title"), 200).split("\n")[0].strip()[:120]
        comp = ""
        m = re.search(r"\((?:Бюджет|Budget):\s*([^)]+)\)\s*$", title)
        if m:
            comp, title = m.group(1).strip(), title[:m.start()].strip()
        m = not comp and re.search(r"\(Budget:\s*([^,)]+)", it.findtext("description") or "")  # Freelancer.com: «(Budget: ₹12500 - ₹37500 INR, Jobs: …»
        if m:
            comp = _clean(m.group(1), 60)
        out.append({"title": title or "Задача", "url": link, "company": "", "location": "Удалённо", "comp": comp,
                    "posted": _date(it.findtext("pubDate")).isoformat(), "text": _clean(it.findtext("description")),
                    "site": site, "market": True, "category": _clean(it.findtext("category"), 120)})
    return out


def fetch_fl():
    return _rss("https://www.fl.ru/rss/all.xml", "FL.ru")


FREELANCER_SKILLS = ["Excel", "Data-Entry", "SQL", "Python", "Data-Processing", "Web-Scraping", "Power-BI", "Tableau",
                     "Google-Sheets", "Data-Analysis", "Statistics"]


def fetch_freelancer():
    jobs, seen, errors = [], set(), []
    for i, skill in enumerate(FREELANCER_SKILLS):
        if i:
            time.sleep(0.5)
        try:
            got = _rss(f"https://www.freelancer.com/rss/job_{skill}.xml", "Freelancer.com")
        except Exception as e:
            log.info("freelancer %s: %s", skill, e)
            errors.append(f"{skill}: {e}")
            continue
        for j in got:
            if j["url"] not in seen:
                seen.add(j["url"])
                jobs.append(j)
    if not jobs and errors:
        raise ValueError("; ".join(errors[:3]))
    return jobs


def fetch_remotive():
    data = json.loads(_get("https://remotive.com/api/remote-jobs?limit=400", timeout=40, size=20_000_000))
    out = []
    for j in data.get("jobs") or []:
        if j.get("job_type") not in ("freelance", "part_time", "contract"):
            continue
        out.append({"title": _clean(j.get("title"), 200), "url": str(j.get("url") or ""), "company": _clean(j.get("company_name"), 120),
                    "location": _clean(j.get("candidate_required_location"), 120) or "Worldwide", "comp": _clean(j.get("salary"), 80),
                    "posted": str(j.get("publication_date") or ""), "text": _clean(j.get("description")),
                    "site": "Remotive", "kind": {"freelance": "freelance", "part_time": "part-time", "contract": "contract"}[j["job_type"]]})
    return [j for j in out if j["url"].startswith("https://")]


HH_QUERY = "аналитик OR analyst OR excel OR sql OR python OR дашборд OR отчет OR парсинг OR данные"


def fetch_hh():
    """hh.ru: part-time and project work, remote, titles with the owner's words (2 pages of 100)."""
    jobs = []
    for page in range(2):
        if page:
            time.sleep(0.7)
        params = {"text": HH_QUERY, "search_field": "name", "order_by": "publication_time", "items_on_page": 100, "page": page,
                  "employment_form": ["PART", "PROJECT"], "work_format": "REMOTE"}
        raw = _get(guests.HH_SEARCH + "?" + urlencode(params, doseq=True), timeout=30, size=8_000_000, headers=guests.HH_BROWSER)
        got = guests.parse_hh_search(raw.decode("utf-8", "replace"))
        if got is None:  # captcha or a new page layout: say so instead of a quiet zero
            if jobs:
                break
            raise ValueError("hh.ru page has no HH-Lux-InitialState (captcha / layout change)")
        jobs += [dict(j, site="hh.ru", kind="частичная / проектная") for j in got]
        if len(got) < 100:
            break
    return jobs


def fetch_freelancer_fast():
    """Freelancer.com: the public API (bids, age, full text) — the skill RSS feeds only if the API fails."""
    import freelancer  # here, not on top: freelancer.py imports this module
    try:
        return freelancer.fetch()
    except Exception as e:
        log.warning("freelancer API: %s, using RSS", e)
        return fetch_freelancer()


SOURCES = {"hh.ru": fetch_hh, "FL.ru": fetch_fl, "Freelancer.com": fetch_freelancer_fast, "Remotive": fetch_remotive}
EVERY = {"Freelancer.com": timedelta(minutes=15), "FL.ru": timedelta(hours=1), "hh.ru": REFRESH, "Remotive": REMOTIVE_EVERY}
FAST = timedelta(minutes=15)  # how often anything can be due (the quickest source)


def load():
    try:
        return guests.read_json(PATH, {})
    except (OSError, ValueError):
        return {}


def source_due(src, name, now_=None):
    try:
        return (now_ or datetime.now(timezone.utc)) - datetime.fromisoformat((src.get(name) or {}).get("at", "")) >= EVERY.get(name, REFRESH)
    except (ValueError, TypeError):
        return True


def due(data=None):
    """Some source wants reading again (Freelancer every 15 min, FL.ru hourly, hh.ru every 3 h, Remotive every 6 h)."""
    data = load() if data is None else data
    src = data.get("src") or {}
    return any(source_due(src, name) for name in SOURCES)


def refresh(force=False):
    """Read the free feeds (blocking; run in a thread) and keep the last DAYS days. -> the saved data."""
    data = load()
    old = {j["url"]: j for j in data.get("jobs") or []}
    src = data.get("src") or {}
    now_ = datetime.now(timezone.utc)
    for name, fetch in SOURCES.items():
        if not source_due(src, name, now_) and (not force or name == "Remotive"):  # Remotive asks for a few calls a day
            continue
        try:
            got = fetch()
            if name == "Freelancer.com" and any(j.get("fl") for j in got):  # the API answered: its orders replace the RSS copies
                old = {u: j for u, j in old.items() if j.get("site") != "Freelancer.com"}
            for j in got:
                old[j["url"]] = j  # a fresh copy wins
            src[name] = {"at": now_.isoformat(), "n": len(got)}
        except Exception as e:
            log.warning("gigs %s: %s", name, e)
            src[name] = {**src.get(name, {}), "err": str(e)[:200], "err_at": now_.isoformat()}
    cutoff = now_ - timedelta(days=DAYS)
    jobs = sorted((j for j in old.values() if guests.parse_posted(j.get("posted")) >= cutoff),
                  key=lambda j: j.get("posted") or "", reverse=True)[:KEEP_MAX]
    data = {"at": now_.isoformat(), "src": src, "jobs": jobs}
    guests.write_json(PATH, data)
    return data


def market_jobs(data=None):
    """The saved feed gigs as stored jobs (date as datetime, text kept for the card)."""
    data = load() if data is None else data
    out = []
    for j in data.get("jobs") or []:
        j = dict(j, title=str(j.get("title") or "").split("\n")[0].strip()[:120] or "Задача")
        job = guests.web_job(j, j.get("site") or "")
        job.update(text=j.get("text") or "", gig_src=j.get("site"), market=bool(j.get("market")))
        if j.get("fl"):
            job["fl"] = j["fl"]
        if j.get("kind"):
            job["text"] = f"{j['kind']}\n{job['text']}"
            job["gig_kind"] = j["kind"]
        if j.get("category"):
            job["text"] += f"\n\nКатегория: {j['category']}"
        out.append(job)
    return out


# ---------------------------------------------------------------- the list

def _twin(job):
    return (re.sub(r"\W+", " ", str(job.get("title") or "").lower()).strip()[:80],
            re.sub(r"\W+", " ", str(job.get("company") or job.get("chat") or "").lower()).strip())


def collect(posts, inbox, feed, market, skip=()):
    """Gigs worth showing, best first: [(job, assessment)]. `posts`: channel posts (date as datetime); `inbox`: the
    owner's stored vacancies {key: item}; `feed`: site-feed jobs (titles only); `market`: market_jobs(); `skip`: keys
    put away (hidden / archived). Jobs come back with `key` and a datetime `date`, ready for guests.to_item."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    out, keys, twins = [], set(skip), set()

    def add(job, a):
        if not a or job["key"] in keys or job["date"] < cutoff:
            return
        twin = _twin(job)
        if twin in twins and twin[0]:
            return
        keys.add(job["key"])
        twins.add(twin)
        out.append((job, a))

    for job in market:
        add(job, assess(job["title"], job.get("text"), market=job.get("market"), date=job["date"], by_source=job.get("gig_kind") or False))
    for key, item in inbox.items():
        if item.get("status") == "hidden" or item.get("archived"):
            keys.add(key)
            continue
        job = dict(item, key=key, date=guests.parse_posted(item.get("date")))
        add(job, assess(job.get("title"), item.get("text") or "", date=job["date"]))
    for p in posts:
        if tc.looks_like_article(p.get("text") or ""):
            continue
        add(p, assess(p.get("title"), p.get("text"), date=p["date"]))
    for job in feed:
        add(job, assess(job.get("title"), "", date=job["date"]))
    out.sort(key=lambda ja: (ja[1]["level"] != "easy", -ja[1]["ease"], -ja[0]["date"].timestamp()))
    return out


# ---------------------------------------------------------------- 💵 pay in dollars

USD_RATES = [  # rough rates, only to show «≈ $» and sort; longer codes first so «A$» is not read as «$»
    (r"a\$|\baud\b", 0.65), (r"c\$|\bcad\b", 0.73), (r"nz\$|\bnzd\b", 0.6), (r"s\$|\bsgd\b", 0.75), (r"hk\$|\bhkd\b", 0.13),
    (r"₹|\binr\b|\brs\.?\s?\d|рупи", 1 / 84), (r"₽|руб|\brub\b|\bр\.|\d\s?р\b|&#8381;", 1 / 90), (r"€|\beur\b|евро", 1.1), (r"£|\bgbp\b", 1.27),
    (r"₸|\bkzt\b|тенге", 1 / 480), (r"₴|\buah\b|грн", 1 / 41), (r"\bbyn\b|бел\w* руб", 0.3), (r"zł|\bpln\b", 0.25),
    (r"\bso'?m\b|\bсум\b|\buzs\b", 1 / 12700), (r"₫|\bvnd\b", 1 / 25000), (r"₱|\bphp\b", 1 / 57), (r"\bidr\b|\brp\.?\s?\d", 1 / 16000), (r"₺|\btry\b", 1 / 34),
    (r"r\$|\bbrl\b", 0.18), (r"\bmxn\b", 0.055), (r"\bzar\b", 0.055), (r"¥|\bjpy\b", 1 / 150), (r"\bcny\b|\brmb\b|юан", 0.14), (r"\bpkr\b", 1 / 280),
    (r"₦|\bngn\b", 1 / 1500), (r"\bbdt\b", 1 / 120), (r"\bchf\b", 1.15), (r"\baed\b", 0.27), (r"\$|usd|долл", 1.0),
]
USD_RATES = [(re.compile(p, re.IGNORECASE), r) for p, r in USD_RATES]
NUM = re.compile(r"(\d[\d\s  ,.]*\d|\d)\s*(k|к|тыс\w*\.?|млн)?(?![\w])", re.IGNORECASE)


def _num(raw, mult):
    s = re.sub(r"[\s  ]", "", raw)
    if re.search(r"[.,]\d{3}(?!\d)", s):  # 1,250 / 12.500: thousands
        s = re.sub(r"[.,](?=\d{3}(?!\d))", "", s)
    s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    m = (mult or "").lower()
    return v * (1e6 if m.startswith("млн") else 1000 if m else 1)


def pay_usd(pay, cyrillic=False, market=False):
    """«₹12500 - ₹37500 INR» / «от 60К» / «$15/hour» -> {lo, hi, per, label} in dollars, or None.
    No currency named: rubles in a Russian text, else dollars. per: «/час», «/мес» or «за проект» (a task budget)."""
    pay = str(pay or "")
    if not re.search(r"\d", pay):
        return None
    rate = next((r for p, r in USD_RATES if p.search(pay)), None)
    if rate is None:
        if re.search(r"\b[A-Z]{3}\b", pay):  # a currency we have no rate for: better no number than a wrong one
            return None
        rate = 1 / 90 if cyrillic else 1.0
    nums = [v for v in (_num(m.group(1), m.group(2)) for m in NUM.finditer(pay)) if v and v >= 5]
    if not nums:
        return None
    low = pay.lower()
    if re.search(r"hour|/\s?hr|\bhr\b|час|/\s?h\b", low):
        per, k = "/час", 1
    elif re.search(r"year|год|annum|/\s?yr", low):
        per, k = "/мес", 1 / 12
    elif re.search(r"month|мес", low):
        per, k = "/мес", 1
    elif market:
        per, k = " за проект", 1
    else:
        per, k = "", 1
    lo, hi = min(nums[:2]) * rate * k, max(nums[:2]) * rate * k
    if hi < 1:
        return None

    def fmt(v):
        v = round(v, -1) if v >= 100 else round(v)
        return f"{int(v):,}".replace(",", " ")

    return {"lo": round(lo), "hi": round(hi), "per": per.strip(),
            "label": ("$" + fmt(lo) + ("–" + fmt(hi) if fmt(hi) != fmt(lo) else "") + per) if rate != 1.0 or len(nums) > 1 or per else "$" + fmt(hi)}
