"""📄 A résumé -> a proposed search preset. No AI service, no paid API: plain rules.

1. extract_text(): PDF (pypdf), DOCX (it's a zip of XML), TXT/MD.
2. analyze(): job titles from the role graph (rolegraph.yml) and skills/tools found in
   the text add up to a score per role; the strongest roles become the proposal.
3. words_for() / apply(): the person picks roles and channels, the bot writes their
   «Что ищу» words, hh.ru query and ticks. Nothing is applied without their tap.

Only the extracted text is kept (cv.txt in the person's folder), never the file itself,
and it can be deleted from the app at any time.
"""

import io
import re
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import guests
import tg_collect as tc
import webapp

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 60000
TYPES = (".pdf", ".docx", ".txt", ".md", ".rtf", ".doc")

# (label shown to the person, pattern, roles it points to, its words go into «Что ищу»)
SKILLS = [
    ("SQL", r"\bsql\b|postgres\w*|mysql|greenplum|clickhouse|oracle|ms sql|t-sql|pl/sql",
     ["data-analyst", "bi-analyst", "data-engineer", "analytics-engineer", "product-analyst"], True),
    ("Python", r"\bpython\b|pandas|numpy|jupyter", ["data-analyst", "data-scientist", "data-engineer"], True),
    ("Excel / Sheets", r"\bexcel\b|google sheets|гугл[- ]таблиц\w*|vba",
     ["data-analyst", "reporting-analyst", "financial-analyst", "accountant"], False),
    ("BI-инструменты", r"power ?bi|tableau|qlik\w*|superset|looker|datalens|metabase|luxms|redash|grafana",
     ["bi-analyst", "reporting-analyst", "data-analyst"], True),
    ("ETL / DWH", r"\betl\b|airflow|\bdbt\b|spark|kafka|hadoop|\bhive\b|data lake|\bdwh\b|хранилищ\w* данных|витрин\w* данных",
     ["data-engineer", "analytics-engineer"], True),
    ("Качество данных", r"data quality|качеств\w* данных|reconciliation|сверк\w*", ["data-quality"], False),
    ("Продуктовые метрики", r"a/?b[- ]?тест\w*|a/b[- ]test\w*|юнит[- ]экономик\w*|unit economics|retention|когорт\w*|cohort|воронк\w*|funnel|продуктов\w* метрик\w*",
     ["product-analyst"], False),
    ("ML", r"machine learning|машинн\w* обучени\w*|scikit|sklearn|pytorch|tensorflow|catboost|xgboost|\bnlp\b|computer vision",
     ["data-scientist", "ml-engineer"], True),
    ("Статистика", r"статистик\w*|statistic\w*|регресси\w*|regression|гипотез\w*", ["data-scientist", "data-analyst", "product-analyst"], False),
    ("Требования / BPMN", r"bpmn|\buml\b|требовани\w*|requirements|user stor\w*|use case\w*|техническ\w* задани\w*|\bбрд\b|\bbrd\b",
     ["business-analyst", "system-analyst", "requirements-analyst"], False),
    ("API и интеграции", r"\brest\b|swagger|openapi|\bsoap\b|интеграци\w*|\bapi\b", ["system-analyst"], False),
    ("Jira / Agile", r"jira|confluence|scrum|agile|kanban", ["project-manager", "product-manager", "business-analyst"], False),
    ("Продукт", r"roadmap|роадмап|бэклог|backlog|\bcjm\b|customer journey|discovery|product vision",
     ["product-manager", "product-analyst"], False),
    ("Финансы", r"мсфо|ifrs|бюджетир\w*|budgeting|\bp&l\b|финансов\w* модел\w*|financial model\w*|fp&a",
     ["financial-analyst"], True),
    ("1С / учёт", r"\b1[сc]\b|бухгалтерск\w*|налогов\w*|рсбу", ["accountant"], True),
    ("Дизайн", r"figma|sketch|photoshop|illustrator|\bux\b|\bui\b|прототип\w*|wireframe", ["designer", "graphic-designer"], True),
    ("Тестирование", r"тестировани\w*|selenium|postman|\bqa\b|test case\w*|тест[- ]кейс\w*|автотест\w*|autotest\w*",
     ["qa-engineer"], True),
    ("Подбор персонала", r"подбор\w* персонал\w*|рекрутинг|recruiting|sourcing|talent acquisition", ["recruiter"], True),
    ("Маркетинг-аналитика", r"google analytics|яндекс[. ]?метрик\w*|\bga4\b|\bseo\b|utm|атрибуци\w*|attribution",
     ["marketing-analyst"], True),
    ("Поддержка клиентов", r"поддержк\w* клиент\w*|customer support|zendesk|helpdesk|service desk", ["support"], False),
    ("C / C++", r"c\+\+|\bembedded c\b", ["software-engineer", "embedded-engineer", "robotics-engineer"], True),
    ("Робототехника", r"робототехн\w*|robotics|\bros ?2\b|\bros\b|moveit|motion planning|path planning|px4|ardupilot|mavros|"
     r"\bslam\b|gazebo|манипулятор\w*|беспилотн\w*|\buav\b|\bбпла\b", ["robotics-engineer"], True),
    ("Встраиваемые системы", r"embedded|встраиваем\w*|микроконтроллер\w*|stm32|firmware|прошивк\w*|\bmqtt\b|modbus|\buart\b|rs-?485|\brtos\b",
     ["embedded-engineer", "robotics-engineer"], True),
    ("Backend", r"\bgolang\b|\bgo\b developer|node\.?js|\bphp\b|laravel|symfony|django|flask|fastapi|spring( boot)?|\bjava\b|kotlin|\.net\b|asp\.net|c#|ruby|rails|\brust\b|\bscala\b|elixir|microservice\w*|микросервис\w*|grpc|rabbitmq",
     ["backend-developer", "software-engineer", "fullstack-developer"], True),
    ("Frontend", r"react(\.js)?\b|\bvue(\.js)?\b|angular|svelte|next\.js|javascript|typescript|\bhtml\b|\bcss\b|sass|webpack|vite\b|redux|вёрстк\w*|верстк\w*",
     ["frontend-developer", "fullstack-developer"], True),
    ("Мобильная разработка", r"\bios\b|android|swift(ui)?\b|flutter|react native|jetpack compose|xcode|objective-c",
     ["mobile-developer"], True),
    ("DevOps / облака", r"docker|kubernetes|\bk8s\b|terraform|ansible|jenkins|gitlab ci|github actions|ci/cd|\baws\b|\bazure\b|\bgcp\b|prometheus|helm\b|nginx|\bsre\b|devops",
     ["devops-engineer", "sysadmin"], True),
    ("Администрирование", r"\blinux\b|\bbash\b|active directory|windows server|vmware|cisco|\bnetwork\w*|zabbix|системн\w* администр\w*|сетев\w* инженер\w*",
     ["sysadmin", "it-support"], False),
    ("Базы данных (DBA)", r"\bdba\b|администрирован\w* баз\w*|репликаци\w*|replication|шардирован\w*|sharding|pg_?bouncer|query optimi[sz]ation|оптимизаци\w* запросов",
     ["dba"], False),
    ("Автотесты", r"selenium|playwright|cypress|pytest|junit|testng|appium|rest[- ]assured|автотест\w*|autotest\w*|test automation|\bsdet\b",
     ["qa-automation"], True),
    ("Безопасность", r"информационн\w* безопасност\w*|cyber ?security|infosec|pentest\w*|пентест\w*|owasp|siem|\bsoc\b|vulnerab\w*|уязвимост\w*|\bsast\b|\bdast\b",
     ["security-engineer"], True),
    ("Игры", r"unity|unreal( engine)?|godot|gameplay|shader\w*|\bhlsl\b|\bglsl\b|game ?dev\w*",
     ["game-developer"], True),
    ("Техническая документация", r"technical writ\w*|технич\w* писател\w*|документаци\w* (?:api|для разработчик\w*)|docs[- ]as[- ]code",
     ["tech-writer"], False),
    ("Scrum / agile-роли", r"scrum master|скрам[- ]мастер|sprint planning|ретроспектив\w*",
     ["scrum-master"], False),
    ("ИТ-поддержка", r"\bl[123] support\b|helpdesk|service desk|техподдержк\w*|техническ\w* поддержк\w*|\bitil\b",
     ["it-support"], False),    ("Управление командой", r"head of|team ?lead|тимлид|engineering manager|руководител\w* (?:отдел|разработ|команд|групп)\w*|"
     r"управлени\w* команд\w*|led a team|managed a team", ["engineering-manager"], False),
]
SKILL_RE = [(label, re.compile(p, re.IGNORECASE), roles, word) for label, p, roles, word in SKILLS]
GENERIC_WORDS = {"api", "rest", "soap", "ui", "ux", "qa", "seo", "utm", "1с", "1c",
                 "ros", "ros2", "ros 2", "uav", "бпла", "uart", "mqtt", "slam", "rtos", "head of", "rs-485", "rs485", "java", "html", "css", "ios", "docker", "linux", "aws", "azure", "gcp", "network", "react", "vue", "angular", "sre", "devops", "unity", "soc", "go", "rust"}

