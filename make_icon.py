"""Build the Windows icon (web/voicecast.ico) from the studio logo.

The logo has a wide dark margin, so it is cropped around the microphone and
waves, placed on a rounded dark tile and saved at every size Windows uses
(16 px in lists up to 256 px for large desktop icons).

    python make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent
LOGO = ROOT / "web" / "logo.png"
ICON = ROOT / "web" / "voicecast.ico"

ICON_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
CROP_BOX = (122, 110, 902, 890)   # square around the mic and waves in the 1024 px logo
TILE_COLOR = (9, 13, 22, 255)     # Obsidian Base from the studio palette
CORNER_RADIUS = 0.18              # fraction of the tile size


def build_master(size=256):
    """Return the cropped logo on a rounded tile, as a size x size RGBA image."""
    logo = Image.open(LOGO).convert("RGB").crop(CROP_BOX)
    logo = logo.resize((size, size), Image.LANCZOS)

    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * CORNER_RADIUS), fill=255)
    tile.paste(Image.new("RGBA", (size, size), TILE_COLOR), (0, 0), mask)
    tile.paste(logo, (0, 0), mask)
    return tile


def frame(master, size):
    """Shrink the master image, sharpening and brightening the small sizes."""
    img = master.resize((size, size), Image.LANCZOS)
    if size <= 48:
        rgb = img.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=2))
        rgb = ImageEnhance.Brightness(rgb).enhance(1.25)
        rgb.putalpha(img.getchannel("A"))
        img = rgb
    return img


def main():
    master = build_master(1024)
    frames = [frame(master, s) for s in ICON_SIZES]
    largest = frames[-1]
    largest.save(ICON, format="ICO", sizes=[(s, s) for s in ICON_SIZES],
                 append_images=frames[:-1])
    print(f"Saved {ICON.relative_to(ROOT)} ({len(ICON_SIZES)} sizes, {ICON.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
