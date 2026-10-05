"""⭐ Избранные компании: a company is «watched» when one of its vacancies is in the person's favourites
(or the person added it by hand); a NEW vacancy of a watched company raises an alarm (a block in the app +
one message in the bot chat). Rules only, no AI.

State (per person, `watch`): manual {key: name}, removed [keys], known {key: [inbox keys seen when the company
became watched — they are never «new»]}, alerted [inbox keys already announced in the chat].
"""

import re

import extras

LEGAL = {"inc", "llc", "ltd", "limited", "gmbh", "corp", "corporation", "co", "company", "plc", "sa", "bv", "ag", "oy",
         "pte", "pty", "ооо", "ао", "пао", "зао", "оао", "ип", "тоо", "llp", "lp", "the"}
MAX_MANUAL = 50
KEEP_ALERTED = 2000


def company_key(name):
    """«Nitka Technologies, Inc.» / «NITKA TECHNOLOGIES» -> «nitka technologies»."""
    words = [w for w in extras._norm(re.split(r"[.|(]\s", str(name or ""))[0].replace(",", " ")).split() if w not in LEGAL]
    return " ".join(words)


def load(state):
    w = state.get("watch") or {}
    return {"manual": dict(w.get("manual") or {}), "removed": list(w.get("removed") or []),
            "known": dict(w.get("known") or {}), "alerted": list(w.get("alerted") or [])}


def watched(inbox, w):
    """{company key: {"name", "auto", "manual"}}: companies of favourites (minus removed) + added by hand."""
    out = {}
    for item in inbox.values():
        key = company_key(item.get("company"))
        if item.get("fav") and key and key not in w["removed"]:
            out.setdefault(key, {"name": item.get("company"), "auto": True, "manual": False})
    for key, name in w["manual"].items():
        out.setdefault(key, {"name": name, "auto": False, "manual": True})["manual"] = True
    return out


def _same_as_fav(item, favs):
    t = extras.role_title(item.get("title"))
    return bool(t) and t in favs


def fresh(inbox, w, wanted):
    """[(inbox key, item)] new vacancies of watched companies, newest first."""
    favs = {}
    for item in inbox.values():
        if item.get("fav"):
            favs.setdefault(company_key(item.get("company")), set()).add(extras.role_title(item.get("title")))
    out = []
    for k, item in inbox.items():
        ck = company_key(item.get("company"))
        if ck not in wanted or k in w["known"].get(ck, []) or item.get("status", "new") != "new" or item.get("archived") or item.get("fav"):
            continue
        if _same_as_fav(item, favs.get(ck, set())):
            continue  # another city of the very vacancy that is already in favourites
        out.append((k, item))
    out.sort(key=lambda kv: str(kv[1].get("date")), reverse=True)
    return out


def sync(member):
    """Take a baseline for companies that just became watched. -> (watch state, watched, inbox)."""
    state = member.load_state()
    w = load(state)
    inbox = member.load_inbox()
    wanted = watched(inbox, w)
    changed = False
    for ck in wanted:
        if ck not in w["known"]:
            w["known"][ck] = [k for k, v in inbox.items() if company_key(v.get("company")) == ck]
            changed = True
    for ck in [c for c in w["known"] if c not in wanted]:
        del w["known"][ck]  # no longer watched: a later re-add starts from a fresh baseline
        changed = True
    if changed:
        member.update_state(watch=w)
    return w, wanted, inbox


def add(member, name):
    name = " ".join(str(name or "").split())[:80]
    key = company_key(name)
    if not key:
        raise ValueError("Не понял название компании")
    w, wanted, _ = sync(member)
    if len(w["manual"]) >= MAX_MANUAL and key not in w["manual"]:
        raise ValueError(f"Уже {MAX_MANUAL} компаний, лишнюю уберите")
    w["manual"][key] = name
    if key in w["removed"]:
        w["removed"].remove(key)
    member.update_state(watch=w)
    sync(member)
    return key


def remove(member, key):
    w, wanted, _ = sync(member)
    key = str(key or "")
    w["manual"].pop(key, None)
    if key in wanted and wanted[key]["auto"] and key not in w["removed"]:
        w["removed"].append(key)  # its favourite vacancy stays a favourite, the company is just not watched
    w["known"].pop(key, None)
    member.update_state(watch=w)


def overview(member):
    """What the app shows: companies with their new-vacancy counts + the new vacancies themselves."""
    w, wanted, inbox = sync(member)
    items = fresh(inbox, w, wanted)
    counts = {}
    for _, item in items:
        counts[company_key(item.get("company"))] = counts.get(company_key(item.get("company")), 0) + 1
    totals = {}
    for item in inbox.values():
        ck = company_key(item.get("company"))
        if ck in wanted and not item.get("archived"):
            totals[ck] = totals.get(ck, 0) + 1
    companies = [{"key": ck, "name": v["name"], "auto": v["auto"], "manual": v["manual"], "new": counts.get(ck, 0),
                  "total": totals.get(ck, 0)} for ck, v in wanted.items()]
    companies.sort(key=lambda c: (-c["new"], c["name"].lower()))
    return companies, items


def to_announce(member):
    """New vacancies of watched companies not yet announced in the chat (any hour: the owner asked for no quiet time).
    -> [(key, item)]; call mark_announced() after sending."""
    w, wanted, inbox = sync(member)
    return [(k, v) for k, v in fresh(inbox, w, wanted) if k not in w["alerted"]]


def mark_announced(member, keys):
    state = member.load_state()
    w = load(state)
    w["alerted"] = (w["alerted"] + [k for k in keys if k not in w["alerted"]])[-KEEP_ALERTED:]
    member.update_state(watch=w)
