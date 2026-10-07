"""⚡ Freelancer.com fast orders for «🧩 Подработки» (owner only).

Fresh fixed-price projects from Freelancer's public API (no login: title, budget, bids, time, full description), junk
dropped, and for each one a brief: age, bids, budget in $, what to do, how long it takes with Claude, risks, a
suggested bid and delivery days. The bid text comes from `proposal_prompt` (Claude on the owner's subscription,
fact-checked against cv.md) or `template` (no AI).

Nothing here places a bid: Freelancer bans automated bidding, and every application goes only after Eduard's own
tap — the app copies the text and opens the project page, he pastes it and presses «Place Bid» himself.
The API does not show the client's «Payment verified» without a login: the card asks to check it on the page.
"""

import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode

import gigs

API = "https://www.freelancer.com/api/projects/0.1/projects/active/"
QUERIES = ["excel", "google sheets", "sql", "python script", "data cleaning", "data entry", "pdf to excel",
           "scraping", "power query", "dashboard"]
PER_QUERY = 30
MAX_AGE_H = 48            # older orders already have their freelancer
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "SGD": "S$", "HKD": "HK$"}

JUNK = gigs.rx(r"site verification", r"confirmis", r"no ocr", r"without ocr", r"purely manual", r"manual(?:ly)? only",
               r"no (?:automation|scripts?|bots?|tools?)", r"trading bot", r"\bforex\b", r"\bcrypto", r"wordpress", r"shopify",
               r"mobile app", r"\bios app", r"android app", r"website (?:development|design|build)", r"build (?:a|my) website",
               r"for my portfolio", r"student project", r"assignment", r"homework", r"thesis", r"dissertation", r"\bexam\b",
               r"captcha", r"\bsurvey takers?\b")
NOT_IN_CV = ["Power BI", "VBA", "Macro", "Power Query", "Looker", "SPSS", "Stata", "MATLAB", "Apps Script", "Selenium", "n8n",
             "Zapier", "Airtable", "Salesforce", "SAP", "QuickBooks", "Xero", "AutoCAD", "JavaScript", "React", "PHP"]
EFFORT = [  # (words in the order, hours with Claude) — first match wins
    (gigs.rx(r"formula", r"fix (?:my|the) (?:sheet|excel|spreadsheet)", r"conditional formatting", r"vlookup", r"pivot"), (1, 2)),
    (gigs.rx(r"pdf", r"ocr", r"extract", r"convert", r"data entry", r"copy", r"typing", r"transcrib"), (1, 3)),
    (gigs.rx(r"clean", r"dedup", r"duplicate", r"normali[sz]e", r"organi[sz]e", r"categori"), (1, 3)),
    (gigs.rx(r"scrap", r"crawl", r"web research", r"lead list", r"collect data"), (2, 4)),
    (gigs.rx(r"dashboard", r"report", r"visuali[sz]", r"chart"), (3, 6)),
    (gigs.rx(r"sql", r"database", r"query"), (2, 4)),
    (gigs.rx(r"python", r"script", r"automat"), (2, 5)),
]


def _get(params):
    url = API + "?" + urlencode(params, doseq=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=gigs.UA), timeout=30) as resp:
        return json.loads(resp.read(10_000_000))


def _clean(text, limit=4000):
    return gigs._clean(text, limit)


def to_job(p):
    """One API project -> the raw gig dict gigs.refresh keeps (posted = when it was put up or last bumped)."""
    cur = (p.get("currency") or {}).get("code") or "USD"
    b = p.get("budget") or {}
    sym = SYMBOL.get(cur, "")
    lo, hi = b.get("minimum"), b.get("maximum")
    money = lambda v: f"{sym}{int(v)}" if sym else f"{int(v)}"
    comp = (f"{money(lo)} - {money(hi)} {cur}" if lo and hi else f"{money(lo or hi)} {cur}" if (lo or hi) else "")
    stats = p.get("bid_stats") or {}
    seo = p.get("seo_url") or ""
    return {"title": _clean(p.get("title"), 160), "url": f"https://www.freelancer.com/projects/{seo}" if seo else
            f"https://www.freelancer.com/projects/{p.get('id')}", "company": "", "location": "Удалённо", "comp": comp,
            "posted": datetime.fromtimestamp(int(p.get("time_submitted") or time.time()), timezone.utc).isoformat(),
            "text": _clean(p.get("description") or p.get("preview_description")), "site": "Freelancer.com", "market": True,
            "category": ", ".join(j.get("name", "") for j in (p.get("jobs") or [])[:6]),
            "fl": {"id": p.get("id"), "bids": int(stats.get("bid_count") or 0), "bid_avg": stats.get("bid_avg"),
                   "cur": cur, "min": lo, "max": hi, "type": p.get("type") or "fixed"}}


