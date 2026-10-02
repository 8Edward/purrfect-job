"""Collect job posts from your Telegram chats into career-ops data/pipeline.md.

Reads every group/channel your account is in (including private ones), keeps
messages from the last `lookback_days` that look like job posts, saves each
one to jds/tg-*.md and adds a `- [ ] local:jds/...` line to the Pending section
of data/pipeline.md, so `/career-ops pipeline` evaluates it like any other JD.

Read-only: it never sends, reacts or marks anything as read.

Usage:
    python telegram/tg_collect.py            # collect
    python telegram/tg_collect.py --dry-run  # show matches, write nothing
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TG_DIR = ROOT / "telegram"
CONFIG_PATH = TG_DIR / "config.yml"
SESSION_PATH = TG_DIR / "tg"  # Telethon adds .session
STATE_PATH = ROOT / "data" / "tg-state.json"
PIPELINE_PATH = ROOT / "data" / "pipeline.md"
JDS_DIR = ROOT / "jds"

PENDING_HEADERS = ("## pending", "## pendientes")


# ---------------------------------------------------------------- filtering

def compile_patterns(patterns):
    return [re.compile(p, re.IGNORECASE) for p in patterns or []]


class JobFilter:
    def __init__(self, keywords, min_length):
        self.job = compile_patterns(keywords.get("job"))
        self.role = compile_patterns(keywords.get("role"))
        self.negative = compile_patterns(keywords.get("negative"))
        self.tags = {name: compile_patterns(p) for name, p in (keywords.get("tags") or {}).items()}
        self.min_length = min_length

    def is_job(self, text):
        if not text or len(text) < self.min_length:
            return False
        if any(p.search(text) for p in self.negative):
            return False
        return any(p.search(text) for p in self.job) and any(p.search(text) for p in self.role)

    def tags_for(self, text):
        return [name for name, pats in self.tags.items() if any(p.search(text) for p in pats)]


def fingerprint(text):
    """Same post reposted into several chats gets the same fingerprint."""
    norm = re.sub(r"\W+", " ", text.lower()).strip()
    return hashlib.sha1(norm[:600].encode("utf-8")).hexdigest()[:16]


def guess_title(text):
    for line in text.splitlines():
        line = re.sub(r"#\w+", "", line)  # hashtags
        line = re.sub(r"[^\w\s.,:()/+\-–—&'\"«»]", "", line)  # emoji and decorations
        line = re.sub(r"\s+", " ", line).strip(" -–—:*")
        if len(line) >= 4:
            return line[:90]
    return "Telegram job post"


def clean_cell(value):
    return re.sub(r"\s+", " ", str(value)).replace("|", "/").strip()


# ---------------------------------------------------------------- telegram

def message_link(entity, msg_id):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    if entity.__class__.__name__ == "Channel":  # private channel or supergroup
        return f"https://t.me/c/{entity.id}/{msg_id}"
    return None  # basic groups have no message links


def chat_matches(entity, title, names):
    username = (getattr(entity, "username", None) or "").lower()
    for name in names:
        n = str(name).lower().lstrip("@")
        if n == title.lower() or (username and n == username):
            return True
    return False


async def collect(cfg, job_filter, state, dry_run):
    from telethon import TelegramClient

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.get("lookback_days", 3))
    seen_fps = set(state.get("fingerprints", []))
    last_ids = state.setdefault("last_ids", {})
    found = []

    client = TelegramClient(str(SESSION_PATH), int(cfg["api_id"]), cfg["api_hash"])
    await client.start()  # first run asks for phone number and login code
    try:
        async for dialog in client.iter_dialogs():
            if not (dialog.is_group or dialog.is_channel):
                continue
            entity, title = dialog.entity, dialog.name or "?"
            if cfg.get("mode") == "list" and not chat_matches(entity, title, cfg.get("include", [])):
                continue
            if chat_matches(entity, title, cfg.get("exclude", [])):
                continue
            if dialog.date and dialog.date < cutoff:
                continue  # nothing new in this chat

            key = str(dialog.id)
            min_id = last_ids.get(key, 0)
            newest = min_id
            async for msg in client.iter_messages(entity, min_id=min_id):
                if msg.date < cutoff:
                    break
                newest = max(newest, msg.id)
                text = msg.message or ""
                if not job_filter.is_job(text):
                    continue
                fp = fingerprint(text)
                if fp in seen_fps:
                    continue
                seen_fps.add(fp)
                found.append({
                    "chat": title,
                    "chat_id": dialog.id,
                    "msg_id": msg.id,
                    "date": msg.date,
                    "link": message_link(entity, msg.id),
                    "text": text,
                    "tags": job_filter.tags_for(text),
                })
            if not dry_run:
                last_ids[key] = newest
    finally:
        await client.disconnect()

    state["fingerprints"] = sorted(seen_fps)[-5000:]
    found.sort(key=lambda j: j["date"])
    return found


# ---------------------------------------------------------------- output

def save_jd(job):
    JDS_DIR.mkdir(exist_ok=True)
    name = f"tg-{job['date']:%Y-%m-%d}-{abs(job['chat_id'])}-{job['msg_id']}.md"
    body = [
        f"# {guess_title(job['text'])}",
        "",
        f"- Source: Telegram — {job['chat']}",
        f"- Posted: {job['date']:%Y-%m-%d %H:%M} UTC",
        f"- Link: {job['link'] or 'n/a (basic group, no message links)'}",
        "",
        "---",
        "",
        job["text"],
        "",
    ]
    (JDS_DIR / name).write_text("\n".join(body), encoding="utf-8")
    return f"jds/{name}"


def pipeline_line(job, jd_path):
    note = job["link"] or f"tg chat {job['chat']}"
    if job["tags"]:
        note += " [" + ", ".join(job["tags"]) + "]"
    location = "Remote" if "remote" in job["tags"] else ""
    cells = [
        f"local:{jd_path}",
        f"TG: {clean_cell(job['chat'])}",
        clean_cell(guess_title(job["text"])),
    ]
    if location:
        cells.append(location)
    cells += [f"posted: {job['date']:%Y-%m-%d}", f"note: {clean_cell(note)}"]
    return "- [ ] " + " | ".join(cells)


def add_to_pipeline(lines):
    text = PIPELINE_PATH.read_text(encoding="utf-8") if PIPELINE_PATH.exists() else "# Pipeline\n\n## Pending\n\n## Processed\n"
    out, inserted = [], False
    for line in text.splitlines():
        out.append(line)
        if not inserted and line.strip().lower() in PENDING_HEADERS:
            out.append("")
            out.extend(lines)
            inserted = True
    if not inserted:
        out += ["", "## Pending", ""] + lines
    tmp = PIPELINE_PATH.with_suffix(".md.partial")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, PIPELINE_PATH)


# ---------------------------------------------------------------- main

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows console: don't crash on emoji
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print matches, write nothing")
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        sys.exit(f"Нет {CONFIG_PATH}. Скопируй config.example.yml в config.yml и впиши api_id / api_hash.")
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    job_filter = JobFilter(cfg.get("keywords", {}), cfg.get("min_length", 120))
    state = load_state()

    jobs = asyncio.run(collect(cfg, job_filter, state, args.dry_run))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    if args.dry_run:
        for job in jobs:
            print(f"- {job['chat']} | {guess_title(job['text'])} | {job['tags']} | {job['link']}")
        print(f"[{stamp}] dry run: {len(jobs)} job post(s), nothing written")
        return

    lines = [pipeline_line(job, save_jd(job)) for job in jobs]
    if lines:
        add_to_pipeline(lines)
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"[{stamp}] added {len(lines)} job post(s) to data/pipeline.md")
    for line in lines:
        print("  " + line)


if __name__ == "__main__":
    main()
