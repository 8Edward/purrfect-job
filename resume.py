"""📄 The résumé workshop behind the app's «Резюме» tab. No AI here: plain rules.

- Versions: each person keeps several résumés («Основное», «English», «Под Plata»…), each a
  Markdown file in their folder. Markdown is the stored format; the app shows it exactly
  as the PDF will look and edits it line by line (blocks).
- doctor(): a score and concrete remarks (contacts, sections, weak verbs, no numbers, clichés…).
- match(): what a vacancy asks for that the résumé doesn't mention.
- build(): a résumé from the step-by-step builder (answers -> Markdown).
- translation_guard(): after a translation, checks that numbers, dates, contacts and names survived.
- export: HTML (self-contained, ATS-friendly) and PDF (resume-pdf.mjs, Playwright).

AI help (rewrite, translate, tailor) lives in resume_ai.py and only ever proposes changes.
"""

import asyncio
import html
import re
import secrets
from datetime import datetime, timezone

import cv_reader
import guests
import tg_collect as tc
import webapp

MAX_VERSIONS = 8
MAX_MD = 30000
PDF_SCRIPT = tc.TG_DIR / "resume-pdf.mjs"

SECTION_ALIASES = {
    "summary": ["о себе", "обо мне", "summary", "profile", "профиль", "professional summary", "about me", "about",
                "цель", "objective", "кратко"],
    "experience": ["опыт работы", "опыт", "work experience", "experience", "professional experience",
                   "employment", "места работы", "трудовой опыт", "employment history"],
    "education": ["образование", "education"],
    "skills": ["навыки", "ключевые навыки", "skills", "key skills", "технологии", "tech stack", "стек",
               "технические навыки", "technical skills", "hard skills", "компетенции"],
    "languages": ["языки", "languages", "знание языков", "иностранные языки"],
    "projects": ["проекты", "projects", "pet projects", "pet-проекты", "портфолио", "portfolio"],
    "courses": ["курсы", "сертификаты", "certifications", "certificates", "courses", "training", "повышение квалификации"],
    "other": ["дополнительно", "additional", "additional information", "дополнительная информация", "прочее", "интересы",
              "interests", "достижения", "achievements"],
}
HEADINGS = {
    "ru": {"summary": "О себе", "experience": "Опыт работы", "education": "Образование", "skills": "Навыки",
           "languages": "Языки", "projects": "Проекты", "courses": "Курсы и сертификаты"},
    "en": {"summary": "Summary", "experience": "Work Experience", "education": "Education", "skills": "Skills",
           "languages": "Languages", "projects": "Projects", "courses": "Courses & Certifications"},
}
DATE_RANGE = re.compile(
    r"((19|20)\d{2}|\b(янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s*(19|20)?\d{0,2})"
    r"\s*(—|–|-|по|to|until|till)\s*"
    r"((19|20)\d{2}|наст\w*|н\.?\s?в\.?|сейчас|present|now|current|today|\w+\.?\s*(19|20)\d{2})", re.IGNORECASE)
BULLET = re.compile(r"^\s*([-•–—*·▪●◦■□➤►]|\d{1,2}[.)])\s+")
WEAK = re.compile(
    r"^(занимал(ся|ась|ись)|занимаюсь|отвечал[аи]? за|отвечаю за|участвовал[аи]?|участвую|принимал[аи]? участие|"
    r"работал[аи]? (с|над|в|на)|выполнял[аи]?|помогал[аи]?|делал[аи]?|в мои обязанности|обязанности|"
    r"был[аи]? ответствен\w*|ответствен\w* за|осуществлял[аи]?|"
    r"responsible for|worked (on|with|in)|helped|participated in|involved in|duties (included|include)|assisted|"
    r"tasked with|in charge of|was responsible)\b", re.IGNORECASE)
CLICHES = re.compile(
    r"\b(ответственн\w+|коммуникабельн\w+|стрессоустойчив\w+|быстро обучаем\w+|обучаем\w+|целеустремл[её]нн\w+|"
    r"исполнительн\w+|пунктуальн\w+|трудолюбив\w+|командн\w+ игрок\w*|нацелен\w* на результат|"
    r"без вредных привычек|высок\w+ работоспособност\w+|креативн\w+|"
    r"team player|hard[- ]working|fast learner|quick learner|results[- ]driven|self[- ]motivated|"
    r"detail[- ]oriented|go[- ]getter|think outside the box|dynamic|passionate)\b", re.IGNORECASE)
PERSONAL = re.compile(r"(дата рождения|date of birth|\bborn\b|семейное положение|marital status|женат|замужем|"
                      r"\bдети\b|children|вероисповедан|religion|\bвес\b|\bрост\b)", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d\s()\-]{8,}\d")
MESSENGER = re.compile(r"(t\.me/|telegram|linkedin|github|@[a-z0-9_]{4,}|портфолио|portfolio|behance|dribbble)", re.IGNORECASE)
WHERE = re.compile(r"(utc|gmt|часов\w* пояс\w*|time ?zone|remote|удал[её]нн?\w*|relocat|релокац)", re.IGNORECASE)
NUMBER = re.compile(r"\d")
STRONG_VERBS = {
    "ru": ["Разработал", "Построил", "Автоматизировал", "Оптимизировал", "Сократил", "Ускорил", "Внедрил",
           "Запустил", "Спроектировал", "Проанализировал", "Выявил", "Повысил", "Организовал", "Настроил"],
    "en": ["Built", "Designed", "Automated", "Optimized", "Reduced", "Accelerated", "Implemented", "Launched",
           "Analyzed", "Identified", "Increased", "Led", "Streamlined", "Delivered"],
}


# ---------------------------------------------------------------- storage: versions

