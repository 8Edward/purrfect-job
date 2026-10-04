"""✨ AI help for résumés through Claude Code on the owner's subscription (`claude -p`).

No paid API: the bot runs the Claude Code that comes with the Claude desktop app, logged in
once by the owner (`claude.exe /login`). If it isn't logged in, every AI button says so and
the rule-based tools keep working.

What it does, always as PROPOSALS the person accepts one by one:
- improve: line-by-line rewrites (strong verbs, structure, brevity) — exact before/after pairs;
- summary: three variants of «О себе»;
- tailor: emphasis for one vacancy (rewrites + questions), never adding skills they lack;
- translate: an accurate RU<->EN translation saved as a new version, then fact-checked by rules;
- cover: a short cover letter for a vacancy.

Every rewrite is checked after the model: a change may not bring in numbers, tools or names
that the résumé (or the vacancy, for letters) doesn't contain. Those changes are dropped.

Privacy: the résumé text goes to Claude (Anthropic) under the owner's account. Guests get AI
only if the owner switches it on for them in 👥 Близкие.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from datetime import date
from pathlib import Path

import resume

log = logging.getLogger("job-radar.ai")

WORK_DIR = Path(os.environ.get("JR_AI_DIR", r"D:\tools\tmp\jobradar-ai"))
TIMEOUT = 240
STATUS_TTL = 300
DAILY = {"owner": 60, "guest": 12}
OPS = ("improve", "summary", "tailor", "translate", "cover")

RULES = """You are a meticulous résumé editor for a job seeker.
HARD RULES — breaking any of them makes the output useless:
1. Never invent facts. Do not add employers, job titles, dates, numbers, percentages, money, team sizes,
   tools, technologies, certificates, degrees or achievements that are not in the text you were given.
2. If a line would be stronger with a number the person did not give, keep the line without a number and
   ask for it in "questions" instead.
3. Keep the résumé's language (Russian stays Russian, English stays English) unless asked to translate.
4. Be concrete and concise: strong action verb first, what was done, with what, and the result that is
   already stated. No clichés (team player, ответственный, стрессоустойчивый...), no first person.