# Words in a pool channel's name or title that make it fit a role family. A channel of a family
# (analysts, designers…) is proposed only to that family; a general one («Remote IT») to everyone.
CHANNEL_HINTS = {
    "analytics": (["analyst", "analytic", "аналит", "data", "дата"],
                  {"data-analyst", "bi-analyst", "reporting-analyst", "product-analyst", "business-analyst",
                   "system-analyst", "requirements-analyst", "analytics-engineer", "data-engineer", "data-quality",
                   "data-scientist", "ml-engineer", "marketing-analyst", "financial-analyst", "dwh-analyst",
                   "fraud-analyst", "risk-analyst", "aml-analyst"}),
    "dev": (["python", "c++", "golang", "backend", "разработ", "программ", "embedded", "robot", "робот"],
            {"software-engineer", "embedded-engineer", "robotics-engineer", "ml-engineer", "engineering-manager",
             "data-engineer", "analytics-engineer", "backend-developer", "fullstack-developer", "mobile-developer",
             "frontend-developer", "game-developer", "devops-engineer", "qa-automation", "security-engineer"}),
    "web": (["frontend", "javascript", "php", "java", "react", "node"], {"frontend-developer", "fullstack-developer"}),
    "mobile": (["ios", "android", "mobile", "мобиль"], {"mobile-developer"}),
    "ops": (["devops", "sre", "sysadmin", "админ", "infra", "cloud"], {"devops-engineer", "sysadmin", "dba", "security-engineer", "it-support"}),
    "game": (["game", "gamedev", "игр", "unity"], {"game-developer"}),
    "sec": (["security", "infosec", "безопасн", "cyber"], {"security-engineer"}),
    "design": (["design", "дизайн"], {"designer", "graphic-designer"}),
    "qa": (["qa", "тест", "test"], {"qa-engineer", "qa-automation"}),
    "product": (["product", "продакт", "проджект"], {"product-manager", "project-manager", "product-analyst"}),
    "junior": (["junior", "джун", "стажир"], set()),  # for beginners: never proposed from a résumé
    "it": (["it", "remote", "career", "geek", "habr", "relocat", "удален", "удалён", "vakansii", "вакансии"], None),  # general
}
TITLE_WORD_RE = re.compile(r"\b(engineer|developer|programmer|analyst|scientist|manager|designer|architect|consultant|"
                           r"specialist|head of|director|researcher|lead|инженер|разработчик|программист|аналитик|менеджер|"
                           r"руководитель|дизайнер|архитектор|консультант|специалист|директор|бухгалтер|тестировщик|рекрутер|"
                           r"робототехник|devops|sre|frontend|backend|fullstack|full stack|full-stack|тимлид|разработчик|программист|администратор|surveyor|technician|technologist|operator|administrator|coordinator|assistant|"
                           r"officer|teacher|tutor|translator|interpreter|writer|editor|copywriter|marketer|lawyer|"
                           r"accountant|recruiter|nurse|doctor|геодезист|техник|технолог|оператор|администратор|"
                           r"координатор|ассистент|учитель|преподаватель|переводчик|редактор|копирайтер|маркетолог|"
                           r"юрист|врач)\w*", re.IGNORECASE)