def folder(member):
    return guests.resume_dir(member)


def load_index(member):
    data = member.load_state().get("resumes") or {}
    data.setdefault("list", [])
    data.setdefault("active", data["list"][0]["id"] if data["list"] else None)
    return data


def save_index(member, index):
    member.update_state(resumes=index)


def version_path(member, vid):
    if not re.fullmatch(r"[a-z0-9]{4,16}", vid or ""):
        raise ValueError("Нет такой версии")
    return folder(member) / f"{vid}.md"


def get_version(member, vid=None):
    """(meta, markdown) of a version; the active one by default. ValueError if missing."""
    index = load_index(member)
    vid = vid or index["active"]
    meta = next((v for v in index["list"] if v["id"] == vid), None)
    if not meta:
        raise ValueError("Нет такой версии")
    path = version_path(member, vid)
    return meta, path.read_text(encoding="utf-8") if path.exists() else ""


def save_version(member, markdown, vid=None, name=None, lang=None, make_active=True):
    """Create (vid=None) or overwrite a version. Returns its meta."""
    markdown = (markdown or "").replace("\r\n", "\n").strip()[:MAX_MD] + "\n"
    index = load_index(member)
    now = datetime.now(timezone.utc).isoformat()
    meta = next((v for v in index["list"] if v["id"] == vid), None) if vid else None
    if meta is None:
        if len(index["list"]) >= MAX_VERSIONS:
            raise ValueError(f"Не больше {MAX_VERSIONS} версий резюме. Удалите ненужную.")
        meta = {"id": secrets.token_hex(4), "name": (name or "Резюме")[:40], "created": now}
        index["list"].append(meta)
    if name:
        meta["name"] = name[:40]
    meta["lang"] = lang or detect_lang(markdown)
    meta["at"] = now
    path = version_path(member, meta["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    if make_active:
        index["active"] = meta["id"]
    save_index(member, index)
    return meta


def delete_version(member, vid):
    index = load_index(member)
    index["list"] = [v for v in index["list"] if v["id"] != vid]
    path = version_path(member, vid)
    if path.exists():
        path.unlink()
    if index["active"] == vid:
        index["active"] = index["list"][0]["id"] if index["list"] else None
    save_index(member, index)


def rename_version(member, vid, name):
    index = load_index(member)
    for v in index["list"]:
        if v["id"] == vid:
            v["name"] = (name or v["name"]).strip()[:40] or v["name"]
    save_index(member, index)


def set_active(member, vid):
    index = load_index(member)
    if not any(v["id"] == vid for v in index["list"]):
        raise ValueError("Нет такой версии")
    index["active"] = vid
    save_index(member, index)


def unique_name(member, base):
    names = {v["name"] for v in load_index(member)["list"]}
    if base not in names:
        return base
    n = 2
    while f"{base} {n}" in names:
        n += 1
    return f"{base} {n}"


# ---------------------------------------------------------------- Markdown <-> blocks

def detect_lang(text):
    cyr = len(re.findall(r"[а-яё]", text, re.IGNORECASE))
    lat = len(re.findall(r"[a-z]", text, re.IGNORECASE))
    return "ru" if cyr >= lat * 0.6 else "en"


def blocks(markdown):
    """Markdown -> [{"t": h1|h2|h3|li|p, "x": inline text}]. The app edits these one by one."""
    out = []
    for line in (markdown or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            out.append({"t": f"h{len(m.group(1))}", "x": m.group(2).strip()})
        elif BULLET.match(s):
            out.append({"t": "li", "x": BULLET.sub("", s, count=1).strip()})
        else:
            out.append({"t": "p", "x": s})
    return out


def markdown_of(blocks_):
    lines, prev = [], None
    for b in blocks_:
        t, x = b.get("t"), re.sub(r"\s+", " ", str(b.get("x", ""))).strip()
        if not x or t not in ("h1", "h2", "h3", "li", "p"):
            continue
        if t in ("h1", "h2", "h3"):
            if lines:
                lines.append("")
            lines.append("#" * int(t[1]) + " " + x)
            lines.append("")
        elif t == "li":
            if prev not in ("li", None) and lines and lines[-1] != "":
                lines.append("")
            lines.append("- " + x)
        else:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(x)
        prev = t
    return "\n".join(lines).strip() + "\n"


def with_summary(blocks_, text):
    """Blocks with `text` as the «О себе» section (created under the header if there is none)."""
    out = [dict(b) for b in blocks_]
    lang = detect_lang(markdown_of(out))
    start = next((i for i, b in enumerate(out) if b["t"] == "h2" and section_key(b["x"]) == "summary"), None)
    if start is None:
        pos = next((i for i, b in enumerate(out) if b["t"] == "h2"), len(out))
        return out[:pos] + [{"t": "h2", "x": HEADINGS[lang]["summary"]}, {"t": "p", "x": text}] + out[pos:]
    end = next((i for i in range(start + 1, len(out)) if out[i]["t"] in ("h1", "h2")), len(out))
    return out[:start + 1] + [{"t": "p", "x": text}] + out[end:]


def section_key(title):
    t = re.sub(r"[^\w\s/&-]", "", webapp.norm(title)).strip()
    for key, aliases in SECTION_ALIASES.items():
        if t in aliases:
            return key
    return None


def from_text(text):
    """Plain text from a PDF/DOCX/TXT résumé -> tidy Markdown: name, section headings,
    job titles inside «experience», bullets. The person can fix the rest in the editor."""
    if re.search(r"^#{1,3} ", text or "", re.MULTILINE):
        return text.strip() + "\n"  # already Markdown
    lines = [re.sub(r"\s+", " ", l).strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if l]
    out, section = [], None
    for i, line in enumerate(lines):
        bare = line.rstrip(":").strip()
        key = section_key(bare) if len(bare) <= 40 else None
        if i == 0 and not key and len(line) <= 60 and not EMAIL.search(line) and not NUMBER.search(line):
            out.append({"t": "h1", "x": line})
            continue
        if key:
            section = key
            out.append({"t": "h2", "x": bare[:1].upper() + bare[1:]})
            continue
        if BULLET.match(line):
            out.append({"t": "li", "x": BULLET.sub("", line, count=1)})
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if section == "experience" and len(line) <= 90 and not line.endswith(".") and not DATE_RANGE.fullmatch(line) \
                and (BULLET.match(nxt) or DATE_RANGE.search(nxt)) and not DATE_RANGE.search(line[:4]):
            out.append({"t": "h3", "x": line})
            continue
        out.append({"t": "p", "x": line})
    return markdown_of(out)


def inline_html(text):
    """**bold**, *italic*, [text](url) on top of escaped text."""
    s = html.escape(text, quote=True)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+|mailto:[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", s)
    return s


# ---------------------------------------------------------------- templates (one look for the app and the PDF)

TEMPLATES = {  # id -> (name, one-line description)
    "classic": ("Классика ATS", "Одна колонка, лучше всего проходит роботов-фильтров"),
    "modern": ("Современный", "Две колонки: навыки, языки и учёба сбоку"),
    "compact": ("Компактный", "Мелче и плотнее — чтобы уместить на одну страницу"),
    "minimal": ("Минимал", "Строгий, с засечками и воздухом"),
}
COLORS = {"blue": "#24407a", "teal": "#0f6a6e", "green": "#2f6b3a", "wine": "#7a2443", "orange": "#a8501c", "graphite": "#2b2f36"}
SIDE_KEYS = ("skills", "languages", "education", "courses")  # «Современный»: these go to the side column
PAGE_W = 794  # A4 width in CSS px; the app scales the page down to the phone
DEFAULT_DESIGN = {"tpl": "classic", "color": "blue", "photo": False, "shape": "round"}

BASE_CSS = """
.cv { --ac: %(color)s; --ac-soft: %(soft)s; color: #1a1d24; font-family: "Segoe UI", Arial, Helvetica, sans-serif;
  font-size: 10.5pt; line-height: 1.42; box-sizing: border-box; background: #fff; }
@media screen { .cv { width: 794px; min-height: 1123px; padding: 56px 60px; } }
.cv * { box-sizing: border-box; }
.cv h1 { font-size: 22pt; margin: 0 0 4px; letter-spacing: -.01em; line-height: 1.15; }
.cv h2 { font-size: 11.5pt; text-transform: uppercase; letter-spacing: .06em; color: var(--ac); border-bottom: 1.2px solid var(--ac-soft);
  padding-bottom: 3px; margin: 16px 0 8px; break-after: avoid; }
.cv h3 { font-size: 11pt; margin: 10px 0 1px; break-after: avoid; }
.cv h3 + p { margin: 0 0 4px; color: #586072; font-size: 9.8pt; }
.cv p { margin: 3px 0; }
.cv ul { margin: 3px 0 6px; padding-left: 18px; }
.cv li { margin: 2px 0; break-inside: avoid; }
.cv a { color: var(--ac); text-decoration: none; }
.cv .head { display: flex; align-items: center; gap: 18px; }
.cv .head > div { flex: 1; min-width: 0; }
.cv .head p { color: #3d4452; margin: 1px 0; }
.cv .photo { width: 104px; height: 104px; object-fit: cover; flex: none; border-radius: 50%%; }
.cv .photo.square { border-radius: 8px; }
"""
TEMPLATE_CSS = {
    "classic": """
.cv .head { flex-direction: row-reverse; }
""",
    "modern": """
.cv { font-size: 10.2pt; }
.cv .head { background: var(--ac); color: #fff; border-radius: 10px; padding: 20px 22px; margin-bottom: 16px; }
.cv .head p { color: rgba(255,255,255,.88); }
.cv .head a { color: #fff; }
.cv .head .photo { border: 3px solid rgba(255,255,255,.85); }
.cv .cols { display: grid; grid-template-columns: 34% 1fr; gap: 22px; align-items: start; }
.cv aside { background: var(--ac-soft); border-radius: 10px; padding: 4px 14px 12px; }
.cv aside h2 { border-bottom-color: rgba(0,0,0,.12); font-size: 10.5pt; }
.cv aside h3 { font-size: 10pt; }
.cv main > section:first-child h2, .cv aside > section:first-child h2 { margin-top: 10px; }
.cv h3 { color: var(--ac); }
""",
    "compact": """
.cv { font-family: Arial, Helvetica, sans-serif; font-size: 9.4pt; line-height: 1.3; }
@media screen { .cv { padding: 40px 44px; } }
.cv h1 { font-size: 18pt; }
.cv h2 { font-size: 10pt; margin: 10px 0 4px; padding-bottom: 2px; }
.cv h3 { font-size: 9.8pt; margin: 6px 0 0; }
.cv h3 + p { font-size: 8.8pt; margin-bottom: 2px; }
.cv li { margin: 1px 0; }
.cv ul { margin: 2px 0 4px; padding-left: 15px; }
.cv .photo { width: 80px; height: 80px; }
.cv .head { flex-direction: row-reverse; }
""",
    "minimal": """
.cv { font-family: Georgia, "Times New Roman", serif; font-size: 10.6pt; line-height: 1.5; }
.cv .head { flex-direction: column; text-align: center; gap: 10px; margin-bottom: 8px; }
.cv h1 { font-size: 24pt; font-weight: 400; letter-spacing: .02em; }
.cv h2 { text-transform: none; letter-spacing: .02em; font-size: 12.5pt; font-weight: 400; font-style: italic; color: var(--ac);
  border-bottom: none; border-top: 1px solid #d9dbe0; padding: 8px 0 0; margin-top: 18px; }
.cv h3 { font-size: 10.8pt; }
.cv h3 + p { font-style: italic; }
""",
}
PRINT_CSS = "@page { size: A4; margin: 14mm 15mm; } html, body { margin: 0; background: #fff; } .cv { -webkit-print-color-adjust: exact; print-color-adjust: exact; }"


def soft(color):
    """A pale tint of the accent colour (for lines and the side column)."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    mix = lambda c: round(c + (255 - c) * 0.88)  # noqa: E731
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def design_of(meta):
    d = dict(DEFAULT_DESIGN)
    d.update({k: meta[k] for k in DEFAULT_DESIGN if k in (meta or {})})
    if d["tpl"] not in TEMPLATES:
        d["tpl"] = "classic"
    if d["color"] not in COLORS:
        d["color"] = "blue"
    return d


def template_css(design):
    color = COLORS[design["color"]]
    return BASE_CSS % {"color": color, "soft": soft(color)} + TEMPLATE_CSS[design["tpl"]]


def render(markdown, design=None, photo=None, editable=False):
    """The résumé body (<div class="cv">…) in a template. `photo` is a data: URI or None.
    editable=True marks every line with data-i (its block index) for the app's tap-to-edit."""
    design = design or dict(DEFAULT_DESIGN)
    bl = blocks(markdown)
    head, secs = [], []
    for i, b in enumerate(bl):
        if b["t"] == "h2":
            secs.append((section_key(b["x"]), [(i, b)]))
        elif secs:
            secs[-1][1].append((i, b))
        else:
            head.append((i, b))

    def lines(items):
        out, in_list = [], False
        for i, b in items:
            if b["t"] == "li" and not in_list:
                out.append("<ul>")
                in_list = True
            elif b["t"] != "li" and in_list:
                out.append("</ul>")
                in_list = False
            mark = f' data-i="{i}"' if editable else ""
            out.append(f"<{b['t']}{mark}>{inline_html(b['x'])}</{b['t']}>")
        if in_list:
            out.append("</ul>")
        return "".join(out)

    pic = ""
    if photo and design.get("photo"):
        shape = " square" if design.get("shape") == "square" else ""
        mark = ' data-photo="1"' if editable else ""
        pic = f'<img class="photo{shape}" src="{photo}" alt=""{mark}>'
    parts = [f'<header class="head">{pic}<div>{lines(head)}</div></header>' if head or pic else ""]
    section = lambda s: f'<section class="sec-{s[0] or "other"}">{lines(s[1])}</section>'  # noqa: E731
    side = [s for s in secs if s[0] in SIDE_KEYS]
    if design["tpl"] == "modern" and side and len(side) < len(secs):
        main = [s for s in secs if s[0] not in SIDE_KEYS]
        parts.append(f'<div class="cols"><aside>{"".join(map(section, side))}</aside><main>{"".join(map(section, main))}</main></div>')
    else:
        parts.append("".join(map(section, secs)))
    return f'<div class="cv tpl-{design["tpl"]}">{"".join(parts)}</div>'


def to_html(markdown, title="Резюме", design=None, photo=None):
    """A self-contained HTML page in the chosen template (real text, so ATS can read it)."""
    design = design or dict(DEFAULT_DESIGN)
    lang = detect_lang(markdown)
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{template_css(design)}
@media screen {{ body {{ background: #eef0f4; margin: 0; padding: 20px 0; }} .cv {{ margin: 0 auto; box-shadow: 0 2px 16px rgba(0,0,0,.12); }} }}
@media print {{ {PRINT_CSS} }}</style></head><body>
{render(markdown, design, photo)}
</body></html>
"""


# ---------------------------------------------------------------- design and photo

def set_design(member, vid, **changes):
    """Template / colour / photo on or off / photo shape of a version."""
    index = load_index(member)
    meta = next((v for v in index["list"] if v["id"] == vid), None)
    if not meta:
        raise ValueError("Нет такой версии")
    for k, v in changes.items():
        if k == "tpl" and v in TEMPLATES or k == "color" and v in COLORS or k == "shape" and v in ("round", "square"):
            meta[k] = v
        elif k == "photo":
            meta[k] = bool(v)
    save_index(member, index)
    return design_of(meta)


def photo_path(member):
    return folder(member) / "photo.jpg"


def save_photo(member, data):
    """Bytes of an image (already cropped square in the app) -> photo.jpg, 400×400 JPEG."""
    from io import BytesIO
    from PIL import Image
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("Не получилось открыть картинку. Нужен JPG или PNG.")
    img = img.convert("RGB")
    side = min(img.size)
    img = img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2))
    img = img.resize((400, 400), Image.LANCZOS)
    path = photo_path(member)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=86)
    return path


def delete_photo(member):
    path = photo_path(member)
    if path.exists():
        path.unlink()
    index = load_index(member)
    for v in index["list"]:
        v["photo"] = False
    save_index(member, index)


def photo_uri(member):
    import base64
    path = photo_path(member)
    if not path.exists():
        return None
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


# ---------------------------------------------------------------- doctor: score and remarks

def sections(markdown):
    """[(key or None, title, [blocks])] — the blocks under each ## heading; the top part has key 'head'."""
    out = [("head", "", [])]
    for b in blocks(markdown):
        if b["t"] == "h2":
            out.append((section_key(b["x"]), b["x"], []))
        else:
            out[-1][2].append(b)
    return out


def experience_bullets(markdown):
    """Bullets of the experience section(s); without a recognised section, all bullets."""
    secs = sections(markdown)
    found = [b["x"] for key, _, bl in secs if key == "experience" for b in bl if b["t"] == "li"]
    if found:
        return found
    return [b["x"] for _, _, bl in secs for b in bl if b["t"] == "li"]


def doctor(markdown):
    """{"score": 0..100, "level": text, "remarks": [...], "good": [...], "stats": {...}}."""
    text = markdown or ""
    lang = detect_lang(text)
    secs = sections(text)
    keys = {k for k, _, _ in secs}
    bl = blocks(text)
    words = len(re.findall(r"\w+", text))
    bullets = experience_bullets(text)
    with_numbers = [b for b in bullets if NUMBER.search(b)]
    remarks, good = [], []

    def remark(rid, level, title, detail, examples=None, ai=None):
        remarks.append({"id": rid, "level": level, "title": title, "detail": detail,
                        "examples": (examples or [])[:4], "ai": ai})

    head_text = " ".join(b["x"] for b in secs[0][2]) + " " + text[:600]
    if not EMAIL.search(text) and not re.search(r"t\.me/|telegram|@[a-z0-9_]{4,}", text, re.I):
        remark("contacts", "must", "Нет контактов", "Добавьте email или Telegram в самый верх, под имя.")
    else:
        good.append("Контакты на месте")
    if not WHERE.search(head_text):
        remark("where", "should", "Не сказано, где вы и в каком часовом поясе",
               "Для удалёнки это спрашивают первым: добавьте строку вроде «Таиланд, UTC+7 · готов к любому часовому поясу · удалённо».")
    if not any(b["t"] == "h1" for b in bl):
        remark("name", "should", "Нет имени заголовком", "Первая строка резюме — ваше имя крупно.")
    if "summary" not in keys:
        remark("summary", "should", "Нет блока «О себе»",
               "2–3 предложения сверху: кто вы, сколько лет опыта, с чем работаете и что ищете. Его читают первым.",
               ai="summary")
    else:
        summary = " ".join(b["x"] for k, _, bl_ in secs if k == "summary" for b in bl_)
        if len(summary) > 650:
            remark("summary-long", "nice", "«О себе» длинновато", "Оставьте 2–4 предложения — до 600 знаков.", ai="summary")
        elif re.match(r"^(я|i)\b", summary.strip(), re.I):
            remark("summary-i", "nice", "«О себе» начинается с «Я»", "Обычно пишут без местоимений: «Аналитик данных с 3 годами опыта…».", ai="summary")
        else:
            good.append("Есть блок «О себе»")
    if "experience" not in keys:
        remark("experience", "must", "Не нашёл раздел «Опыт работы»",
               "Заголовок раздела: «Опыт работы» (или Work Experience), под ним места работы с датами и пунктами.")
    if "skills" not in keys:
        remark("skills", "should", "Нет раздела «Навыки»", "Список инструментов и технологий: по нему ищут роботы-фильтры (ATS).")
    else:
        good.append("Есть навыки")
    if "education" not in keys:
        remark("education", "nice", "Нет раздела «Образование»", "Даже одна строка: вуз, специальность, год.")
    if words < 150:
        remark("short", "must", "Резюме слишком короткое", f"Сейчас {words} слов. Хорошее резюме — 300–800 слов: опишите задачи и результаты.")
    elif words > 1100:
        remark("long", "should", "Резюме длинное", f"{words} слов — это больше двух страниц. Оставьте главное за последние 5–7 лет.", ai="improve")
    if bullets:
        weak = [b for b in bullets if WEAK.match(b)]
        if weak:
            verbs = ", ".join(STRONG_VERBS[lang][:6])
            remark("weak", "should" if len(weak) < 3 else "must", f"Слабые формулировки: {len(weak)}",
                   f"«Занимался / отвечал за / участвовал» звучат как обязанности. Начинайте с действия: {verbs}…",
                   weak, ai="improve")
        share = len(with_numbers) / len(bullets)
        if len(bullets) >= 3 and share < 0.5:
            remark("numbers", "must" if share < 0.25 else "should", f"Мало цифр: {len(with_numbers)} из {len(bullets)} пунктов",
                   "Цифры доказывают результат: сколько, на сколько процентов, за какое время, для скольких людей. "
                   "Добавьте их хотя бы в половину пунктов — только настоящие.",
                   [b for b in bullets if not NUMBER.search(b)])
        else:
            good.append(f"Цифры в {len(with_numbers)} из {len(bullets)} пунктов")
        long_ = [b for b in bullets if len(b) > 230]
        if long_:
            remark("long-bullets", "nice", f"Длинные пункты: {len(long_)}", "Один пункт — одна мысль, до двух строк.", long_, ai="improve")
        starts = {}
        for b in bullets:
            w = (re.findall(r"\w+", b.lower()) or [""])[0]
            starts[w] = starts.get(w, 0) + 1
        repeated = [w for w, n in starts.items() if n >= 3 and w]
        if repeated:
            remark("repeat", "nice", "Пункты начинаются одинаково",
                   f"«{repeated[0]}» — {starts[repeated[0]]} раз. Чередуйте глаголы: {', '.join(STRONG_VERBS[lang][:5])}.", ai="improve")
    elif "experience" in keys:
        remark("no-bullets", "should", "В опыте нет пунктов",
               "Под каждым местом работы 3–5 пунктов «действие → результат», каждый с новой строки через «-».")
    entries = [b for k, _, bl_ in secs if k == "experience" for b in bl_ if b["t"] == "h3"]
    exp_text = "\n".join(b["x"] for k, _, bl_ in secs if k == "experience" for b in bl_)
    if entries and not DATE_RANGE.search(exp_text):
        remark("dates", "must", "У мест работы нет дат", "Укажите период: «Март 2024 — сейчас».")
    cliches = sorted({m.group(0).lower() for m in CLICHES.finditer(text)})
    if cliches:
        remark("cliches", "nice" if len(cliches) < 3 else "should", "Пустые слова",
               "Эти качества ничего не доказывают — уберите или замените фактом: " + ", ".join(cliches[:6]) + ".",
               cliches, ai="improve")
    personal = sorted({m.group(0).lower() for m in PERSONAL.finditer(text)})
    if personal:
        remark("personal", "nice", "Лишние личные данные",
               "Для международной удалёнки дату рождения, семейное положение и т.п. обычно не указывают: " + ", ".join(personal) + ".")
    skills_items = [x for k, _, bl_ in secs if k == "skills" for b in bl_ for x in re.split(r"[,;·|]", b["x"])]
    if len(skills_items) > 35:
        remark("skills-many", "nice", f"Очень длинный список навыков ({len(skills_items)})", "Оставьте 15–25 главных, сгруппируйте по темам.")

    weights = {"must": 14, "should": 7, "nice": 3}
    score = max(5, 100 - sum(weights[r["level"]] for r in remarks))
    order = {"must": 0, "should": 1, "nice": 2}
    remarks.sort(key=lambda r: order[r["level"]])
    level = ("Отличное резюме" if score >= 85 else "Хорошее резюме" if score >= 70
             else "Можно сделать сильнее" if score >= 50 else "Нужно доработать")
    return {"score": score, "level": level, "remarks": remarks, "good": good,
            "stats": {"words": words, "bullets": len(bullets), "with_numbers": len(with_numbers), "lang": lang}}


# ---------------------------------------------------------------- vacancy match

STOP_CAPS = {"the", "we", "you", "our", "and", "for", "with", "about", "who", "what", "are", "is", "this", "that", "your",
             "join", "team", "role", "job", "company", "remote", "full", "time", "part", "senior", "middle", "junior",
             "lead", "will", "can", "must", "nice", "have", "plus", "apply", "please", "requirements", "responsibilities",
             "benefits", "salary", "location", "position", "description", "english", "russian", "we're", "it", "in", "of",
             "to", "a", "an", "or", "on", "at", "as", "be", "by", "if", "all", "us", "eu", "uk", "usa", "hr", "ceo",
             "monday", "friday", "requirements:", "you'll", "ideal", "candidate", "experience", "skills", "work", "new"}


def match(resume_md, vacancy):
    """What the vacancy asks for, and which of it the résumé mentions. Rules only."""
    vac, res = webapp.norm(vacancy), webapp.norm(resume_md)
    terms = {}
    for label, rx, _roles, _word in cv_reader.SKILL_RE:  # known skills and tools, by group
        found = sorted({m.group(0).strip() for m in rx.finditer(vac)}, key=len, reverse=True)
        for f in found[:4]:
            terms.setdefault(f, {"term": f, "group": label, "count": len(rx.findall(vac))})
    for tok in re.findall(r"(?<![\w@/.])([A-Z][A-Za-z0-9+#.]{1,20}|[a-z]+[A-Z][A-Za-z0-9]+|C\+\+|C#|\.NET)(?![\w])", vacancy or ""):
        t = tok.strip(".").lower()
        if len(t) >= 2 and t not in STOP_CAPS and not t.isdigit():
            item = terms.setdefault(t, {"term": tok.strip("."), "group": "упоминается", "count": 0})
            item["count"] += 1
    graph = webapp.load_graph()
    rid = webapp.find_role(graph, vacancy[:300] if vacancy else "")
    present, missing = [], []
    for key, item in terms.items():
        rx = webapp.phrase_re(item["term"])
        hit = bool(rx and rx.search(res)) or key in res
        (present if hit else missing).append(item)
    rank = lambda i: (i["group"] == "упоминается", -i["count"])  # noqa: E731
    present.sort(key=rank)
    missing.sort(key=rank)
    total = len(present) + len(missing)
    missing = [i for i in missing if i["group"] != "упоминается" or i["count"] >= 2][:20]
    return {"score": round(100 * len(present) / total) if total else 0, "present": present[:30], "missing": missing,
            "role": graph[rid]["names"][0] if rid else None}


# ---------------------------------------------------------------- builder from scratch

def compose_bullet(action, how="", result="", lang="ru"):
    """«что сделал» + «чем» + «результат» -> one strong line."""
    action, how, result = (s.strip().rstrip(".;,") for s in (action or "", how or "", result or ""))
    if not action:
        return ""
    line = action[:1].upper() + action[1:]
    if how:
        line += " " + how
    if result:
        joiner = ", " if re.match(r"^[а-яё]+(в|вши)\b|^(which|saving|cutting|reducing|increasing)\b", result, re.I) else ": " if lang == "ru" else ", "
        line += joiner + result
    return line


def years_of(jobs):
    years = []
    now = datetime.now().year
    for j in jobs:
        a = re.search(r"(19|20)\d{2}", j.get("from", "") or "")
        b = re.search(r"(19|20)\d{2}", j.get("to", "") or "")
        if a:
            years.append((int(a.group(0)), int(b.group(0)) if b else now))
    if not years:
        return 0
    return max(0, max(e for _, e in years) - min(s for s, _ in years))


def suggest_summary(d):
    """A plain, honest first draft of «О себе» from the builder answers."""
    lang = d.get("lang", "ru")
    title = (d.get("title") or "").strip()
    years = years_of(d.get("jobs") or [])
    skills = [s for s in (d.get("skills") or []) if s][:5]
    wants = d.get("wants") or []
    if lang == "en":
        parts = [f"{title or 'Specialist'}" + (f" with {years}+ years of experience" if years else "") + "."]
        if skills:
            parts.append("Work with " + ", ".join(skills) + ".")
        if wants:
            parts.append("Looking for " + " / ".join(wants) + " remote work.")
    else:
        y = f" с опытом {years}+ {'год' if years == 1 else 'года' if years < 5 else 'лет'}" if years else ""
        parts = [f"{title or 'Специалист'}{y}."]
        if skills:
            parts.append("Работаю с " + ", ".join(skills) + ".")
        if wants:
            parts.append("Ищу удалённую работу: " + ", ".join(wants) + ".")
    return " ".join(parts)


def build(d):
    """Builder answers -> Markdown résumé."""
    lang = "en" if d.get("lang") == "en" else "ru"
    h = HEADINGS[lang]
    out = []
    name = (d.get("name") or "").strip() or ("Your Name" if lang == "en" else "Имя Фамилия")
    out.append({"t": "h1", "x": name})
    if d.get("title"):
        out.append({"t": "p", "x": f"**{d['title'].strip()}**"})
    contact = [d.get(k, "").strip() for k in ("location", "email", "telegram", "phone", "linkedin", "github") if (d.get(k) or "").strip()]
    if contact:
        out.append({"t": "p", "x": " · ".join(contact)})
    summary = (d.get("summary") or "").strip() or suggest_summary(d)
    out += [{"t": "h2", "x": h["summary"]}, {"t": "p", "x": summary}]
    jobs = [j for j in d.get("jobs") or [] if (j.get("role") or j.get("company"))]
    if jobs:
        out.append({"t": "h2", "x": h["experience"]})
        for j in jobs:
            role, company = (j.get("role") or "").strip(), (j.get("company") or "").strip()
            out.append({"t": "h3", "x": " — ".join(x for x in (role, company) if x)})
            when = " — ".join(x for x in ((j.get("from") or "").strip(), (j.get("to") or "").strip()) if x)
            meta = " | ".join(x for x in ((j.get("place") or "").strip(), when) if x)
            if meta:
                out.append({"t": "p", "x": meta})
            for b in j.get("bullets") or []:
                text = compose_bullet(b.get("action"), b.get("how"), b.get("result"), lang) if isinstance(b, dict) else str(b).strip()
                if text:
                    out.append({"t": "li", "x": text})
    edu = [e for e in d.get("education") or [] if e.get("school")]
    if edu:
        out.append({"t": "h2", "x": h["education"]})
        for e in edu:
            out.append({"t": "h3", "x": e["school"].strip()})
            meta = " | ".join(x for x in ((e.get("degree") or "").strip(), (e.get("year") or "").strip()) if x)
            if meta:
                out.append({"t": "p", "x": meta})
    skills = [s.strip() for s in d.get("skills") or [] if s and s.strip()]
    if skills:
        out += [{"t": "h2", "x": h["skills"]}, {"t": "p", "x": ", ".join(dict.fromkeys(skills))}]
    langs = [x for x in d.get("languages") or [] if (x.get("name") or "").strip()]
    if langs:
        out.append({"t": "h2", "x": h["languages"]})
        out.append({"t": "p", "x": ", ".join(f"{x['name'].strip()}" + (f" ({x['level'].strip()})" if (x.get("level") or "").strip() else "")
                                         for x in langs)})
    projects = [p for p in d.get("projects") or [] if (p.get("name") or "").strip()]
    if projects:
        out.append({"t": "h2", "x": h["projects"]})
        for p in projects:
            out.append({"t": "li", "x": f"**{p['name'].strip()}**" + (f" — {p['text'].strip()}" if (p.get("text") or "").strip() else "")})
    return markdown_of(out)


def skill_suggestions(title):
    """Skills to offer as chips in the builder for a target role (from the skills table)."""
    graph = webapp.load_graph()
    rid = webapp.find_role(graph, title or "")
    names = {
        "SQL": ["SQL", "PostgreSQL", "ClickHouse"], "Python": ["Python", "Pandas"], "Excel / Sheets": ["Excel", "Google Sheets"],
        "BI-инструменты": ["Power BI", "Tableau", "Superset", "DataLens"], "ETL / DWH": ["Airflow", "dbt", "Spark", "DWH"],
        "Качество данных": ["Data Quality"], "Продуктовые метрики": ["A/B-тесты", "Unit-экономика", "Retention"],
        "ML": ["scikit-learn", "PyTorch"], "Статистика": ["Статистика"], "Требования / BPMN": ["BPMN", "UML", "User Stories"],
        "API и интеграции": ["REST API", "Swagger", "Postman"], "Jira / Agile": ["Jira", "Confluence", "Scrum"],
        "Продукт": ["Roadmap", "CJM", "Discovery"], "Финансы": ["Бюджетирование", "P&L", "МСФО"], "1С / учёт": ["1С", "РСБУ"],
        "Дизайн": ["Figma", "Photoshop", "UX"], "Тестирование": ["Тест-кейсы", "Postman", "Selenium"],
        "Подбор персонала": ["Подбор", "Sourcing", "ATS"], "Маркетинг-аналитика": ["Яндекс Метрика", "Google Analytics", "SEO"],
        "Поддержка клиентов": ["Zendesk", "Helpdesk"],
    }
    out = []
    for label, _rx, roles, _w in cv_reader.SKILL_RE:
        if rid and rid in roles:
            out += names.get(label, [])
    return list(dict.fromkeys(out))[:18] or ["SQL", "Excel", "Python", "Jira", "Figma", "Английский"]


# ---------------------------------------------------------------- translation guard

MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
          "present", "i", "a", "the", "and", "or", "in", "for", "with", "of", "to", "on", "at", "by"}


def name_tokens(text):
    """Latin tokens that look like names of tools, products or companies: a capital letter after the
    first one (PostgreSQL), digits or +/# (C++, S3), or a capitalised word that doesn't start the line
    or sentence (Visio, Kafka). Lower-cased. Ordinary words, months and line-initial verbs are skipped."""
    out = set()
    for line in (text or "").splitlines():
        line = BULLET.sub("", re.sub(r"^#+\s*", "", line.strip()))
        for m in re.finditer(r"[A-Za-z][A-Za-z0-9+#.]*[A-Za-z0-9+#]|[A-Za-z]", line):
            w = m.group(0)
            low = w.lower()
            if low in MONTHS or len(w) < 2:
                continue
            before = line[:m.start()]
            starts_sentence = not before.strip() or re.search(r"[.!?:;—–-]\s*$", before)
            special = any(c.isupper() for c in w[1:]) or any(c.isdigit() or c in "+#" for c in w)
            if special or (w[0].isupper() and not starts_sentence):
                out.add(low)
    return out

def translation_guard(source, target, target_lang):
    """Facts that must survive a translation: numbers, emails, links, handles, Latin names,
    and the shape (headings, bullets). Returns a list of human-readable problems."""
    problems = []

    def tokens(rx, text):
        return [m.group(0) for m in re.finditer(rx, text)]
    src_nums = sorted(n.replace(",", ".") for n in tokens(r"\d+(?:[.,]\d+)?", source))
    dst_nums = sorted(n.replace(",", ".") for n in tokens(r"\d+(?:[.,]\d+)?", target))
    lost = list(src_nums)
    for n in dst_nums:
        if n in lost:
            lost.remove(n)
    if lost:
        problems.append("Пропали или изменились числа: " + ", ".join(sorted(set(lost))[:10]))
    for label, rx in (("email", EMAIL.pattern), ("ссылки", r"https?://\S+|t\.me/\S+"), ("никнеймы", r"@[A-Za-z0-9_]{4,}")):
        missing = [t for t in set(tokens(rx, source)) if t not in target]
        if missing:
            problems.append(f"Пропали {label}: " + ", ".join(missing[:5]))
    if target_lang == "en":
        target_words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.]*[A-Za-z0-9+#]|[A-Za-z]", target)}
        as_written = {w.lower(): w for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.]*[A-Za-z0-9+#]", source)}
        gone = sorted(as_written.get(w, w) for w in name_tokens(source) if w not in target_words and w not in target.lower())
        if gone:
            problems.append("Пропали названия: " + ", ".join(gone[:10]))
        cyr = [b["x"] for b in blocks(target) if re.search(r"[а-яё]{3,}", b["x"], re.I) and b["t"] != "h1"]
        if cyr:
            problems.append(f"Остался русский текст в {len(cyr)} строк(ах): «{cyr[0][:60]}»")
    s, t = blocks(source), blocks(target)
    for kind, name in (("h2", "разделов"), ("h3", "мест работы/учёбы"), ("li", "пунктов")):
        a, b = sum(1 for x in s if x["t"] == kind), sum(1 for x in t if x["t"] == kind)
        if a != b:
            problems.append(f"Разное число {name}: было {a}, стало {b}")
    return problems


# ---------------------------------------------------------------- export

def export_file(member, vid, fmt, workdir):
    """Write the version as .md / .html / .pdf into workdir. Returns the path (pdf: after node runs)."""
    meta, md = get_version(member, vid)
    base = re.sub(r"[^\w-]+", "_", (blocks(md)[0]["x"] if blocks(md) else "resume"))[:40].strip("_") or "resume"
    base = f"{base}_{re.sub(r'[^\w-]+', '_', meta['name'])[:20]}"
    workdir.mkdir(parents=True, exist_ok=True)
    if fmt == "md":
        path = workdir / f"{base}.md"
        path.write_text(md, encoding="utf-8")
        return path
    page = to_html(md, title=f"{meta['name']} — {base}", design=design_of(meta), photo=photo_uri(member))
    html_path = workdir / f"{base}.html"
    html_path.write_text(page, encoding="utf-8")
    return html_path


async def to_pdf(html_path, timeout=120):
    pdf = html_path.with_suffix(".pdf")
    proc = await asyncio.create_subprocess_exec("node", str(PDF_SCRIPT), str(html_path), str(pdf), cwd=str(tc.ROOT),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("PDF не собрался за 2 минуты")
    if proc.returncode != 0 or not pdf.exists():
        raise RuntimeError("PDF не собрался: " + err.decode("utf-8", "replace").strip()[-200:])
    return pdf


def import_legacy(member):
    """A résumé uploaded before versions existed (cv.txt) becomes «Основное»."""
    if load_index(member)["list"]:
        return
    old = cv_reader.cv_path(member)
    if old.exists():
        save_version(member, from_text(old.read_text(encoding="utf-8")), name="Основное")


def overview(member):
    """Everything the tab needs: versions, the active text as blocks, its check."""
    import_legacy(member)
    index = load_index(member)
    if not index["list"]:
        return {"versions": [], "active": None}
    try:
        meta, md = get_version(member)
    except ValueError:
        return {"versions": index["list"], "active": None}
    design = design_of(meta)
    photo = photo_uri(member)
    return {"versions": index["list"], "active": meta, "markdown": md, "blocks": blocks(md), "doctor": doctor(md),
            "design": {**design, "has_photo": bool(photo)},
            "paper": {"css": template_css(design), "html": render(md, design, photo, editable=True)},
            "templates": [{"id": k, "name": n, "about": a} for k, (n, a) in TEMPLATES.items()],
            "colors": COLORS}

