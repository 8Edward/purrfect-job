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
        old.unlink()

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
