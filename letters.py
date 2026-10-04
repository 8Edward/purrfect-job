"""A response letter built from templates, no AI, no tokens.

The letter is put together from: the person's résumé (name, the «о себе» paragraph, skills), the vacancy (title,
company, which of the person's skills it names) and a tone (phrase banks, Russian and English). Every fact comes
from the résumé: the only things written here are greetings, linking phrases and the closing. «Presets» are the
«о себе» paragraph to use: taken from the résumé (short / with skills) or typed by the person.
Nothing is sent from here; the app shows the text and the person decides.
"""

import re

import resume

SUMMARY_HEADS = re.compile(r"(summary|about|profile|objective|о себе|обо мне|профиль|кратко|цель)", re.I)
SKILL_HEADS = re.compile(r"(skills|tools|tech|stack|навык|стек|инструмент|технолог)", re.I)
LANG_LABEL = re.compile(r"(язык|language)", re.I)
CONTACTS = re.compile(r"@\S+\.\S+|\+?\d[\d ()-]{8,}\d|t\.me/|linkedin\.com", re.I)
ABOUT_MAX = 480

# tone -> language -> {hello, open, skills_match, skills_all, close, bye}; {title} {company} {skills} {name} filled in
TONES = {
    "neutral": {
        "name": {"ru": "Деловой", "en": "Professional"},
        "ru": {"hello": "Здравствуйте{team}!", "open": "Меня заинтересовала вакансия «{title}»{at}. Коротко о себе:",
               "skills_match": "Из того, что названо в вакансии, у меня есть: {skills}.", "skills_all": "Мои основные инструменты: {skills}.",
               "close": "Буду рад обсудить, чем могу быть полезен. Резюме прикладываю.", "bye": "С уважением,"},
        "en": {"hello": "Hello{team},", "open": "I am writing about the {title} position{at}. A few words about me:",
               "skills_match": "From your requirements, I work with: {skills}.", "skills_all": "My main tools: {skills}.",
               "close": "I would be glad to discuss how I can help. My résumé is attached.", "bye": "Best regards,"},
    },
    "short": {
        "name": {"ru": "Коротко", "en": "Short"},
        "ru": {"hello": "Здравствуйте!", "open": "Отклик на вакансию «{title}»{at}.",
               "skills_match": "Подходит по: {skills}.", "skills_all": "Инструменты: {skills}.",
               "close": "Резюме во вложении.", "bye": "Спасибо,"},
        "en": {"hello": "Hello,", "open": "Applying for {title}{at}.",
               "skills_match": "Matches: {skills}.", "skills_all": "Tools: {skills}.",
               "close": "Résumé attached.", "bye": "Thanks,"},
    },
    "warm": {
        "name": {"ru": "Тёплый", "en": "Warm"},
        "ru": {"hello": "Добрый день{team}!", "open": "Увидел вакансию «{title}»{at} и сразу захотелось откликнуться. Немного о себе:",
               "skills_match": "В вашем описании узнал знакомое: {skills}.", "skills_all": "Работаю с: {skills}.",
               "close": "Буду рад познакомиться и рассказать подробнее. Резюме прикладываю.", "bye": "Хорошего дня,"},
        "en": {"hello": "Hi{team},", "open": "I came across the {title} role{at} and wanted to reach out right away. A little about me:",
               "skills_match": "I recognise a lot in your description: {skills}.", "skills_all": "I work with: {skills}.",
               "close": "I would love to talk and tell you more. My résumé is attached.", "bye": "Have a great day,"},
    },
    "bold": {
        "name": {"ru": "Смелый (экзотика)", "en": "Bold (exotic)"},
        "ru": {"hello": "Привет{team}!", "open": "Пишу по вакансии «{title}»{at}: думаю, мы друг другу подойдём. Почему:",
               "skills_match": "Что совпадает с вашим списком: {skills}.", "skills_all": "Чем занимаюсь каждый день: {skills}.",
               "close": "Давайте созвонимся на 15 минут и проверим. Резюме внутри.", "bye": "До связи,"},
        "en": {"hello": "Hi{team},", "open": "I am reaching out about {title}{at}: I think we would work well together. Why:",
               "skills_match": "What matches your list: {skills}.", "skills_all": "What I do every day: {skills}.",
               "close": "Let us have a 15-minute call and find out. My résumé is attached.", "bye": "Talk soon,"},
    },
    "playful": {
        "name": {"ru": "С улыбкой (экзотика)", "en": "Playful (exotic)"},
        "ru": {"hello": "Здравствуйте{team}!", "open": "Если «{title}»{at} ещё ищет человека, то, кажется, это я. Вот коротко:",
               "skills_match": "Ваш список и мой набор пересеклись в: {skills}.", "skills_all": "Мой набор: {skills}.",
               "close": "Если интересно, расскажу подробнее (и без скучных фраз). Резюме во вложении.", "bye": "С теплом,"},
        "en": {"hello": "Hello{team},", "open": "If {title}{at} is still looking for someone, that might be me. In short:",
               "skills_match": "Your list and my toolkit overlap on: {skills}.", "skills_all": "My toolkit: {skills}.",
               "close": "Happy to tell you more (without the boring phrases). My résumé is attached.", "bye": "Warmly,"},
    },
}
DEFAULT_TONE = "neutral"