5. Output ONLY what was asked — no greetings, no explanations outside the requested format."""

GLOSSARY = """витрина данных = data mart; хранилище данных = data warehouse (DWH); выгрузка данных = data extraction;
сверка = reconciliation; отчётность = reporting; дашборд = dashboard; ad-hoc запросы = ad-hoc queries;
качество данных = data quality; источник данных / система-источник = source system; топ-менеджмент = top management;
бизнес-заказчик = business stakeholder; ТЗ / техническое задание = technical specification; требования = requirements;
внедрил = implemented; сопровождал = maintained; доработал = enhanced; ведущий = Lead; старший = Senior; младший = Junior;
стажёр = Intern; вуз = university; бакалавр = Bachelor's degree; магистр = Master's degree; специалист (степень) = Specialist degree;
наст. время / по настоящее время = Present; ООО = LLC; АО = JSC; ПАО = PJSC; МГТУ им. Н.Э. Баумана = Bauman Moscow State Technical University;
НИУ ВШЭ / ВШЭ = HSE University; МГУ = Lomonosov Moscow State University; млн руб. = million rubles (RUB ...M);
Почта России = Russian Post"""


def find_exe(configured=None):
    if configured and Path(configured).exists():
        return str(configured)
    # The Claude desktop app is an MSIX package: its %APPDATA% folder is virtual and only processes
    # inside the package (like Claude's own terminal) see it there. The bot, started by the task
    # scheduler, finds the real files under %LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming.
    # If the app was moved to another drive (Settings → Apps → Move), Windows keeps its data in
    # <drive>:\WpSystem\<user SID>\AppData\Local\Packages\Claude_*.
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    bases = {Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else None, Path.home() / "AppData" / "Roaming"}
    bases |= set((local / "Packages").glob("Claude_*/LocalCache/Roaming"))
    for drive in "CDEFGH":
        root = Path(f"{drive}:/WpSystem")
        if root.exists():
            bases |= set(root.glob("*/AppData/Local/Packages/Claude_*/LocalCache/Roaming"))
    bases -= {None}
    found = []
    for base in bases:
        root = base / "Claude" / "claude-code"
        # claude-code/<version>/claude.exe, and since the app update claude-code/<version>/<hash>/claude.exe
        for exe in list(root.glob("*/claude.exe")) + list(root.glob("*/*/claude.exe")):
            try:
                ver = exe.parent.name if exe.parent.parent == root else exe.parent.parent.name
                found.append((tuple(int(x) for x in ver.split(".")), exe))
            except ValueError:
                continue
    if found:
        return str(max(found)[1])
    return shutil.which("claude")


class Claude:
    """Runs `claude -p` with no tools, no project files and no saved session."""

    def __init__(self, exe=None):
        self.exe = find_exe(exe)
        self.lock = asyncio.Lock()  # one job at a time: it's one person's subscription
        self.checked = (0.0, "checking")  # (when, None = ready | "login" | "missing" | "error" | "checking")

    async def ask(self, system, prompt, timeout=TIMEOUT):
        if not self.exe or not Path(self.exe).exists():  # the app updated or moved to another drive
            self.exe = find_exe()
        if not self.exe:
            raise RuntimeError("missing")
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, TEMP=str(WORK_DIR.parent), TMP=str(WORK_DIR.parent))
        async with self.lock:
            proc = await asyncio.create_subprocess_exec(
                self.exe, "-p", "--output-format", "text", "--tools", "", "--no-session-persistence",
                "--setting-sources", "local", "--system-prompt", system,
                cwd=str(WORK_DIR), env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("timeout")
        text = out.decode("utf-8", "replace").strip()
        if "not logged in" in (text + err.decode("utf-8", "replace")).lower() or "/login" in text[:80]:
            self.checked = (time.monotonic(), "login")
            raise RuntimeError("login")
        if proc.returncode != 0:
            raise RuntimeError("error: " + (err.decode("utf-8", "replace") or text)[-300:])
        return text

    async def status(self):
        """None if ready, else "missing" / "login" / "error". Cached for a few minutes."""
        at, value = self.checked
        if at and time.monotonic() - at < STATUS_TTL:
            return value
        if not self.exe:
            value = "missing"
        else:
            try:
                await self.ask("Reply with the single word OK.", "ping", timeout=60)
                value = None
            except RuntimeError as e:
                value = str(e) if str(e) in ("login", "missing") else "error"
        self.checked = (time.monotonic(), value)
        return value


# ---------------------------------------------------------------- limits and permission

def allowed(member):
    return member.is_owner or bool(getattr(member, "info", {}).get("ai"))


def uses_left(member):
    cap = DAILY["owner" if member.is_owner else "guest"]
    used = member.load_state().get("ai_uses") or {}
    return cap - (used.get("n", 0) if used.get("date") == date.today().isoformat() else 0)


def count_use(member):
    used = member.load_state().get("ai_uses") or {}
    n = used.get("n", 0) if used.get("date") == date.today().isoformat() else 0
    member.update_state(ai_uses={"date": date.today().isoformat(), "n": n + 1})


# ---------------------------------------------------------------- parsing and fact guards

def parse_json(text):
    """The first JSON object in the model's answer (it sometimes wraps it in ``` fences)."""
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("bad-answer")
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        raise RuntimeError("bad-answer")


def numbers(text):
    return {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", text or "")}


def latin_names(text):
    """Tool and product names: PostgreSQL, ClickHouse, Visio, dbt-like words with capitals or digits.
    Ordinary words (and the capital that starts a line, months) are not names."""
    return resume.name_tokens(text)


def invented(new, allowed_text, check_latin=True):
    """What `new` brings that `allowed_text` doesn't have: numbers and tool/product names."""
    extra = sorted(numbers(new) - numbers(allowed_text))
    if check_latin:
        known = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.]*", allowed_text or "")}
        extra += sorted(w for w in latin_names(new) if w not in known)
    return extra


