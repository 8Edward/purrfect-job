"""«Tier B»: remote companies from remoteintech/remote-jobs that hire worldwide / in Asia-Pacific
(telegram/tierb.yml, built by tierb-build.mjs). The bot remembers each such company once one of its vacancies
reached someone's feed: data/tg-tierb-seen.json {company: {first, last, n}}."""

import json
import os
import re
from datetime import datetime, timezone

import yaml

import tg_collect as tc

LIST_PATH = tc.TG_DIR / "tierb.yml"
SEEN_PATH = tc.DATA_DIR / "tg-tierb-seen.json"
_names = None


def _norm(name):
    return re.sub(r"[^a-z0-9а-я]+", "", str(name or "").lower())


def names():
    """{normalized name: name as listed}, read once."""
    global _names
    if _names is None:
        try:
            data = yaml.safe_load(LIST_PATH.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        _names = {_norm(c["name"]): c["name"] for c in data.get("companies") or [] if isinstance(c, dict) and c.get("name")}
    return _names


def is_tierb(company):
    return bool(company) and _norm(company) in names()


def load_seen():
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8")) if SEEN_PATH.exists() else {}
    except ValueError:
        return {}


def remember(items):
    """New feed items -> count the tier B companies among them. Returns how many items were tier B."""
    hits = [names()[_norm(i.get("company"))] for i in items if is_tierb(i.get("company"))]
    if not hits:
        return 0
    seen, now = load_seen(), datetime.now(timezone.utc).isoformat(timespec="seconds")
    for name in hits:
        row = seen.setdefault(name, {"first": now, "n": 0})
        row["last"], row["n"] = now, row["n"] + 1
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_PATH.with_suffix(".partial")
    tmp.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, SEEN_PATH)
    return len(hits)
