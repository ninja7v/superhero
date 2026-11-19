"""Draw docs/assets/demo.gif, the animated illustration at the top of the README.

    uv run python docs/assets/make_demo_gif.py

It is a drawing, not a screen recording: a stylised desktop where the laser follows the
user's gaze, a wink clicks, and a spoken AI request runs after a confirming wink.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 720, 420
FPS = 15
OUT = Path(__file__).with_name("demo.gif")

INK, SURFACE, RAISED, LINE = "#121419", "#1a1d24", "#232733", "#2a2f3b"
TEXT, MUTED, RED, BLUE = "#e8eaf0", "#8a91a3", "#ff2d2d", "#4c8dff"
FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu")


def font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for folder in FONT_DIRS:
        path = Path(folder) / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


F_SMALL, F_TEXT, F_BOLD, F_CAPTION = font(13), font(15), font(15, True), font(24, True)

WINDOW = (150, 70, 690, 360)
SEARCH = (170, 112, 540, 140)
PLAY = (560, 112, 670, 140)
CARDS = [(170 + i * 172, 160, 330 + i * 172, 330) for i in range(3)]


def ease(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def lerp(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def centre(box: tuple[int, int, int, int]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def desktop(draw: ImageDraw.ImageDraw, results: bool, pressed: bool) -> None:
    draw.rectangle((0, 0, W, H), fill="#0d0f13")
    draw.rectangle((0, 0, W, 26), fill="#08090c")
    draw.text((12, 6), "Activities", font=F_SMALL, fill=MUTED)
    draw.text((W - 60, 6), "10:42", font=F_SMALL, fill=MUTED)
    for i in range(4):  # the dock
        draw.rounded_rectangle((20, 70 + i * 62, 70, 120 + i * 62), 10, fill=SURFACE)
    x0, y0, x1, _y1 = WINDOW
    draw.rounded_rectangle(WINDOW, 10, fill=INK, outline=LINE)
    draw.rounded_rectangle((x0, y0, x1, y0 + 30), 10, fill=SURFACE)
    draw.rectangle((x0, y0 + 20, x1, y0 + 30), fill=SURFACE)
    title = "relaxing music - Videos" if results else "Videos"
    draw.text((x0 + 14, y0 + 7), title, font=F_SMALL, fill=MUTED)
    draw.rounded_rectangle(SEARCH, 8, fill=SURFACE, outline=LINE)
    draw.text(
        (SEARCH[0] + 12, SEARCH[1] + 6),
        "relaxing music" if results else "Search",
        font=F_TEXT,
        fill=TEXT if results else MUTED,
    )
    draw.rounded_rectangle(PLAY, 8, fill=BLUE if pressed else RAISED)
    draw.text((PLAY[0] + 34, PLAY[1] + 6), "Play", font=F_BOLD, fill=TEXT)
    names = ["Rain sounds", "Calm piano", "Ocean waves"]
    tints = ["#2b3550", "#3a2b50", "#2b5045"]
    for i, card in enumerate(CARDS):
        draw.rounded_rectangle(card, 8, fill=SURFACE)
        thumb = (card[0] + 10, card[1] + 10, card[2] - 10, card[1] + 100)
        draw.rounded_rectangle(thumb, 6, fill=tints[i] if results else RAISED)
        if results:
            draw.text((card[0] + 12, card[1] + 112), names[i], font=F_SMALL, fill=TEXT)
        bar = (card[0] + 12, card[1] + 134, card[2] - 40, card[1] + 142)
        draw.rounded_rectangle(bar, 4, fill=RAISED)


def face(draw: ImageDraw.ImageDraw, look: tuple[float, float], wink: float, mouth: float) -> None:
    """The user, as the webcam sees them: a small picture in the bottom-left corner."""
    box = (20, 316, 128, 404)
    draw.rounded_rectangle(box, 10, fill="#1c2230", outline=LINE)
    cx, cy = 74, 362
    draw.ellipse((cx - 30, cy - 36, cx + 30, cy + 36), fill="#d9a77f")
    dx, dy = (look[0] - 0.5) * 8, (look[1] - 0.5) * 6
    for side in (-1, 1):
        ex, ey = cx + side * 12, cy - 8
        if side == -1 and wink > 0:
            h = max(1.0, 7 * (1 - wink))
            draw.ellipse((ex - 7, ey - h / 2, ex + 7, ey + h / 2), fill="white")
        else:
            draw.ellipse((ex - 7, ey - 5, ex + 7, ey + 5), fill="white")
            draw.ellipse((ex - 3 + dx, ey - 3 + dy, ex + 3 + dx, ey + 3 + dy), fill="#3b2a20")
    open_h = 2 + 14 * mouth
    draw.ellipse((cx - 9, cy + 14 - open_h / 2, cx + 9, cy + 14 + open_h / 2), fill="#6b2a2a")


def laser(img: Image.Image, at: tuple[float, float], click: float) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    x, y = at
    if click > 0:  # a ripple where the wink clicked
        r = 10 + 30 * click
        g.ellipse(
            (x - r, y - r, x + r, y + r), outline=(255, 45, 45, int(200 * (1 - click))), width=3
        )
    g.ellipse((x - 14, y - 14, x + 14, y + 14), fill=(255, 45, 45, 60))
    g.ellipse((x - 8, y - 8, x + 8, y + 8), fill=(255, 45, 45, 255), outline="white", width=2)
    img.alpha_composite(layer)


def status(draw: ImageDraw.ImageDraw, text: str) -> None:
    if not text:
        return
    width = draw.textlength(text, font=F_TEXT) + 32
    x0 = (W - width) / 2
    draw.rounded_rectangle((x0, 34, x0 + width, 62), 14, fill="#000000", outline=RED)
    draw.text((x0 + 16, 40), text, font=F_TEXT, fill=TEXT)


def caption(draw: ImageDraw.ImageDraw, text: str, alpha: float) -> None:
    if alpha <= 0:
        return
    shade = int(255 * alpha)
    width = draw.textlength(text, font=F_CAPTION)
    draw.text(((W - width) / 2 + 60, 376), text, font=F_CAPTION, fill=(shade, shade, shade))


def frame(t: float) -> Image.Image:
    """The picture at t seconds. 0-3 s look to point, 3-4.5 s wink to click, then voice."""
    img = Image.new("RGBA", (W, H))
    draw = ImageDraw.Draw(img)
    play, search = centre(PLAY), centre(SEARCH)
    if t < 3:
        path = [(420.0, 300.0), (300.0, 200.0), (480.0, 240.0), play]
        seg = min(int(t), 2)
        point = lerp(path[seg], path[seg + 1], ease(t - seg))
    else:
        point = play
    if t >= 6.4:
        point = lerp(play, search, ease((t - 6.4) / 0.8))
    look = ((point[0] - 150) / 540, (point[1] - 70) / 290)
    wink = max(0.0, 1 - abs(t - 3.4) / 0.25) + max(0.0, 1 - abs(t - 8.6) / 0.25)
    click = 0.0
    for at in (3.45, 8.65):
        if at <= t < at + 0.6:
            click = (t - at) / 0.6
    mouth = 0.0
    if 4.6 <= t < 5.7:  # held open: voice on
        mouth = ease((t - 4.6) / 0.3) * (1 - ease((t - 5.4) / 0.3))
    elif 5.7 <= t < 6.8:  # talking
        mouth = 0.35 + 0.35 * abs(math.sin(t * 18))
    desktop(draw, results=t >= 9.0, pressed=3.45 <= t < 3.9)
    face(draw, look, min(wink, 1.0), mouth)
    for text, a, b in (
        ("●  Listening…   “computer, search YouTube for relaxing music”", 5.0, 6.9),
        ("Thinking about “search YouTube for relaxing music”…", 6.9, 7.6),
        ("open youtube.com/results?search_query=relaxing+music  ·  wink to run", 7.6, 8.7),
        ("Done", 8.7, 9.8),
    ):
        if a <= t < b:
            status(draw, text)
    laser(img, point, click)
    draw = ImageDraw.Draw(img)
    for text, a, b in (
        ("Look to point.", 0.0, 3.0),
        ("Wink to click.", 3.0, 4.5),
        ("Speak to command.", 4.5, 10.5),
    ):
        if a <= t < b:
            caption(draw, text, min(1.0, (t - a) / 0.3, (b - t) / 0.3))
    return img.convert("RGB")


def main() -> None:
    frames = [frame(i / FPS) for i in range(int(10.5 * FPS))]
    palette = frames[int(8 * FPS)].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    images = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    images[0].save(
        OUT, save_all=True, append_images=images[1:], duration=1000 // FPS, loop=0, optimize=True
    )
    print(f"{OUT} {OUT.stat().st_size // 1024} KiB, {len(images)} frames")


if __name__ == "__main__":
    main()