def norm_line(s):
    return re.sub(r"[\s*_]+", " ", (s or "").replace("ё", "е")).strip().lower()


def checked_changes(raw, md):
    """Keep model changes that point at a real line, really change it and invent nothing."""
    bl = resume.blocks(md)
    index = {norm_line(b["x"]): i for i, b in enumerate(bl)}
    out, dropped = [], 0
    for c in (raw or [])[:20]:
        before, after = str(c.get("before", "")).strip(), str(c.get("after", "")).strip()
        before = re.sub(r"^[-•#\s]+", "", before)
        after = re.sub(r"^[-•#\s]+", "", after)
        i = index.get(norm_line(before))
        if i is None or not after or norm_line(after) == norm_line(before):
            dropped += 1
            continue
        if invented(after, md):
            dropped += 1
            continue
        out.append({"index": i, "type": bl[i]["t"], "before": bl[i]["x"], "after": after,
                    "why": str(c.get("why", "")).strip()[:160]})
    return out, dropped


def questions(raw):
    out = []
    for q in (raw or [])[:8]:
        text = q.get("ask") if isinstance(q, dict) else q
        if text:
            out.append(str(text).strip()[:240])
    return out


# ---------------------------------------------------------------- operations

def change_format(lang):
    return f"""Return ONE JSON object:
{{"changes": [{{"before": "<one line copied EXACTLY from the résumé, without the leading '- ' or '#'>",
               "after": "<the improved line, same language, same kind of line>",
               "why": "<why, in Russian, max 12 words>"}}],
 "questions": ["<in Russian: a short question asking the person for a missing fact or number>"]}}
Up to 12 changes, only lines that really get better. Each "before" must match one line of the résumé exactly.
Résumé language: {'Russian' if lang == 'ru' else 'English'}."""


