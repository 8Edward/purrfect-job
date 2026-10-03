"""Renders the bot's home-screen picture: a dark dashboard with live counters."""

import hashlib
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 640
BG_TOP, BG_BOTTOM = (10, 14, 32), (30, 20, 72)
WHITE, MUTED = (245, 247, 255), (150, 158, 196)
ACCENTS = {"new": (56, 189, 248), "taken": (52, 211, 153), "fav": (251, 191, 36)}

FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu"), Path("/System/Library/Fonts")]
FONT_FILES = {
    "bold": ["segoeuib.ttf", "DejaVuSans-Bold.ttf", "Helvetica.ttc"],
    "regular": ["segoeui.ttf", "DejaVuSans.ttf", "Helvetica.ttc"],
    "black": ["seguibl.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "Helvetica.ttc"],
}


def font(kind, size):
    for name in FONT_FILES[kind]:
        for d in FONT_DIRS:
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), size)
    return ImageFont.load_default(size)


def gradient():
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            t = min(1.0, (y / H) * 0.7 + (x / W) * 0.3)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
    return img


def glow(img, center, radius, color, alpha):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius], fill=(*color, alpha))
    return Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(radius // 2)))


def tile(img, box, number, label, accent):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=28, fill=(255, 255, 255, 16), outline=(255, 255, 255, 40), width=2)
    d.rounded_rectangle([box[0] + 28, box[1], box[0] + 108, box[1] + 6], radius=3, fill=(*accent, 255))
    img = Image.alpha_composite(img, layer)
    d = ImageDraw.Draw(img)
    d.text((box[0] + 28, box[1] + 30), str(number), font=font("black", 96), fill=(*accent, 255))
    d.text((box[0] + 30, box[3] - 58), label, font=font("regular", 30), fill=(*WHITE, 255))
    return img


def render_dashboard(stats, out_dir, subtitle="Удалённые вакансии · Data & Analytics"):
    """stats: new, taken, fav, chats, boards, checked_at (datetime|None). Returns a cached PNG path."""
    key = hashlib.sha1((repr(sorted(stats.items())) + subtitle).encode()).hexdigest()[:12]
    path = Path(out_dir) / f"dash-{key}.png"
    if path.exists():
        return path
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for old in Path(out_dir).glob("dash-*.png"):
        try:
            old.unlink()
        except OSError:  # still being sent by another message (Windows locks it); cleaned up next time
            pass

    img = gradient().convert("RGBA")
    img = glow(img, (1100, 80), 260, (124, 58, 237), 120)
    img = glow(img, (160, 600), 220, (56, 189, 248), 70)
    d = ImageDraw.Draw(img)

    d.ellipse([64, 70, 82, 88], fill=(52, 211, 153, 255))
    d.text((96, 62), "ONLINE", font=font("bold", 24), fill=(52, 211, 153, 255))
    d.text((60, 100), "Job Radar", font=font("black", 84), fill=(*WHITE, 255))
    d.text((64, 200), subtitle[:60], font=font("regular", 32), fill=(*MUTED, 255))

    checked = stats.get("checked_at")
    stamp = f"проверено {checked:%H:%M}" if checked else "ещё не проверял"
    f = font("regular", 26)
    d.text((W - 64 - d.textlength(stamp, font=f), 70), stamp, font=f, fill=(*MUTED, 255))

    tiles = [("new", stats["new"], "новых"), ("taken", stats["taken"], "в работе"), ("fav", stats["fav"], "в избранном")]
    gap, top, bottom = 32, 300, 540
    width = (W - 128 - gap * 2) // 3
    for i, (kind, number, label) in enumerate(tiles):
        x = 64 + i * (width + gap)
        img = tile(img, (x, top, x + width, bottom), number, label, ACCENTS[kind])

    d = ImageDraw.Draw(img)
    boards = f"{stats['boards']} сайтов с вакансиями" if stats["boards"] else "сайты выключены"
    footer = f"{stats['chats']} чатов в Telegram   ·   {boards}"
    d.text((64, 572), footer, font=font("regular", 26), fill=(*MUTED, 255))

    img.convert("RGB").save(path, "PNG", optimize=True)
    return path