ANALYSIS_VERSION = 2  # stored analyses older than this are re-read from the kept résumé text
OWN = "own:"  # a role the dictionary does not know, named by the résumé itself: "own:robotics engineer"
LEVEL_RE = re.compile(r"\b(junior|middle|senior|lead|джуниор|мидл|сеньор|ведущий|старший|младший)\b", re.IGNORECASE)
LEVEL_NAME = {"джуниор": "Junior", "младший": "Junior", "мидл": "Middle", "сеньор": "Senior", "старший": "Senior",
              "ведущий": "Lead"}
ENGLISH_RE = re.compile(r"(english|английск\w*)\W{0,3}(?:[^\n]{0,30}?)\b(a1|a2|b1|b2|c1|c2|native|fluent|upper[- ]intermediate|"
                        r"intermediate|advanced|pre[- ]intermediate)\b", re.IGNORECASE)


# ---------------------------------------------------------------- reading the file

def extract_text(filename, data):
    """The résumé's plain text. ValueError with a human message if it can't be read."""
    name = (filename or "").lower()
    if len(data) > MAX_BYTES:
        raise ValueError("Файл больше 5 МБ. Загрузите резюме поменьше (обычно оно весит до 1 МБ).")
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        text = _pdf_text(data)
    elif data[:5] == b"{\\rtf":  # hh.ru's «Word» download is RTF, whatever the extension
        text = _rtf_text(data)
    elif name.endswith(".docx") or (data[:2] == b"PK" and not name.endswith((".txt", ".md"))):
        text = _docx_text(data)
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", "replace") if _looks_utf8(data) else data.decode("cp1251", "replace")
    else:
        raise ValueError("Подойдёт PDF, DOCX, RTF или TXT. Старый .doc сохраните из Word как .docx.")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) < 200:
        raise ValueError("Почти не нашёл текста. Если PDF — это скан или картинка, сохраните резюме как текстовый PDF или DOCX.")
    return text[:MAX_CHARS]