def fetch():
    """Fresh fixed-price projects for the owner's topics, junk dropped, newest first."""
    jobs, seen, errors = [], set(), []
    for i, q in enumerate(QUERIES):
        if i:
            time.sleep(0.4)
        try:
            data = _get({"query": q, "limit": PER_QUERY, "full_description": "true", "job_details": "true",
                         "project_types[]": "fixed"})
        except Exception as e:
            errors.append(f"{q}: {e}")
            continue
        for p in (data.get("result") or {}).get("projects") or []:
            if p.get("id") in seen or p.get("type") != "fixed":
                continue
            seen.add(p.get("id"))
            job = to_job(p)
            if age_hours(job) > MAX_AGE_H or junk(job):
                continue
            jobs.append(job)
    if not jobs and errors:
        raise ValueError("; ".join(errors[:3]))
    return sorted(jobs, key=lambda j: j["posted"], reverse=True)


def age_hours(job):
    """How long ago the order went up (or was bumped): from `date` on a stored job, `posted` on a raw one."""
    try:
        when = job["date"] if isinstance(job.get("date"), datetime) else datetime.fromisoformat(job["posted"])
        return (datetime.now(timezone.utc) - when).total_seconds() / 3600
    except (KeyError, ValueError, TypeError):
        return 0


def usd(cur, value):
    pay = gigs.pay_usd(f"{SYMBOL.get(cur, '')}{value} {cur}", market=True) if value else None
    return pay["hi"] if pay else None


OTHER_JOB = gigs.rx(r"manager\b", r"copywrit", r"arquitect", r"architect", r"designer", r"\blogo\b", r"marketing", r"\bseo\b",
                    r"video", r"animat", r"translat", r"tutor", r"teacher", r"voice", r"recruit", r"sales", r"cold call",
                    r"\bcad\b", r"\bdesign\b", r"google ads", r"lead[- ]gen", r"\bads\b", r"(?:web|app|game|unity|wordpress|shopify|android|ios|flutter|react|full[- ]?stack) develop", r"engineer\b")
TITLE_DATA = gigs.rx(r"\bdata\b", r"excel", r"spreadsheet", r"\bsheets?\b", r"\bpdf\b", r"\bcsv\b", r"database", r"\bsql\b",
                     r"python", r"scrap", r"dashboard", r"\breport", r"data entry", r"typing", r"retype", r"extract", r"convert",
                     r"clean", r"\blist\b", r"\bleads?\b", r"table", r"invoice", r"inventory")
NOT_REMOTE = gigs.rx(r"must be (?:in|located|based)", r"\bon[- ]?site\b", r"in[- ]person", r"full[- ]time", r"trading",
                     r"\brender", r"customer support", r"dukungan", r"follow[- ]up calls", r"\bvetting\b", r"\bhack", r"jailbreak",
                     r"\bbms\b", r"accountant", r"bookkeep")
DATA_SKILLS = ("Excel", "Google Sheets", "SQL", "Python", "Дашборды", "Парсинг", "Данные")


def relevant(job):
    """About data work: one of the owner's data skills in the title or in Freelancer's own skill tags (the API's search
    also matches words deep in the text, which drags in copywriters and project managers)."""
    title = job["title"]
    if OTHER_JOB.search(title) or NOT_REMOTE.search(f"{title}\n{job.get('text') or ''}"):
        return False
    return bool(TITLE_DATA.search(title)) or any(name in DATA_SKILLS and p.search(title) for name, p in gigs.SKILLS)


def junk(job):
    blob = f"{job['title']}\n{job.get('text') or ''}"
    if JUNK.search(blob) or not relevant(job):
        return True
    fl = job.get("fl") or {}
    top = usd(fl.get("cur"), fl.get("max") or fl.get("min"))
    return top is not None and top < 50


# ---------------------------------------------------------------- the brief on a card

