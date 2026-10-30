"""A free public https address for the mini app: a Cloudflare «quick tunnel».

cloudflared (no account, no domain) forwards https://<random>.trycloudflare.com
to the app on this computer. A quick tunnel gets a new random address every time
cloudflared starts, and every old «Открыть» button then leads to an error page.

So cloudflared runs as its OWN process, detached from the bot: restarting the bot
(after an update) keeps the tunnel and its address. The bot only watches it: every
WATCH_EVERY seconds it asks cloudflared's local metrics page for the current address
(/quicktunnel) and starts cloudflared if nothing answers. The address changes only
when the computer reboots or cloudflared dies; then the bot re-points its buttons.
While the computer sleeps or is off, the app is offline. After a long sleep Cloudflare
may forget the tunnel («Tunnel not found»): cloudflared keeps running and reports the
old address, but it no longer exists. So the bot also checks /ready, and if the tunnel
has had no connection for DEAD_AFTER seconds it stops cloudflared and starts a new one.
"""

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen

log = logging.getLogger("job-radar.tunnel")

DEFAULT_EXE = Path(r"D:\tools\cloudflared\cloudflared.exe")
METRICS = "127.0.0.1:20241"
WATCH_EVERY = 30
DEAD_AFTER = 120
LOG_PATH = Path(__file__).resolve().parent / "data" / "tunnel.log"

# Windows: no console window, own process group, and out of the bot's job object, so
# stopping the bot's scheduled task doesn't take the tunnel (and its address) down too.
DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
BREAKAWAY = 0x01000000                            # CREATE_BREAKAWAY_FROM_JOB


def find_cloudflared(configured=None):
    for candidate in (configured, DEFAULT_EXE, shutil.which("cloudflared")):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def current_host():
    """The address cloudflared serves now: 'x.trycloudflare.com', '' while connecting,
    None if no cloudflared answers on the metrics port."""
    try:
        with urlopen(f"http://{METRICS}/quicktunnel", timeout=3) as r:
            return json.loads(r.read() or b"{}").get("hostname") or ""
    except Exception:
        return None


def is_ready():
    """True if cloudflared has a live connection to Cloudflare (/ready answers 200)."""
    try:
        with urlopen(f"http://{METRICS}/ready", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def stop_all():
    """Stop a cloudflared that runs but whose tunnel is gone."""
    if hasattr(subprocess, "CREATE_NO_WINDOW"):  # Windows
        cmd = ["taskkill", "/F", "/IM", "cloudflared.exe"]
    else:
        cmd = ["pkill", "-x", "cloudflared"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start(exe, port):
    LOG_PATH.parent.mkdir(exist_ok=True)
    args = [exe, "tunnel", "--no-autoupdate", "--metrics", METRICS, "--url", f"http://127.0.0.1:{port}"]
    out = open(LOG_PATH, "ab")
    kw = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out, "close_fds": True}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):  # Windows
        try:
            return subprocess.Popen(args, creationflags=DETACHED | BREAKAWAY, **kw)
        except OSError:  # the job forbids breaking away: still detached, just tied to the job
            return subprocess.Popen(args, creationflags=DETACHED, **kw)
    return subprocess.Popen(args, start_new_session=True, **kw)


async def run(port, on_url, exe=None):
    """Keep a tunnel to 127.0.0.1:port up forever; await on_url(url) whenever the
    address changes, and on_url(None) when the tunnel is gone."""
    exe = find_cloudflared(exe)
    if not exe:
        log.warning("cloudflared not found: the app works only on this computer (no public link)")
        return
    loop = asyncio.get_running_loop()
    url, started_at, dead_since = None, None, None
    while True:
        host = await loop.run_in_executor(None, current_host)
        if host is not None and not await loop.run_in_executor(None, is_ready):
            dead_since = dead_since or loop.time()
            if loop.time() - dead_since > DEAD_AFTER:
                log.warning("tunnel %s has no connection to Cloudflare, restarting cloudflared", host or "?")
                await loop.run_in_executor(None, stop_all)
                await asyncio.sleep(2)
                host, started_at, dead_since = None, None, None
        else:
            dead_since = None
        if host is None and (started_at is None or loop.time() - started_at > 60):
            log.info("no tunnel running, starting cloudflared")
            await loop.run_in_executor(None, start, exe, port)
            started_at = loop.time()
        new = f"https://{host}" if host else None
        if new != url and (new or host is None):  # '' = still connecting: keep the old address meanwhile
            url = new
            log.info("tunnel %s", f"up: {url}" if url else "down")
            await on_url(url)
        await asyncio.sleep(WATCH_EVERY if url and not dead_since else 5)
