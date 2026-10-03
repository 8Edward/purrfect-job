"""Feed helpers for the mini app: salary range, time zone filter, merged duplicates,
the application tracker, the morning digest, AI scoring of a vacancy and the AI career plan.

Rules, no AI, except `score_prompt`/`plan_prompt` (run through resume_ai.Claude on the owner's
subscription, with their own daily caps).
"""

import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone

import resume
import resume_ai
import tg_collect as tc

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


# ---------------------------------------------------------------- 🔗 one vacancy from several sources

def _norm(s):
    return re.sub(r"[^\w]+", " ", str(s or "").lower().replace("ё", "е")).strip()


def twin_keys(item):
    """Things that make two inbox items the same vacancy: same title + company, same link,
    same post text (a repost into another channel)."""
    keys = []
    title, company = _norm(item.get("title")), _norm(item.get("company"))
    if title and company:
        keys.append("tc:" + title + "|" + company)
    if item.get("link"):
        keys.append("l:" + item["link"].split("?")[0].rstrip("/").lower())
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

    owner = {}
    for key, item in pairs:
        find(key)
        for t in twin_keys(item):
            if t in owner:
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

GOALS = {"grade": "поднять грейд в той же роли", "role": "сменить роль", "field": "сменить направление / отрасль"}

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
