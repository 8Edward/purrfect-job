"""A free public https address for the mini app: a Cloudflare «quick tunnel».

cloudflared (no account, no domain) forwards https://<random>.trycloudflare.com
to the app on this computer. The address is new every time the tunnel starts,
so the bot re-points its «Открыть» button whenever it changes. If cloudflared
dies, it is restarted. While the computer sleeps or is off, the app is offline.
"""

import asyncio
import logging
import re
import shutil
from pathlib import Path

log = logging.getLogger("job-radar.tunnel")

DEFAULT_EXE = Path(r"D:\tools\cloudflared\cloudflared.exe")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def find_cloudflared(configured=None):
    for candidate in (configured, DEFAULT_EXE, shutil.which("cloudflared")):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


async def run(port, on_url, exe=None, retry=30):
    """Keep a tunnel to 127.0.0.1:port up forever; call on_url(url) for each new address
    and on_url(None) when it drops."""
    exe = find_cloudflared(exe)
    if not exe:
        log.warning("cloudflared not found: the app works only on this computer (no public link)")
        return
    while True:
        proc = await asyncio.create_subprocess_exec(
            exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        url = None
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                m = URL_RE.search(line.decode("utf-8", "replace"))
                if m and m.group(0) != url:
                    url = m.group(0)
                    log.info("tunnel up: %s", url)
                    await on_url(url)
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        log.warning("tunnel stopped (code %s), restarting in %ds", proc.returncode, retry)
        await on_url(None)
        await asyncio.sleep(retry)
