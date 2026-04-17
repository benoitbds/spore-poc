"""Generate SPORE's default Open Graph image + favicon assets.

Produces:
  - spore-web/public/og-default.png (1200x630 — OG / Twitter card)
  - spore-web/public/favicon.ico (multi-res ICO)
  - spore-web/public/apple-touch-icon.png (180x180)

The design is pure geometry / typography — no emoji fonts required,
no network fetches. Colours mirror the site's Tailwind palette
(ink-900, emerald-bio, cyan-bio, mist-*).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

WEB_PUBLIC = Path(__file__).resolve().parents[2] / "spore-web" / "public"

# ── Palette (hex → RGBA) ────────────────────────────────────────────
INK_900 = (7, 7, 10)
INK_700 = (17, 17, 22)
EMERALD_BIO = (16, 185, 129)
EMERALD_GLOW = (52, 211, 153)
CYAN_BIO = (6, 182, 212)
MIST_100 = (245, 245, 245)
MIST_300 = (209, 213, 219)
MIST_500 = (107, 114, 128)


FONT_TITLE = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _radial_glow(
    size: tuple[int, int],
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int],
    max_alpha: int = 110,
) -> Image.Image:
    """Return an RGBA layer with a soft radial glow centred on ``center``."""
    w, h = size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    steps = 40
    for i in range(steps, 0, -1):
        alpha = int(max_alpha * (i / steps) ** 2)
        r = int(radius * (i / steps))
        draw.ellipse(
            [center[0] - r, center[1] - r, center[0] + r, center[1] + r],
            fill=(*color, alpha),
        )
    # Soften the stepping
    return layer.filter(ImageFilter.GaussianBlur(radius=40))


def make_og_image() -> Image.Image:
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), INK_900)

    # Ambient glows — emerald top-left, cyan bottom-right
    img.paste(
        _radial_glow((W, H), (180, 120), 520, EMERALD_BIO, max_alpha=90),
        (0, 0),
        _radial_glow((W, H), (180, 120), 520, EMERALD_BIO, max_alpha=90),
    )
    img.paste(
        _radial_glow((W, H), (W - 120, H - 60), 480, CYAN_BIO, max_alpha=60),
        (0, 0),
        _radial_glow((W, H), (W - 120, H - 60), 480, CYAN_BIO, max_alpha=60),
    )

    draw = ImageDraw.Draw(img)

    # Top accent hairline
    for i in range(3):
        alpha = [255, 140, 60][i]
        draw.line(
            [(60, 38 + i), (W - 60, 38 + i)],
            fill=(*EMERALD_BIO, alpha),
            width=1,
        )

    # "SPORE" wordmark — large serif, tracked tight left-column only
    title_font = ImageFont.truetype(FONT_TITLE, 170)
    tag_font = ImageFont.truetype(FONT_SANS_BOLD, 34)
    sub_font = ImageFont.truetype(FONT_SANS, 24)
    url_font = ImageFont.truetype(FONT_SANS, 20)

    draw.text((72, 170), "SPORE", fill=MIST_100, font=title_font)

    # Hairline under wordmark
    draw.line([(80, 365), (280, 365)], fill=EMERALD_BIO, width=2)

    # Tagline (two lines, kept under ~700px wide to avoid the right graph)
    draw.text(
        (72, 385),
        "L'IA qui imagine",
        fill=EMERALD_GLOW,
        font=tag_font,
    )
    draw.text(
        (72, 425),
        "les découvertes de demain",
        fill=EMERALD_GLOW,
        font=tag_font,
    )
    # Sub-tagline
    draw.text(
        (72, 482),
        "Des hypothèses scientifiques inédites,",
        fill=MIST_300,
        font=sub_font,
    )
    draw.text(
        (72, 514),
        "vérifiées, vulgarisées.",
        fill=MIST_300,
        font=sub_font,
    )

    # Bottom URL
    draw.text(
        (72, H - 50),
        "spore-research.com",
        fill=MIST_500,
        font=url_font,
    )

    # Decorative right-side network — spore/bridge motif
    node_color = EMERALD_GLOW
    edge_color = (*EMERALD_BIO, 120)
    nodes: list[tuple[int, int, int]] = [
        (940, 180, 13),   # hub
        (1080, 120, 7),
        (1100, 260, 9),
        (1010, 320, 6),
        (900, 380, 8),
        (1000, 460, 10),
        (1110, 500, 7),
        (940, 530, 6),
    ]
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    edges = [
        (0, 1), (0, 2), (0, 3),
        (2, 3), (3, 4), (3, 5),
        (5, 6), (5, 7), (4, 7),
    ]
    for a, b in edges:
        x1, y1, _ = nodes[a]
        x2, y2, _ = nodes[b]
        odraw.line([(x1, y1), (x2, y2)], fill=edge_color, width=1)

    for x, y, r in nodes:
        odraw.ellipse([x - r, y - r, x + r, y + r], fill=(*node_color, 230))
        # Halo
        odraw.ellipse(
            [x - r * 2, y - r * 2, x + r * 2, y + r * 2],
            outline=(*node_color, 70),
            width=1,
        )

    img.paste(overlay, (0, 0), overlay)

    return img


def make_favicon() -> list[Image.Image]:
    """Return PIL images for each favicon size in an ICO bundle."""
    frames: list[Image.Image] = []
    for size in (16, 32, 48, 64):
        frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame)
        # Outer rounded-square background
        draw.rectangle([0, 0, size, size], fill=INK_700)
        # Central seed — stylised "S" via two arcs + dot
        cx, cy = size / 2, size / 2
        r = size / 2 - max(1, size // 12)
        # Dot centre
        dot_r = max(1, size // 10)
        draw.ellipse(
            [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=EMERALD_GLOW,
        )
        # Outer ring
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            outline=EMERALD_BIO, width=max(1, size // 16),
        )
        frames.append(frame)
    return frames


def main() -> None:
    WEB_PUBLIC.mkdir(parents=True, exist_ok=True)

    og = make_og_image()
    og.save(WEB_PUBLIC / "og-default.png", format="PNG", optimize=True)
    print(f"wrote {WEB_PUBLIC / 'og-default.png'}")

    frames = make_favicon()
    # Save the largest frame with `sizes` — PIL resamples down internally,
    # which gives better 16/32px output than starting from the 16px frame.
    largest = frames[-1]
    largest.save(
        WEB_PUBLIC / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )
    print(f"wrote {WEB_PUBLIC / 'favicon.ico'}")

    # Apple touch icon (180x180)
    apple = Image.new("RGBA", (180, 180), (0, 0, 0, 0))
    adraw = ImageDraw.Draw(apple)
    adraw.rectangle([0, 0, 180, 180], fill=INK_700)
    cx, cy = 90, 90
    r = 62
    adraw.ellipse(
        [cx - r, cy - r, cx + r, cy + r], outline=EMERALD_BIO, width=6,
    )
    adraw.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=EMERALD_GLOW)
    apple.save(WEB_PUBLIC / "apple-touch-icon.png", format="PNG", optimize=True)
    print(f"wrote {WEB_PUBLIC / 'apple-touch-icon.png'}")


if __name__ == "__main__":
    main()