def _looks_utf8(data):
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _pdf_text(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("PDF пока не читаю на этом компьютере. Загрузите DOCX или TXT.")
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:15])
    except Exception:
        raise ValueError("Не получилось прочитать PDF. Попробуйте DOCX или TXT.")


def _docx_text(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("Не получилось прочитать DOCX. Попробуйте PDF или TXT.")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(xml)
    paragraphs = []
    for p in root.iter(ns + "p"):
        paragraphs.append("".join(t.text or "" for t in p.iter(ns + "t")))
    return "\n".join(paragraphs)


RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "listtable", "listoverridetable",
            "rsidtbl", "generator", "xmlnstbl", "themedata", "colorschememapping", "latentstyles", "datastore"}
RTF_TOKEN = re.compile(r"\\([a-z]+)(-?\d+)? ?|\\'([0-9a-f]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]", re.IGNORECASE | re.DOTALL)


def _rtf_text(data):
    """Plain text of an RTF file: skips font/colour/picture groups, decodes \\'xx bytes in the
    file's code page and \\uN characters."""
    raw = data.decode("latin-1")
    cp = re.search(r"\\ansicpg(\d+)", raw)
    codepage = f"cp{cp.group(1)}" if cp else "cp1251"
    out, stack, skip, pending, eat_fallback = [], [], False, bytearray(), False

    def flush():
        if pending:
            out.append(pending.decode(codepage, "replace"))
            pending.clear()
    for m in RTF_TOKEN.finditer(raw):
        word, arg, hexa, sym, brace, text = m.groups()
        if hexa:
            if eat_fallback:
                eat_fallback = False
            elif not skip:
                pending.append(int(hexa, 16))
            continue
        flush()
        if brace == "{":
            stack.append(skip)
        elif brace == "}":
            skip = stack.pop() if stack else False
        elif sym:
            if sym == "*":
                skip = True
            elif not skip and sym in "\\{}":
                out.append(sym)
        elif word:
            if word in RTF_SKIP:
                skip = True
            elif skip:
                continue
            elif word in ("par", "line", "row"):
                out.append("\n")
            elif word in ("tab", "cell"):
                out.append(" ")
            elif word == "u" and arg:
                out.append(chr(int(arg) % 65536))
                eat_fallback = True  # the one ANSI character that follows \uN is its fallback
                continue
        elif text and not skip:
            if eat_fallback:
                text = text[1:]
            out.append(text)
        eat_fallback = False
    flush()
    return "".join(out)