def nice(v, cur):
    """A round bid: ₹13 750 -> 14 000, $27.5 -> 30."""
    step = 500 if cur in ("INR",) and v >= 5000 else 100 if v >= 1000 else 10 if v >= 100 else 5
    return int(-(-v // step) * step)


BIG_JOB = gigs.rx(r"\bapi\b", r"integrat", r"plugin", r"platform", r"\bsystem\b", r"\d+ (?:third-party )?websites",
                  r"web app", r"\bsaas\b", r"machine learning", r"end-to-end")


def brief(job, cv_text=""):
    """Everything Eduard looks at before bidding, from the order itself (no AI)."""
    fl = job.get("fl") or {}
    blob = f"{job.get('title') or ''}\n{job.get('text') or ''}"
    cur, lo, hi = fl.get("cur") or "USD", fl.get("min"), fl.get("max")
    hours = next((h for p, h in EFFORT if p.search(blob)), (2, 4))
    if BIG_JOB.search(blob):  # a system, not a task: a day or two of work even with Claude
        hours = (8, 16)
    bid = None
    if lo:
        bid = min(nice(lo * 1.1, cur), int(hi)) if hi else nice(lo, cur)
    days = 2 if hours[1] <= 3 else 3 if hours[1] <= 5 else 5
    risks = []
    bids = int(fl.get("bids") or 0)
    if bids >= 40:
        risks.append(f"много конкурентов: {bids} заявок")
    cv_low = (cv_text or "").lower()
    missing = [t.strip() for t in NOT_IN_CV if re.search(r"(?<![\w])" + re.escape(t.strip()) + r"(?![\w])", blob, re.I)
               and t.strip().lower() not in cv_low]
    if missing:
        risks.append("нет в резюме: " + ", ".join(missing[:3]))
    if re.search(r"100\s?% accura|zero errors|error[- ]free", blob, re.I):
        risks.append("требуют 100% точности")
    if re.search(r"urgent|asap|within (?:24|12|6) hours|today", blob, re.I):
        risks.append("срочно")
    if not re.search(r"sample|attached|example|template|file", blob, re.I):
        risks.append("объём не ясен — спросить в заявке")
    risks.append("Payment verified у клиента API не показывает — проверьте на странице")
    todo = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", job.get("text") or ""))
    return {"age_h": round(age_hours(job), 1), "bids": bids,
            "bid_avg": round(fl["bid_avg"]) if fl.get("bid_avg") else None,
            "bid_avg_usd": usd(cur, fl.get("bid_avg")) if fl.get("bid_avg") else None,
            "todo": " ".join(todo[:2])[:300], "hours": list(hours), "bid": bid, "cur": cur, "bid_usd": usd(cur, bid) if bid else None,
            "days": days, "risks": risks, "fee_note": "сайт берёт 10% с суммы"}


# ---------------------------------------------------------------- the bid text

PROPOSAL_SYSTEM = """You write short bids for Freelancer.com projects for one freelancer.
Use ONLY facts from his CV below: never invent experience, numbers, clients, tools or certificates.
No contact details, links, emails, phone numbers or messengers (Freelancer bans them before hiring).
English, plain text, 90-150 words: one opening line that shows you understood the task; a plan of 3-4 numbered steps
tailored to this exact order; one line of relevant experience taken from the CV; one short question to the client
about the files or volume. No greetings beyond «Hi!», no sign-off, no emojis.
Answer with JSON only: {"text": "..."}"""


def proposal_prompt(job, cv_text):
    return (f"CV:\n{cv_text[:6000]}\n\nPROJECT: {job.get('title')}\nBUDGET: {job.get('comp')}\nSKILLS: {job.get('category') or ''}\n"
            f"DESCRIPTION:\n{(job.get('text') or '')[:3500]}")


PLANS = [
    (gigs.rx(r"pdf", r"ocr", r"extract", r"scan", r"retyp", r"typing", r"transcri"), [
        "Extract the data from all files with a Python script, keeping the structure you need.",
        "If some pages are scans, use OCR and check those fields against the source by hand.",
        "Run checks for empty fields, broken characters and duplicates, and fix every flagged item.",
        "Deliver the final Excel file in your format with a short note on anything unclear."]),
    (gigs.rx(r"clean", r"dedup", r"duplicate", r"organi[sz]e", r"categori", r"normali[sz]e"), [
        "Review your file and agree on the target structure with you.",
        "Clean it: trim spaces, fix formats and spelling, remove duplicates.",
        "Apply clear rules for categories so the result is consistent and easy to filter.",
        "Deliver the clean file plus a list of what changed and what needs your decision."]),
    (gigs.rx(r"scrap", r"crawl", r"research", r"collect"), [
        "Agree on the fields and the list of sources with you.",
        "Collect the data with a Python script where the sites allow it, by hand where they don't.",
        "Check the result for duplicates, empty fields and wrong formats.",
        "Deliver an Excel/CSV file ready to filter."]),
    (gigs.rx(r"dashboard", r"report", r"chart", r"visuali[sz]"), [
        "Agree on the key metrics and questions the report must answer.",
        "Prepare and check the data (SQL/Python or Excel).",
        "Build the report with clear charts and filters.",
        "Walk you through it and adjust after your feedback."]),
]
DEFAULT_PLAN = ["Review your files and confirm the expected result with you.",
                "Do the work with Excel and Python, so it is fast and consistent.",
                "Check the result for errors, gaps and duplicates before delivery.",
                "Deliver the file and a short note on anything that needs your decision."]


def experience_line(cv_text):
    """One true sentence about him, taken from the CV's own summary."""
    m = re.search(r"##\s*Summary\s*\n+(.+)", cv_text or "")
    if not m:
        return ""
    first = re.split(r"(?<=\.)\s+", m.group(1).strip())[0]
    return first if len(first) < 220 else ""


def template(job, cv_text):
    """A bid text without AI: a plan for the kind of task + the CV's own first summary sentence."""
    blob = f"{job.get('title') or ''}\n{job.get('text') or ''}"
    plan = next((p for rx_, p in PLANS if rx_.search(blob)), DEFAULT_PLAN)
    exp = experience_line(cv_text)
    lines = [f"Hi! I can do «{job.get('title')}» quickly and carefully.", "", "My plan:"]
    lines += [f"{n}. {step}" for n, step in enumerate(plan, 1)]
    if exp:
        lines += ["", exp.replace("Data Analyst with", "I'm a Data Analyst with")]
    lines += ["", "Could you share a sample of the files and roughly how much data there is?"]
    return "\n".join(lines)
