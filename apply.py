"""✉️ «Откликнуться»: the owner's one-tap response to a vacancy, always confirmed by them first.

What really works for free and without risking accounts:
- email (an address in the vacancy text): the letter + the résumé PDF are sent from the owner's Gmail over SMTP,
  once they put an app password into config.yml (`smtp:`); without it the page opens a ready mailto: draft and
  the PDF goes into the bot chat;
- a Telegram contact from a post (@hr): only a draft to copy, the chat link and the PDF in the bot chat
  (writing to strangers from a personal account is how accounts get limited);
- a site / hh.ru / LinkedIn: no open apply API, so: copy the letter, open the vacancy, the PDF is in the chat.
Whatever path is taken, the vacancy is marked «Откликнулся» in the tracker.

Nothing is sent without the owner's tap on «Отправить». `apply_dry_run: true` in config.yml (or JR_APPLY_DRY=1)
makes «Отправить» log instead of sending — for tests.
"""

import asyncio
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import extras
import resume

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.IGNORECASE)
TG_RE = re.compile(r"(?:(?<![\w@./])@|t\.me/)([a-z][a-z0-9_]{4,31})\b", re.IGNORECASE)
SKIP_MAIL = re.compile(r"(no-?reply|noreply|example\.|domain\.|\.(png|jpe?g|gif|webp)$)", re.IGNORECASE)
SKIP_TG = {"joinchat", "share", "addlist", "proxy", "iv", "s", "c"}


def contacts(item, text):
    """Where a response can go: emails and Telegram handles found in the vacancy, and its link."""
    blob = " ".join([text or "", item.get("text") or ""])
    mails = list(dict.fromkeys(m.group(0).rstrip(".").lower() for m in EMAIL_RE.finditer(blob) if not SKIP_MAIL.search(m.group(0))))
    chat = (item.get("chat") or "").lstrip("@").lower()
    tgs = list(dict.fromkeys(m.group(1) for m in TG_RE.finditer(blob)
                             if m.group(1).lower() not in SKIP_TG and m.group(1).lower() != chat
                             and not re.search(re.escape(m.group(1)) + r"\.[a-z]{2,}", blob, re.IGNORECASE)))
    return {"email": mails[:3], "tg": tgs[:3], "link": item.get("link") or ""}


def pick_version(member, lang):
    """The résumé version for this vacancy: one in its language if there is one, else the active one."""
    index = resume.load_index(member)
    same = [v for v in index["list"] if v.get("lang", "ru") == lang]
    active = next((v for v in index["list"] if v["id"] == index["active"]), None)
    if active and active.get("lang", "ru") == lang:
        return active
    return same[0] if same else active


def smtp_settings(cfg):
    s = cfg.get("smtp") or {}
    if s.get("user") and s.get("app_password"):
        return {"host": s.get("host", "smtp.gmail.com"), "port": int(s.get("port", 465)), "user": s["user"],
                "password": str(s["app_password"]).replace(" ", ""), "name": s.get("name", "")}
    return None


def dry_run(cfg):
    return bool(cfg.get("apply_dry_run")) or os.environ.get("JR_APPLY_DRY") == "1"


def send_email(smtp, to, subject, body, pdf_path):
    """One email with the résumé attached, from the owner's own mailbox (blocking: run in an executor)."""
    msg = EmailMessage()
    msg["From"] = formataddr((smtp["name"], smtp["user"])) if smtp["name"] else smtp["user"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if pdf_path:
        with open(pdf_path, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(pdf_path))
    with smtplib.SMTP_SSL(smtp["host"], smtp["port"], context=ssl.create_default_context(), timeout=60) as s:
        s.login(smtp["user"], smtp["password"])
        s.send_message(msg)


async def resume_pdf(member, vid):
    path = resume.export_file(member, vid, "pdf", __import__("guests").resume_dir(member) / "export")
    return await resume.to_pdf(path)


def subject_for(item, name, lang):
    title = item.get("title") or ("Vacancy" if lang == "en" else "Вакансия")
    return f"{title} — {name}" if lang == "en" else f"Отклик на вакансию «{title}» — {name}"


def mark_applied(member, key, how):
    inbox = member.load_inbox()
    item = inbox.get(key)
    if not item:
        return None
    if item.get("status") == "new":
        item["status"] = "taken"
    item["fav"] = True
    if (item.get("track") or {}).get("stage") in (None, ""):
        extras.set_stage(item, "applied", note=how)
    member.save_inbox(inbox)
    return item


async def dry_or_send(cfg, smtp, to, subject, body, pdf, log):
    if dry_run(cfg):
        log.info("apply DRY RUN: would email %s «%s» (%d chars, pdf %s)", to, subject, len(body), bool(pdf))
        return "dry"
    await asyncio.get_running_loop().run_in_executor(None, send_email, smtp, to, subject, body, str(pdf) if pdf else None)
    return "sent"