# «Профиль для откликов»: the person's own details for responses. (key, Russian label, English label, in the letter by default)
PROFILE_FIELDS = [
    ("fio", "ФИО", "Full name", True),
    ("location", "Где живу", "Location", False),
    ("salary", "Ожидаемая зарплата", "Expected salary", False),
    ("notice", "Когда могу выйти", "Available from", False),
    ("permit", "Право на работу", "Work authorization", False),
    ("about", "О себе", "About me", True),
    ("email", "Почта", "Email", True),
    ("phone", "Телефон", "Phone", False),
    ("telegram", "Telegram", "Telegram", True),
    ("linkedin", "LinkedIn", "LinkedIn", False),
    ("site", "Сайт, GitHub, портфолио", "Website / GitHub / portfolio", False),
]
PROFILE_KEYS = [f[0] for f in PROFILE_FIELDS]
CUSTOM_MAX, VALUE_MAX = 10, 700


def tone_list(lang="ru"):
    return [{"id": k, "name": t["name"][lang if lang in ("ru", "en") else "ru"]} for k, t in TONES.items()]


def _clean(text):
    return re.sub(r"\s+", " ", re.sub(r"[*_`]+", "", str(text or ""))).strip()


def parse_resume(md):
    """-> {name, summary, skills[]} from the résumé's own words."""
    bl = resume.blocks(md)
    name = next((_clean(b["x"]) for b in bl if b["t"] == "h1"), "")
    summary, skills, section = [], [], ""
    for b in bl:
        if b["t"] in ("h2", "h3"):
            section = b["x"]
            continue
        if b["t"] == "h1":
            continue
        if SUMMARY_HEADS.search(section) and b["t"] in ("p", "li"):
            summary.append(_clean(b["x"]))
        elif SKILL_HEADS.search(section) and b["t"] in ("p", "li"):
            line = _clean(b["x"])
            if ":" in line and len(line.split(":", 1)[0]) < 30:
                label, line = line.split(":", 1)
                if LANG_LABEL.search(label):  # «Языки: Русский (родной), Английский (B2)» is not a tool
                    continue
            skills += [s.strip(" .") for s in re.split(r"[,;·|]", line) if 1 < len(s.strip(" .")) <= 30]
    if not summary:  # no summary section: the paragraph under the name, before the first section (not the contacts line)
        for b in bl:
            if b["t"] in ("h2", "h3"):
                break
            if b["t"] == "p" and len(b["x"]) > 60 and not CONTACTS.search(b["x"]):
                summary = [_clean(b["x"])]
                break
    return {"name": name, "summary": " ".join(summary), "skills": list(dict.fromkeys(skills))}


def resume_presets(md):
    """Ready «о себе» texts out of the résumé: [{id, name, text}]. Empty when it has no summary."""
    info = parse_resume(md)
    out = []
    if info["summary"]:
        out.append({"id": "resume", "name": "Из резюме", "text": _fit(info["summary"])})
    return out


def _fit(text, limit=ABOUT_MAX):
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > limit * 0.5 else cut.rsplit(" ", 1)[0] + "…").strip()