# ---------------------------------------------------------------- analysis

def analyze(text, graph, pool):
    """Roles, skills and a proposed preset from the résumé text."""
    low = webapp.norm(text)
    scores, why = {}, {}

    def add(rid, points, reason):
        if rid in graph:
            scores[rid] = scores.get(rid, 0) + points
            why.setdefault(rid, [])
            if reason not in why[rid]:
                why[rid].append(reason)

    for rid, role in graph.items():  # the job titles themselves weigh most
        for name in role["names"]:
            rx = webapp.phrase_re(name) if len(name) > 3 else None
            hits = len(rx.findall(low)) if rx else 0
            if hits:
                add(rid, 5 * min(hits, 3), f"в резюме есть «{name}»")

    skills, groups = [], []  # groups: (roles of the skill, its words)
    for label, rx, roles, word in SKILL_RE:
        found = [m.group(0) for m in rx.finditer(low)]
        if not found:
            continue
        skills.append({"label": label, "count": len(found)})
        for rid in roles:
            add(rid, min(len(found), 3), f"навык: {label}")
        if word:  # the two most frequent tools of each skill, so one group doesn't crowd out the rest
            counts = {}
            for f in found:
                w = f.strip().lower()
                if len(w) >= 2 and w not in GENERIC_WORDS:
                    counts[w] = counts.get(w, 0) + 1
            groups.append((set(roles), [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:2]))

    # the title the résumé gives itself weighs most; one the dictionary doesn't know becomes the person's own role
    own = []
    for title in headline_titles(text):
        rid = webapp.find_role(graph, title)
        if rid:
            add(rid, 10, f"в резюме есть «{title}»")
        elif not any(webapp.norm(title) in webapp.norm(n) for r in graph.values() for n in r["names"]):
            own.append(title)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    # a role is proposed only when the résumé names it; tools alone (SQL, Python…) fit many jobs and used to
    # make everyone a «аналитик данных». Skill-only roles stay as unticked suggestions.
    named = [(rid, s) for rid, s in ranked if any(w.startswith("в резюме есть") for w in why.get(rid, []))]
    top = named[0][1] if named else 0
    primary = [rid for rid, s in named if s >= max(4, top * 0.5)][:3]
    nearby = []
    for rid in primary:
        for other, kind in graph[rid]["links"].items():
            if kind == "adj" and other not in primary and other not in nearby:
                nearby.append(other)
    shown = primary + [rid for rid, _ in ranked if rid not in primary][:3]
    shown += [rid for rid in nearby if rid not in shown][:3]

    words = []
    for roles, ws in sorted(groups, key=lambda g: not (g[0] & set(primary))):
        words += [w for w in ws if w not in words]
    levels = [LEVEL_NAME.get(m.group(1).lower(), m.group(1).capitalize()) for m in LEVEL_RE.finditer(text[:4000])]
    english = ENGLISH_RE.search(text)
    own_roles = [{"id": OWN + t, "label": t, "score": 10, "primary": len(primary) + i < 3, "why": ["заголовок резюме"]}
                 for i, t in enumerate(own)]
    return {
        "v": ANALYSIS_VERSION,
        "roles": [{"id": rid, "label": graph[rid]["names"][0], "score": scores.get(rid, 0),
                   "primary": rid in primary, "why": why.get(rid, ["смежная роль"])[:4]} for rid in primary]
        + own_roles
        + [{"id": rid, "label": graph[rid]["names"][0], "score": scores.get(rid, 0),
            "primary": False, "why": why.get(rid, ["смежная роль"])[:4]} for rid in shown if rid not in primary],
        "skills": skills,
        "skill_words": words[:8],  # the tools of the person's own roles first: a robotics engineer's C++ before SQL
        "level": max(set(levels), key=levels.count) if levels else None,
        "english": english.group(2).upper() if english and len(english.group(2)) == 2 else (english.group(2).capitalize() if english else None),
        "channels": suggest_channels(pool, primary + [OWN + t for t in own]),
        "chars": len(text),
    }


