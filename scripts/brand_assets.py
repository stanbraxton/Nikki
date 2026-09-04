"""Generate Nikki's brand assets from the source avatar (Nicole cartoon).

Usage: python scripts/brand_assets.py <source_avatar.png>
Writes into public/: avatars/nikki.png, favicon.png, apple-touch-icon.png,
logo_dark.png, logo_light.png (Chainlit picks these up automatically).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PUBLIC = Path(__file__).resolve().parent.parent / "public"
FONT = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"


def circular(src: Image.Image, size: int) -> Image.Image:
    """Crop the (square) source to a transparent-background circle."""
    big = size * 4
    im = src.convert("RGBA").resize((big, big), Image.LANCZOS)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    im.putalpha(mask)
    return im.resize((size, size), Image.LANCZOS)


def logo(avatar: Image.Image, text_color: str, height: int = 128) -> Image.Image:
    """Horizontal lockup: circular avatar + 'Nikki' wordmark, transparent background."""
    scale = 4
    h = height * scale
    font = ImageFont.truetype(FONT, int(h * 0.62))
    tw = int(font.getlength("Nikki"))
    gap = int(h * 0.22)
    pad = int(h * 0.04)
    im = Image.new("RGBA", (h + gap + tw + pad, h), (0, 0, 0, 0))
    im.alpha_composite(circular(avatar, h), (0, 0))
    d = ImageDraw.Draw(im)
    bbox = d.textbbox((0, 0), "Nikki", font=font)
    y = (h - (bbox[3] - bbox[1])) // 2 - bbox[1]
    d.text((h + gap, y), "Nikki", font=font, fill=text_color)
    return im.resize((im.width // scale, im.height // scale), Image.LANCZOS)


def main() -> None:
    src = Image.open(sys.argv[1])
    (PUBLIC / "avatars").mkdir(parents=True, exist_ok=True)
    circular(src, 512).save(PUBLIC / "avatars" / "nikki.png")
    circular(src, 256).save(PUBLIC / "favicon.png")
    circular(src, 180).save(PUBLIC / "apple-touch-icon.png")
    logo(src, "#f5f5f7").save(PUBLIC / "logo_dark.png")
    logo(src, "#1c1c1e").save(PUBLIC / "logo_light.png")
    for p in sorted(PUBLIC.rglob("*.png")):
        print(p.relative_to(PUBLIC), Image.open(p).size)


if __name__ == "__main__":
    main()