def matched_skills(skills, vacancy_text):
    """The person's skills that the vacancy names (whole-word, case-free), in the person's order, at most 8."""
    low = (vacancy_text or "").lower()
    found = []
    for s in skills:
        if re.search(r"(?<![\w+#])" + re.escape(s.lower()) + r"(?![\w+#])", low):
            found.append(s)
    return found[:8]


def compose(md, item, vacancy_text, lang, tone=DEFAULT_TONE, about=None, profile=None):
    """The letter text. `about` = the «о себе» paragraph to use (a preset); None = the profile's, else the résumé's summary.
    `profile` (clean_profile): its name signs the letter, its ticked fields end it as a contacts block."""
    lang = lang if lang in ("ru", "en") else "ru"
    t = TONES.get(tone, TONES[DEFAULT_TONE])[lang]
    info = parse_resume(md)
    company, title = _clean(item.get("company")), _clean(item.get("title")) or ("вакансию" if lang == "ru" else "the role")
    team = (f" ({company})" if lang == "ru" else f", {company} team") if company else ""
    at = (f" в {company}" if lang == "ru" else f" at {company}") if company else ""
    fill = dict(title=title, company=company, team=team, at=at)
    profile = profile or {}
    use = profile.get("use") or []
    mine_about = pv(profile, "about", lang) if "about" in use else ""
    about = _fit(about if about is not None else (mine_about or info["summary"]))
    if "fio" in use and pv(profile, "fio", lang):
        info["name"] = pv(profile, "fio", lang)
    mine = matched_skills(info["skills"], f"{item.get('title') or ''}\n{vacancy_text}")
    if mine:
        skills_line = t["skills_match"].format(skills=", ".join(mine))
    elif info["skills"]:
        skills_line = t["skills_all"].format(skills=", ".join(info["skills"][:6]))
    else:
        skills_line = ""
    opening = t["open"].format(**fill)
    if not about:  # nothing to introduce: the opening line must not end on a colon
        opening = opening.rstrip(":").rstrip() + "."
    parts = [t["hello"].format(**fill), opening, about, skills_line, t["close"], t["bye"] + ("\n" + info["name"] if info["name"] else ""),
             "\n".join(contact_block(profile, lang))]
    return "\n\n".join(p for p in parts if p).strip()


# ---------------------------------------------------------------- the profile

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)
PHONE = re.compile(r"\+?\d[\d ()\-]{8,}\d")
TG = re.compile(r"(@[A-Za-z][A-Za-z0-9_]{4,31})|t\.me/([A-Za-z][A-Za-z0-9_]{4,31})", re.I)
LINKEDIN = re.compile(r"(?:https?://)?(?:[\w.]+\.)?linkedin\.com/[\w\-/%]+", re.I)
SITE = re.compile(r"(?:https?://)?(?:www\.)?(?:github\.com|gitlab\.com|behance\.net)/[\w\-/.]+", re.I)


def pv(profile, key, lang):
    """The profile's text for a field in a language; the other language's text when this one is empty ('' if none)."""
    v = (profile.get("values") or {}).get(key) or {}
    other = "en" if lang == "ru" else "ru"
    return v.get(lang) or v.get(other) or ""


def profile_lang_values(found):
    """{key: str} found in a résumé -> {key: {ru, en}}: a text goes into the language it is written in (names, contacts into both)."""
    out = {}
    for k, v in found.items():
        if k == "about":
            out[k] = {resume.detect_lang(v): v}
        else:
            out[k] = {"ru": v, "en": v}
    return out


def clean_profile(data):
    """Whatever the app sent -> {values{key: text}, custom[{label, value, use}], use[key]}; unknown keys dropped."""
    data = data if isinstance(data, dict) else {}
    vals = data.get("values") if isinstance(data.get("values"), dict) else {}
    values = {}
    for k in PROFILE_KEYS:
        v = vals.get(k)
        if isinstance(v, str):  # one text for both languages
            v = {"ru": v, "en": v}
        if isinstance(v, dict):
            both = {lg: str(v.get(lg) or "").strip()[:VALUE_MAX] for lg in ("ru", "en")}
            if any(both.values()):
                values[k] = both
    custom = []
    for c in (data.get("custom") if isinstance(data.get("custom"), list) else [])[:CUSTOM_MAX]:
        if isinstance(c, dict) and str(c.get("label") or "").strip() and str(c.get("value") or "").strip():
            custom.append({"label": str(c["label"]).strip()[:40], "value": str(c["value"]).strip()[:VALUE_MAX], "use": bool(c.get("use", True))})
    use = data.get("use") if isinstance(data.get("use"), list) else [f[0] for f in PROFILE_FIELDS if f[3]]
    return {"values": values, "custom": custom, "use": [k for k in use if k in PROFILE_KEYS]}


