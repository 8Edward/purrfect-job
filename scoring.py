"""A quick fit score for a vacancy from word overlap with the résumé: no AI, no tokens, instant.

It is a rough guide (how many of your skills the vacancy names, whether its title is a role you look for, any
red flags: office, one country, old), shown next to the ✨ AI score, which stays the careful one.
Score 1.0-5.0; `why` lists what moved it.
"""

import time
from datetime import datetime, timezone

import extras
import letters
import tg_collect as tc
import workmode

_cache = {}   # member id -> (time, context): the résumé is parsed once a minute, not for every card
CACHE_SECONDS = 60


def context(member):
    """What a score needs about the person: skills from the résumé, the role words of their feed, their country filter."""
    hit = _cache.get(member.id)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        text = extras.candidate_text(member)
    except Exception:
        text = ""
    skills = letters.parse_resume(text)["skills"] if text else []
    roles = tc.compile_role(member.cfg().get("keywords", {}).get("role"))
    ctx = {"skills": skills, "roles": roles, "country": extras.country_settings(member)}
    _cache[member.id] = (time.monotonic(), ctx)
    return ctx


def score(ctx, item):
    """-> {score, why[]} for one stored vacancy; score None when there is nothing to compare with (no résumé, no words)."""
    title = item.get("title") or ""
    text = f"{title}\n{item.get('company') or ''}\n{item.get('text') or ''}"
    if not ctx["skills"] and not ctx["roles"]:
        return {"score": None, "why": []}
    value, why = 2.0, []
    if ctx["roles"] and any(p.search(title) for p in ctx["roles"]):
        value += 1.5
        why.append("название совпадает с тем, что вы ищете")
    if ctx["skills"]:
        found = letters.matched_skills(ctx["skills"], text)
        thin = len(text) < 250  # a short card names few skills: the absence says little
        if found:
            value += 1.5 * min(1.0, len(found) / 3)
            why.append("ваши навыки: " + ", ".join(found[:5]))
        elif not thin:
            value -= 0.5
            why.append("из ваших навыков в описании ничего не названо")
    if workmode.not_remote(item):
        value -= 2.0
        why.append("не удалёнка")
    elif extras.hires_from(item, ctx["country"]["key"]) is False:
        value -= 1.5
        why.append("не берут из вашей страны")
    try:
        if (datetime.now(timezone.utc) - datetime.fromisoformat(item["date"])).days > extras.OLD_DAYS:
            value -= 0.3
            why.append("опубликована давно")
    except (KeyError, TypeError, ValueError):
        pass
    return {"score": round(max(1.0, min(5.0, value)), 1), "why": why}