def suggest_channels(pool, roles):
    """Channels for these roles: their family's ones, plus general ones. Without a role, nothing:
    proposing every channel made everyone's setup the owner's (the pool is mostly analysts' channels)."""
    out, mine = [], set(roles)
    if not mine:
        return out
    for key, ch in pool["channels"].items():
        hay = webapp.norm(key + " " + (ch.get("title") or ""))
        families = [family for hints, family in CHANNEL_HINTS.values() if family is not None and any(h in hay for h in hints)]
        general = any(h in hay for h in CHANNEL_HINTS["it"][0])
        if any(f & mine for f in families) or (not families and general):
            out.append(key)
    return out


def headline_titles(text):
    """The job titles a résumé gives itself near the top («Senior Robotics Engineer / Head of Software
    Engineering» -> ["robotics engineer", "head of software engineering"]), level words dropped."""
    lines = [ln.strip(" \t•·|-—#*") for ln in str(text or "").splitlines() if ln.strip()][:8]
    for line in lines:
        if len(line) > 100 or "@" in line or not TITLE_WORD_RE.search(line):
            continue
        out = []
        for part in re.split(r"\s*[/|,;]\s*|\s+[-–—]\s+", line):
            part = re.sub(r"\s+", " ", LEVEL_RE.sub(" ", part)).strip(" .:-–—()").lower()
            if 4 <= len(part) <= 40 and len(part.split()) <= 5 and TITLE_WORD_RE.search(part) and part not in out:
                out.append(part)
        if out:
            return out[:3]
    return []


def role_words(graph, rids):
    """«Что ищу» words for the chosen roles: their names (both languages), no 1-3 letter ones."""
    words = []
    for rid in rids:
        if rid.startswith(OWN) and len(rid) > len(OWN) + 3 and rid[len(OWN):] not in words:
            words.append(rid[len(OWN):])
        for name in graph.get(rid, {}).get("names", [])[:4]:
            if len(name) > 3 and name.lower() not in words:
                words.append(name.lower())
    return words


