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
ANIM_VERSION = 2
OUT_W, OUT_H = 640, 400  # the GIF; frames are drawn twice as big and scaled down, so edges stay smooth
K = 2                    # drawing scale
AW, AH = OUT_W * K, OUT_H * K
AC = (AW // 2, 186 * K)  # radar centre
AR = 150 * K             # radar radius
A_BG = ((14, 38, 44), (32, 30, 58))
A_RING, A_SWEEP, A_MINT, A_PINK, A_WHITE = (88, 190, 170), (120, 230, 200), (90, 214, 150), (255, 140, 160), (238, 242, 250)
CAT, CAT_DARK, CAT_LIGHT, CAT_EYE = (138, 146, 158), (96, 104, 116), (196, 202, 212), (120, 214, 160)
BLIPS = [  # (angle in degrees clockwise from north, distance 0..1 — icons stay well inside the rim, kind)
    (40, .55, "paw"), (110, .62, "dollar"), (175, .45, "offer"), (235, .6, "whiskers"), (300, .5, "dollar"), (345, .66, "tail"),
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


def _icon(d, kind, x, y, alpha):
    c = lambda col: (*col, alpha)  # noqa: E731
    s = 1.5 * K
    if kind == "paw":
        d.ellipse([x - 7 * s, y - 2 * s, x + 7 * s, y + 9 * s], fill=c(A_PINK))
        for dx, dy in ((-9, -7), (-3, -11), (3, -11), (9, -7)):
            d.ellipse([x + (dx - 3) * s, y + (dy - 3) * s, x + (dx + 3) * s, y + (dy + 3) * s], fill=c(A_PINK))
    elif kind == "whiskers":
        d.ellipse([x - 3 * s, y - 3 * s, x + 3 * s, y + 3 * s], fill=c(A_PINK))
        for dy in (-5, 0, 5):
            d.line([x - 5 * s, y, x - 18 * s, y + dy * s], fill=c(A_WHITE), width=int(1.5 * K))
            d.line([x + 5 * s, y, x + 18 * s, y + dy * s], fill=c(A_WHITE), width=int(1.5 * K))
    elif kind == "tail":
        d.arc([x - 14 * s, y - 14 * s, x + 14 * s, y + 14 * s], 200, 20, fill=c(CAT_LIGHT), width=int(4 * s))
        d.ellipse([x + 9 * s, y - 9 * s, x + 16 * s, y - 2 * s], fill=c(CAT_LIGHT))
    elif kind == "dollar":
        d.ellipse([x - 12 * s, y - 12 * s, x + 12 * s, y + 12 * s], fill=c(A_MINT))
        d.text((x, y + 1 * s), "$", font=font("bold", int(17 * s)), fill=(14, 38, 44, alpha), anchor="mm")
    elif kind == "offer":
        d.rounded_rectangle([x - 13 * s, y - 16 * s, x + 13 * s, y + 16 * s], radius=int(3 * s), fill=c(A_WHITE))
        for k in range(3):
            d.line([x - 8 * s, y + (-8 + k * 6) * s, x + 8 * s, y + (-8 + k * 6) * s], fill=c((150, 158, 196)), width=int(1.5 * K))
        d.line([x - 6 * s, y + 9 * s, x - 1 * s, y + 13 * s, x + 8 * s, y + 4 * s], fill=c(A_MINT), width=int(2.5 * K))


def _cat(d, x, y, s, alpha=255):
    """A grey cat's face: ears with pink insides, green eyes, pink nose, white whiskers."""
    a = lambda col: (*col, alpha)  # noqa: E731
    for side in (-1, 1):
        d.polygon([(x + side * 27 * s, y - 8 * s), (x + side * 21 * s, y - 42 * s), (x + side * 4 * s, y - 24 * s)], fill=a(CAT))
        d.polygon([(x + side * 22 * s, y - 14 * s), (x + side * 20 * s, y - 33 * s), (x + side * 10 * s, y - 23 * s)], fill=a(A_PINK))
    d.ellipse([x - 32 * s, y - 30 * s, x + 32 * s, y + 28 * s], fill=a(CAT))
    d.ellipse([x - 14 * s, y + 2 * s, x + 14 * s, y + 24 * s], fill=a(CAT_LIGHT))  # muzzle
    for side in (-1, 1):  # forehead stripes
        d.line([x + side * 6 * s, y - 28 * s, x + side * 4 * s, y - 18 * s], fill=a(CAT_DARK), width=max(2, int(3 * s)))
    d.line([x, y - 29 * s, x, y - 17 * s], fill=a(CAT_DARK), width=max(2, int(3 * s)))
    for ex in (-12, 12):
        d.ellipse([x + (ex - 7) * s, y - 10 * s, x + (ex + 7) * s, y + 3 * s], fill=a(CAT_EYE))
        d.ellipse([x + (ex - 2) * s, y - 9 * s, x + (ex + 2) * s, y + 2 * s], fill=a((20, 26, 30)))
        d.ellipse([x + (ex - 4.5) * s, y - 8 * s, x + (ex - 2) * s, y - 5 * s], fill=a(A_WHITE))
    d.polygon([(x - 4 * s, y + 7 * s), (x + 4 * s, y + 7 * s), (x, y + 12 * s)], fill=a(A_PINK))
    d.line([x, y + 12 * s, x - 4 * s, y + 16 * s], fill=a(CAT_DARK), width=max(2, int(2 * s)))
    d.line([x, y + 12 * s, x + 4 * s, y + 16 * s], fill=a(CAT_DARK), width=max(2, int(2 * s)))
    for side in (-1, 1):
        for dy in (8, 14):
            d.line([x + side * 12 * s, y + 12 * s, x + side * 44 * s, y + dy * s], fill=a(A_WHITE), width=max(2, int(1.6 * s)))


def _frame(base, sweep, seen, final=0.0, brand=BRAND):
    img = base.copy()
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    box = [AC[0] - AR, AC[1] - AR, AC[0] + AR, AC[1] + AR]
    if final < 1:
        for k in range(20):  # the fading trail behind the sweep line
            d.pieslice(box, sweep - 90 - (k + 1) * 2.5, sweep - 90 - k * 2.5, fill=(*A_SWEEP, int(60 * (1 - k / 20) * (1 - final))))
    for r in (1, .66, .33):
        d.ellipse([AC[0] - AR * r, AC[1] - AR * r, AC[0] + AR * r, AC[1] + AR * r], outline=(*A_RING, 110), width=2 * K)
    d.line([AC[0] - AR, AC[1], AC[0] + AR, AC[1]], fill=(*A_RING, 55), width=K)
    d.line([AC[0], AC[1] - AR, AC[0], AC[1] + AR], fill=(*A_RING, 55), width=K)
    if final < 1:
        x, y = _pt(sweep, 1)
        d.line([AC, (x, y)], fill=(*A_SWEEP, 230), width=3 * K)
    for angle, dist, kind in BLIPS:
        age = seen.get((angle, dist))
        if age is None:
            continue
        alpha = int(min(255, 60 + age * 50) if age < 4 else max(70, 255 - (age - 4) * 4))  # fade in, then glow down slowly
        alpha = int(alpha * (1 - final))
        if alpha > 4:
            _icon(d, kind, *_pt(angle, dist), alpha)
    img = Image.alpha_composite(img, layer)
    if final > 0:
        top = Image.new("RGBA", img.size, (0, 0, 0, 0))
        t = ImageDraw.Draw(top)
        a = int(255 * final)
        t.ellipse([AC[0] - 70 * K, AC[1] - 80 * K, AC[0] + 70 * K, AC[1] + 60 * K], fill=(*A_MINT, int(45 * final)))
        _cat(t, AC[0], AC[1] - 8 * K, 1.6 * K * (0.7 + 0.3 * final), a)
        t.text((AW // 2, AH - 34 * K), brand, font=font("black", 44 * K), fill=(*A_WHITE, a), anchor="mm")
        img = Image.alpha_composite(img, top)
    return img.convert("RGB").resize((OUT_W, OUT_H), Image.LANCZOS)


def radar_animation(out_dir, brand=BRAND):
    """The home-screen animation (GIF, cached): the radar sweeps slowly, paws, whiskers, a tail, dollars and
    an offer light up one by one, then a grey cat appears with «brand». Telegram plays it as a looping video."""
    brand = (brand or BRAND)[:22]
    key = hashlib.sha1(f"{ANIM_VERSION}|{brand}".encode()).hexdigest()[:10]
    path = Path(out_dir) / f"radar-{key}.gif"
    if path.exists():
        return path
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    base = _bg()
    frames, durations, seen = [], [], {}
    for n in range(72):  # one slow turn: 5° a frame
        sweep = n * 5
        for k in seen:
            seen[k] += 1
        for angle, dist, _ in BLIPS:
            if (angle, dist) not in seen and (angle - sweep) % 360 < 5:
                seen[(angle, dist)] = 0
        frames.append(_frame(base, sweep, seen, brand=brand))
        durations.append(60)
    for n in range(1, 13):  # the find
        frames.append(_frame(base, 0, seen, final=n / 12, brand=brand))
        durations.append(55)
    durations[-1] = 2600
    # one shared palette for every frame: no colour flicker between frames, clean flat edges
    sheet = Image.new("RGB", (OUT_W, OUT_H * 3))
    for i, f in enumerate((frames[20], frames[60], frames[-1])):
        sheet.paste(f, (0, OUT_H * i))
    palette = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    gif[0].save(path, save_all=True, append_images=gif[1:], duration=durations, loop=0, optimize=False, disposal=1)
    return path
