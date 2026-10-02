"""Job boards (career-ops scan.mjs) as a second source for the bot.

scan.mjs scans the boards in portals.yml, remembers what it saw in
data/scan-history.tsv and appends new offers to data/pipeline.md. The bot
takes those new lines back out of the pipeline and shows them as cards
instead; 👍 puts the line back, so the pipeline only holds what you chose.
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import tg_collect as tc

SITE_NAMES = {
    "weworkremotely.com": "We Work Remotely",
    "remoteok.com": "Remote OK",
    "remotive.com": "Remotive",
    "himalayas.app": "Himalayas",
    "workingnomads.com": "Working Nomads",
    "jobicy.com": "Jobicy",
    "nodesk.co": "NoDesk",
    "jobspresso.co": "Jobspresso",
    "news.ycombinator.com": "Hacker News",
    "4dayweek.io": "4 Day Week",
    "arbeitnow.com": "Arbeitnow",
}


def site_name(url):
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return SITE_NAMES.get(host, host)


def board_key(url):
    return "w_" + hashlib.sha1(url.encode()).hexdigest()[:12]


def parse_pipeline_line(line):
    """'- [ ] url | company | title | location | comp | posted: d | ...' -> dict."""
    cells = [c.strip() for c in re.sub(r"^- \[.\]\s*", "", line.strip()).split("|")]
    labeled = {}
    positional = []
    for cell in cells:
        m = re.match(r"^(posted|trust|note|rank):\s*(.*)$", cell)
        if m:
            labeled[m.group(1)] = m.group(2)
        else:
            positional.append(cell)
    positional += [""] * 5
    url, company, title, location, comp = positional[:5]
    return {"url": url, "company": company, "title": title, "location": location, "comp": comp, **labeled}


def take_from_pipeline(urls):
    """Remove the Pending lines for these URLs from pipeline.md and return them by URL."""
    if not urls or not tc.PIPELINE_PATH.exists():
        return {}
    wanted, taken, kept = set(urls), {}, []
    for line in tc.PIPELINE_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("- [ ] "):
            url = parse_pipeline_line(line)["url"]
            if url in wanted and url not in taken:
                taken[url] = line
                continue
        kept.append(line)
    if taken:
        tc.PIPELINE_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return taken


def board_job(url, line):
    info = parse_pipeline_line(line)
    try:
        date = datetime.fromisoformat(info["posted"]).replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        date = datetime.now(timezone.utc)
    return {
        "source": "web",
        "key": board_key(url),
        "title": info["title"] or "Вакансия",
        "company": info["company"],
        "location": info["location"],
        "comp": info["comp"],
        "site": site_name(url),
        "link": url,
        "date": date,
        "pipeline_line": line,
        "tags": [],
    }


async def scan_boards(timeout=600):
    """Run scan.mjs; returns (jobs, receipt). Raises RuntimeError if it fails."""
    proc = await asyncio.create_subprocess_exec(
        "node", "scan.mjs", "--json", "--quiet", cwd=str(tc.ROOT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("поиск по сайтам не уложился в 10 минут")
    lines = [l for l in out.decode("utf-8", "replace").splitlines() if l.startswith("{")]
    if not lines:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["нет вывода"]
        raise RuntimeError(f"scan.mjs: {tail[0][:200]}")
    receipt = json.loads(lines[-1])
    taken = take_from_pipeline(receipt.get("added_urls", []))
    return [board_job(url, line) for url, line in taken.items()], receipt