async def run(ai, op, md, vacancy="", lang=None, focus=""):
    """One AI operation on a résumé. Returns a dict for the app (see each op)."""
    src_lang = resume.detect_lang(md)
    if op == "improve":
        prompt = (f"Improve this résumé line by line.{(' Focus on: ' + focus) if focus else ''}\n"
                  f"{change_format(src_lang)}\n\nRÉSUMÉ (Markdown):\n{md}")
        data = parse_json(await ai.ask(RULES, prompt))
        changes, dropped = checked_changes(data.get("changes"), md)
        return {"changes": changes, "questions": questions(data.get("questions")), "dropped": dropped}

    if op == "summary":
        prompt = (f"Write 3 different variants of the «{'О себе' if src_lang == 'ru' else 'Summary'}» section for this résumé: "
                  "2–3 sentences each, built only from facts in the résumé (role, years, domain, tools, the strongest "
                  "stated result, what they look for if stated). Return ONE JSON object: {\"variants\": [\"...\", \"...\", \"...\"]}\n\n"
                  f"RÉSUMÉ:\n{md}")
        data = parse_json(await ai.ask(RULES, prompt))
        variants = [v.strip() for v in data.get("variants") or [] if isinstance(v, str) and v.strip()]
        clean = [v for v in variants if not invented(v, md)][:3]
        return {"variants": clean, "dropped": len(variants) - len(clean)}

    if op == "tailor":
        prompt = ("Adapt this résumé to the vacancy below: rephrase existing lines so the experience that matters for "
                  "this job comes first and uses the vacancy's wording where it is TRUE for the person. Never add a skill "
                  "or tool they don't list — if the vacancy asks for it, put a question instead. "
                  "Also propose a new «О себе» aimed at this vacancy (only facts from the résumé) as \"summary\".\n"
                  f"{change_format(src_lang)}\nAdd the key \"summary\": \"...\".\n\n"
                  f"VACANCY:\n{vacancy[:6000]}\n\nRÉSUMÉ:\n{md}")
        data = parse_json(await ai.ask(RULES, prompt))
        changes, dropped = checked_changes(data.get("changes"), md)
        summary = str(data.get("summary") or "").strip()
        if summary and invented(summary, md):
            summary, dropped = "", dropped + 1
        return {"changes": changes, "questions": questions(data.get("questions")), "summary": summary, "dropped": dropped}

    if op == "translate":
        target = lang or ("en" if src_lang == "ru" else "ru")
        if target == "en":
            how = ("Translate this Russian résumé into natural, professional English as used in international tech hiring.\n"
                   "- Keep EVERY line and the Markdown structure: same headings, same number of jobs and bullets, same order.\n"
                   "- Headings: Summary, Work Experience, Education, Skills, Languages, Projects, Courses & Certifications.\n"
                   "- Dates like «Март 2024 — наст. время» -> «Mar 2024 – Present».\n"
                   "- Past roles in past tense, the current role in present tense; strong action verbs; never «Responsible for».\n"
                   "- Keep all numbers exactly; money may be written as «RUB 50M+». Keep tool and product names as they are.\n"
                   "- Company names: keep the brand, translate the legal form (ООО -> LLC) and add a short English gloss in "
                   "parentheses only if the original line already explains what the company is.\n"
                   "- Person's name: use the Latin spelling found in their email/links if any, otherwise standard transliteration.\n"
                   f"Glossary:\n{GLOSSARY}\n")
        else:
            how = ("Translate this English résumé into natural, professional Russian as used by Russian-speaking tech companies.\n"
                   "- Keep EVERY line and the Markdown structure: same headings, same number of jobs and bullets, same order.\n"
                   "- Headings: О себе, Опыт работы, Образование, Навыки, Языки, Проекты, Курсы и сертификаты.\n"
                   "- Dates like «Mar 2024 – Present» -> «Март 2024 — наст. время». Keep numbers, tool and product names.\n")
        prompt = how + "Return ONLY the translated Markdown, nothing before or after it.\n\nRÉSUMÉ:\n" + md
        text = await ai.ask(RULES.replace("3. Keep the résumé's language", "3. Translate as asked; otherwise keep the language"), prompt)
        text = re.sub(r"^```(markdown|md)?\s*|```\s*$", "", text.strip()).strip()
        if len(resume.blocks(text)) < max(3, len(resume.blocks(md)) // 2):
            raise RuntimeError("bad-answer")
        return {"markdown": text + "\n", "lang": target, "problems": resume.translation_guard(md, text, target)}

    if op == "cover":
        target = lang or resume.detect_lang(vacancy or md)
        prompt = (f"Write a short cover letter ({'на русском' if target == 'ru' else 'in English'}, 120–180 words) for the vacancy "
                  "below, from the person whose résumé follows. Open with why this role fits, give 2 concrete proofs from the "
                  "résumé (their real numbers only), close with availability for a call. Say nothing about the company that "
                  "isn't in the vacancy text. Return ONE JSON object: {\"text\": \"...\"}\n\n"
                  f"VACANCY:\n{vacancy[:6000]}\n\nRÉSUMÉ:\n{md}")
        data = parse_json(await ai.ask(RULES, prompt))
        text = str(data.get("text") or "").strip()
        bad = invented(text, md + "\n" + vacancy, check_latin=False)
        return {"text": text, "warning": ("В письме есть цифры, которых нет в резюме: " + ", ".join(bad)) if bad else ""}

    raise ValueError("unknown op")


ERRORS = {
    "login": "ИИ не подключён: на компьютере владельца нужно один раз войти в Claude Code.",
    "missing": "ИИ не найден на компьютере владельца.",
    "timeout": "ИИ думал слишком долго. Попробуйте ещё раз.",
    "bad-answer": "ИИ ответил не по формату. Попробуйте ещё раз.",
}


def human_error(e):
    return ERRORS.get(str(e), "ИИ сейчас не отвечает. Попробуйте позже.")