def hh_query(graph, rids):
    names = []
    for rid in rids:
        role = graph.get(rid)
        if rid.startswith(OWN):
            names.append(rid[len(OWN):])
        if not role:
            continue
        ru = next((n for n in role["names"] if re.search(r"[а-яё]", n, re.I)), None)
        en = next((n for n in role["names"] if not re.search(r"[а-яё]", n, re.I) and len(n) > 3), None)
        names += [n for n in (ru, en) if n]
    return " OR ".join(f'"{n}"' for n in dict.fromkeys(names))[:guests.HH_QUERY_MAX]


# ---------------------------------------------------------------- storing and applying

def cv_path(member):
    return (tc.DATA_DIR / "tg-cv.txt") if member.is_owner else (member.dir / "cv.txt")


def save(member, filename, text, analysis):
    path = cv_path(member)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    member.update_state(cv={"file": (filename or "резюме")[:80], "at": datetime.now(timezone.utc).isoformat(),
                            "analysis": analysis})


def refresh(member, graph, pool):
    """Re-read a résumé analysed by an older version of this file (the kept text, same file name and
    date): new roles in the dictionary reach people who uploaded before. The feed is not touched."""
    info = member.load_state().get("cv")
    path = cv_path(member)
    if not info or (info.get("analysis") or {}).get("v", 1) >= ANALYSIS_VERSION or not path.exists():
        return info
    info = dict(info, analysis=analyze(path.read_text(encoding="utf-8"), graph, pool))
    member.update_state(cv=info)
    return info


def forget(member):
    path = cv_path(member)
    if path.exists():
        path.unlink()
    state = member.load_state()
    state.pop("cv", None)
    member.save_state(state)


def apply(member, graph, rids, channels, mode, skill_words=True):
    """Write the chosen preset: «Что ищу» words (replace or add), hh.ru query + tick, channel ticks."""
    info = member.load_state().get("cv") or {}
    analysis = info.get("analysis") or {}
    rids = [r for r in rids if r in graph or (r.startswith(OWN) and 4 <= len(r) - len(OWN) <= 40)][:6]
    if not rids:
        raise ValueError("Отметьте хотя бы одну роль.")
    words = role_words(graph, rids) + (analysis.get("skill_words", []) if skill_words else [])
    search = member.load_search()
    kw = search.setdefault("keywords", {})
    preset = guests.load_preset()
    untouched = {p.lower() for p in kw.get("role", [])} == {p.lower() for p in preset.get("role", [])}
    state = member.load_state()
    if mode == "replace" or untouched:  # still the newcomer example (Data Analyst): their own résumé replaces it
        kw["role"] = []
    elif mode == "sync":  # live from the role chips: swap what the résumé added last time, keep typed words
        mine = {w.lower() for w in state.get("cv_words") or []}
        kw["role"] = [w for w in kw.get("role", []) if w.lower() not in mine]
    before = {w.lower() for w in kw.get("role", [])}
    added, _ = guests.add_words(search, "role", ", ".join(words))
    member.save_search(search)
    member.update_state(cv_words=[w for w in kw.get("role", []) if w.lower() not in before])
    query = hh_query(graph, rids)
    if query:
        guests.set_hh(member, query=query)
        listed = guests.listed_sites()
        if guests.HH_ID not in member.sites(listed):
            member.set_sites(member.sites(listed) + [guests.HH_ID])
    pool = guests.load_pool()
    if channels is not None:
        ticked = member.selected(pool)
        if untouched and set(ticked) == set(preset.get("pool", [])) & set(pool["channels"]):
            ticked = []  # the example's channels were never changed: keep only what fits this résumé
        if mode == "sync":  # channels the résumé ticked before and the person unticked now go away
            gone = set(state.get("cv_channels") or []) - set(channels)
            ticked = [k for k in ticked if k not in gone]
        new = [k for k in channels if k in pool["channels"] and k not in ticked]
        member.set_selected(ticked + new)
        member.update_state(cv_channels=list(dict.fromkeys((state.get("cv_channels") or []) + new)) if mode == "sync" else new)
    return {"words": len(kw.get("role", [])), "added": len(added), "hh_query": query}