if __name__ == "__main__":
    import sys

    print(render_dashboard({"new": 5, "taken": 12, "fav": 3, "chats": 7, "boards": 11,
                            "checked_at": datetime.now()}, sys.argv[1] if len(sys.argv) > 1 else "."))


# ---------------------------------------------------------------- 🐾 the chat's home: a cat radar animation

BRAND = "Purrfect Job"   # the word the radar finds at the end; config.yml `brand:` overrides it
ANIM_VERSION = 1
AW, AH = 480, 320
AC = (240, 150)          # radar centre
AR = 118                 # radar radius
A_BG = ((14, 38, 44), (32, 30, 58))
A_RING, A_SWEEP, A_GOLD, A_MINT, A_PINK = (88, 190, 170), (120, 230, 200), (242, 169, 59), (90, 214, 150), (255, 140, 160)
BLIPS = [  # (angle in degrees clockwise from north, distance 0..1, kind)
    (35, .55, "paw"), (80, .82, "dollar"), (125, .45, "whiskers"), (170, .75, "offer"), (215, .6, "tail"),
    (255, .38, "dollar"), (300, .7, "paw"), (335, .85, "offer"), (15, .3, "whiskers"),
]


def _bg():
    img = Image.new("RGB", (AW, AH))
    d = ImageDraw.Draw(img)
    for y in range(AH):
        t = y / AH
        d.line([(0, y), (AW, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(*A_BG)))
    return img.convert("RGBA")


def _pt(angle, dist):
    import math
    a = math.radians(angle - 90)
    return AC[0] + math.cos(a) * AR * dist, AC[1] + math.sin(a) * AR * dist


def _icon(d, kind, x, y, alpha, scale=1.0):
    c = lambda col: (*col, alpha)  # noqa: E731
    s = scale
    if kind == "paw":
        d.ellipse([x - 7 * s, y - 2 * s, x + 7 * s, y + 9 * s], fill=c(A_PINK))
        for dx, dy in ((-9, -7), (-3, -11), (3, -11), (9, -7)):
            d.ellipse([x + (dx - 3) * s, y + (dy - 3) * s, x + (dx + 3) * s, y + (dy + 3) * s], fill=c(A_PINK))
    elif kind == "whiskers":
        d.ellipse([x - 3 * s, y - 3 * s, x + 3 * s, y + 3 * s], fill=c(A_PINK))
        for dy in (-5, 0, 5):
            d.line([x - 5 * s, y, x - 18 * s, y + dy * s], fill=c((235, 240, 250)), width=2)
            d.line([x + 5 * s, y, x + 18 * s, y + dy * s], fill=c((235, 240, 250)), width=2)
    elif kind == "tail":
        d.arc([x - 14 * s, y - 14 * s, x + 14 * s, y + 14 * s], 200, 20, fill=c(A_GOLD), width=int(4 * s))
        d.ellipse([x + 9 * s, y - 9 * s, x + 16 * s, y - 2 * s], fill=c(A_GOLD))
    elif kind == "dollar":
        d.ellipse([x - 12 * s, y - 12 * s, x + 12 * s, y + 12 * s], fill=c(A_MINT))
        f = font("bold", int(18 * s))
        d.text((x, y), "$", font=f, fill=(14, 38, 44, alpha), anchor="mm")
    elif kind == "offer":
        d.rounded_rectangle([x - 13 * s, y - 16 * s, x + 13 * s, y + 16 * s], radius=3, fill=c((245, 247, 255)))
        for k in range(3):
            d.line([x - 8 * s, y + (-8 + k * 6) * s, x + 8 * s, y + (-8 + k * 6) * s], fill=c((150, 158, 196)), width=2)
        d.line([x - 6 * s, y + 9 * s, x - 1 * s, y + 13 * s, x + 8 * s, y + 4 * s], fill=c(A_MINT), width=3)


def _cat(d, x, y, s, alpha=255):
    col = (*A_GOLD, alpha)
    d.polygon([(x - 26 * s, y - 10 * s), (x - 20 * s, y - 40 * s), (x - 4 * s, y - 22 * s)], fill=col)
    d.polygon([(x + 26 * s, y - 10 * s), (x + 20 * s, y - 40 * s), (x + 4 * s, y - 22 * s)], fill=col)
    d.ellipse([x - 30 * s, y - 28 * s, x + 30 * s, y + 26 * s], fill=col)
    for ex in (-11, 11):
        d.ellipse([x + (ex - 5) * s, y - 8 * s, x + (ex + 5) * s, y + 4 * s], fill=(14, 38, 44, alpha))
    d.polygon([(x - 4 * s, y + 8 * s), (x + 4 * s, y + 8 * s), (x, y + 13 * s)], fill=(*A_PINK, alpha))
    for side in (-1, 1):
        for dy in (6, 12):
            d.line([x + side * 9 * s, y + 11 * s, x + side * 40 * s, y + dy * s], fill=(245, 247, 255, alpha), width=2)


def _frame(base, sweep, seen, final=0.0, brand=BRAND):
    img = base.copy()
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    box = [AC[0] - AR, AC[1] - AR, AC[0] + AR, AC[1] + AR]
    if final < 1:
        for k in range(16):  # the fading wedge behind the sweep line
            d.pieslice(box, sweep - 90 - (k + 1) * 3, sweep - 90 - k * 3, fill=(*A_SWEEP, int(70 * (1 - k / 16) * (1 - final))))
    for r in (1, .66, .33):
        d.ellipse([AC[0] - AR * r, AC[1] - AR * r, AC[0] + AR * r, AC[1] + AR * r], outline=(*A_RING, 110), width=2)
    d.line([AC[0] - AR, AC[1], AC[0] + AR, AC[1]], fill=(*A_RING, 60), width=1)
    d.line([AC[0], AC[1] - AR, AC[0], AC[1] + AR], fill=(*A_RING, 60), width=1)
    if final < 1:
        x, y = _pt(sweep, 1)
        d.line([AC, (x, y)], fill=(*A_SWEEP, 230), width=3)
    for angle, dist, kind in BLIPS:
        age = seen.get((angle, dist))
        if age is None:
            continue
        alpha = int(max(60, 255 - age * 9) * (1 - final))
        if alpha > 4:
            _icon(d, kind, *_pt(angle, dist), alpha)
    img = Image.alpha_composite(img, layer)
    if final > 0:
        top = Image.new("RGBA", img.size, (0, 0, 0, 0))
        t = ImageDraw.Draw(top)
        a = int(255 * final)
        t.ellipse([AC[0] - 54, AC[1] - 66, AC[0] + 54, AC[1] + 42], fill=(*A_MINT, int(60 * final)))
        _cat(t, AC[0], AC[1] - 14, 1.25 * (0.6 + 0.4 * final), a)
        f = font("black", 40)
        t.text((AW // 2, AH - 44), brand, font=f, fill=(*A_GOLD, a), anchor="mm", stroke_width=2, stroke_fill=(14, 38, 44, a))
        t.text((AW // 2, AH - 14), "found by Job Radar", font=font("regular", 15), fill=(235, 240, 250, int(a * .8)), anchor="mm")
        img = Image.alpha_composite(img, top)
    return img.convert("RGB")


def radar_animation(out_dir, brand=BRAND):
    """The home-screen animation (GIF, cached): the radar sweeps, paws, whiskers, tails, dollars and
    offers light up, then it finds «brand». Telegram plays a GIF as a silent looping video."""
    brand = (brand or BRAND)[:22]
    key = hashlib.sha1(f"{ANIM_VERSION}|{brand}".encode()).hexdigest()[:10]
    path = Path(out_dir) / f"radar-{key}.gif"
    if path.exists():
        return path
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    base = _bg()
    frames, durations, seen = [], [], {}
    for n in range(36):  # one full turn
        sweep = n * 10
        for k in seen:
            seen[k] += 1
        for angle, dist, _ in BLIPS:
            if (angle, dist) not in seen and (angle - sweep) % 360 < 10:
                seen[(angle, dist)] = 0
        frames.append(_frame(base, sweep, seen, brand=brand))
        durations.append(70)
    for n in range(1, 9):  # the find
        frames.append(_frame(base, 0, seen, final=n / 8, brand=brand))
        durations.append(60)
    durations[-1] = 2200
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    return path
