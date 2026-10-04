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


def compose(md, item, vacancy_text, lang, tone=DEFAULT_TONE, about=None):
    """The letter text. `about` = the «о себе» paragraph to use (a preset); None = the résumé's own summary."""
    lang = lang if lang in ("ru", "en") else "ru"
    t = TONES.get(tone, TONES[DEFAULT_TONE])[lang]
    info = parse_resume(md)
    company, title = _clean(item.get("company")), _clean(item.get("title")) or ("вакансию" if lang == "ru" else "the role")
    team = (f" ({company})" if lang == "ru" else f", {company} team") if company else ""
    at = (f" в {company}" if lang == "ru" else f" at {company}") if company else ""
    fill = dict(title=title, company=company, team=team, at=at)
    about = _fit(about if about is not None else info["summary"])
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
    parts = [t["hello"].format(**fill), opening, about, skills_line, t["close"], t["bye"] + ("\n" + info["name"] if info["name"] else "")]
    return "\n\n".join(p for p in parts if p).strip()