def from_resume(md):
    """Details found in the résumé by rules (no AI): {key: text}. Contacts come from the lines under the name."""
    info = parse_resume(md)
    out = {}
    if info["name"]:
        out["fio"] = info["name"]
    if info["summary"]:
        out["about"] = _fit(info["summary"], VALUE_MAX)
    head = []
    for b in resume.blocks(md)[1:]:  # until the first section
        if b["t"] in ("h2", "h3"):
            break
        head.append(b["x"])
    text = " | ".join(head)
    if m := EMAIL.search(text):
        out["email"] = m.group(0)
    if m := PHONE.search(text):
        out["phone"] = m.group(0).strip()
    if m := TG.search(EMAIL.sub(" ", text)):  # an address like a@gmail.com is not a handle
        out["telegram"] = "@" + (m.group(1) or m.group(2)).lstrip("@")
    if m := LINKEDIN.search(text):
        out["linkedin"] = m.group(0)
    if m := SITE.search(text):
        out["site"] = m.group(0)
    for part in re.split(r"[|·•]", next((h for h in head if EMAIL.search(h) or PHONE.search(h)), "")):
        part = part.strip()
        if 2 < len(part) < 40 and not (EMAIL.search(part) or PHONE.search(part) or TG.search(part) or "http" in part.lower()
                                       or re.match(r"(тг|tg|telegram|linkedin)", part, re.I)):
            out["location"] = part
            break
    return out


def contact_block(profile, lang):
    """The contact lines ending a letter: the profile fields (and own fields) the person ticked; name and about are not here."""
    lang = lang if lang in ("ru", "en") else "ru"
    use = set(profile.get("use", []))
    lines = [f"{ru if lang == 'ru' else en}: {pv(profile, key, lang)}" for key, ru, en, _ in PROFILE_FIELDS
             if key not in ("fio", "about") and key in use and pv(profile, key, lang)]
    lines += [f"{c['label']}: {c['value']}" for c in profile.get("custom", []) if c.get("use")]
    return lines


def package_blocks(profile, lang):
    """The ready pieces of a response package, each with its own «copy» button in the app: [{label, text}] in the language.
    Only filled fields; the letter itself and the résumé file are separate."""
    lang = lang if lang in ("ru", "en") else "ru"
    blocks = []
    for key, ru, en, _ in PROFILE_FIELDS:
        text = pv(profile, key, lang)
        if text:
            blocks.append({"key": key, "label": ru if lang == "ru" else en, "text": text})
    blocks += [{"key": "custom", "label": c["label"], "text": c["value"]} for c in profile.get("custom", [])]
    return blocks


MANUAL = [  # a vacancy asking for more than a letter: a test, a questionnaire, real experience answers
    (re.compile(r"тестов\w+ задани|пройдите тест|выполните тест|прикрепите тест", re.I), "тестовое задание"),
    (re.compile(r"ответьте на (?:следующие |несколько )?вопрос|ответы на вопрос|заполните анкету|анкета кандидата|опросник", re.I), "вопросы или анкета"),
    (re.compile(r"answer the following|please answer|application questions|screening questions|fill out the (?:form|questionnaire)|take-home|coding challenge|assessment", re.I), "questions / test"),
    (re.compile(r"опишите (?:свой )?опыт|расскажите о (?:своём|своем) опыте|why do you want|describe your experience", re.I), "рассказ об опыте"),
]


def manual_reasons(text):
    """What in the vacancy needs the person's own answer ([] = a plain letter is enough)."""
    text = str(text or "")
    return [label for rx, label in MANUAL if rx.search(text)]
