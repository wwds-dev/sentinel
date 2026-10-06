"""Generates assets/icon.icns for the Sentinel app from a drawn PNG."""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

SIZE = 1024
BG = (12, 16, 14)
GREEN = (60, 255, 136)
GREEN_DIM = (30, 130, 80)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

cx, cy = SIZE // 2, SIZE // 2
r = int(SIZE * 0.46)

# rounded-square dark backdrop
pad = int(SIZE * 0.04)
draw.rounded_rectangle([pad, pad, SIZE - pad, SIZE - pad], radius=int(SIZE * 0.18), fill=BG)

# outer hex/shield ring
draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GREEN_DIM, width=int(SIZE * 0.012))

# shield shape
shield_w = r * 1.25
shield_top = cy - r * 0.85
points = [
    (cx, shield_top),
    (cx + shield_w * 0.55, shield_top + shield_w * 0.18),
    (cx + shield_w * 0.55, shield_top + shield_w * 0.95),
    (cx, shield_top + shield_w * 1.35),
    (cx - shield_w * 0.55, shield_top + shield_w * 0.95),
    (cx - shield_w * 0.55, shield_top + shield_w * 0.18),
]
draw.polygon(points, fill=(18, 26, 22), outline=GREEN, width=int(SIZE * 0.01))

# central "S" / sentinel eye glyph using an arc + dot to suggest a radar/eye
eye_r = r * 0.42
draw.ellipse([cx - eye_r, cy - eye_r * 0.55 + r * 0.05, cx + eye_r, cy + eye_r * 0.55 + r * 0.05],
             outline=GREEN, width=int(SIZE * 0.018))
pupil_r = r * 0.13
draw.ellipse([cx - pupil_r, cy - pupil_r + r * 0.05, cx + pupil_r, cy + pupil_r + r * 0.05], fill=GREEN)

# faint scan lines for a "sentinel/AI" feel, composited with real alpha
overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
odraw = ImageDraw.Draw(overlay)
for i in range(-2, 3):
    y = cy + i * r * 0.28
    odraw.line([(cx - r * 0.7, y), (cx + r * 0.7, y)], fill=(60, 255, 136, 55), width=2)
img.alpha_composite(overlay)
draw = ImageDraw.Draw(img)

png_path = ASSETS / "icon_source.png"
img.save(png_path)

iconset_dir = ASSETS / "icon.iconset"
iconset_dir.mkdir(exist_ok=True)

sizes = [16, 32, 64, 128, 256, 512, 1024]
for s in sizes:
    resized = img.resize((s, s), Image.LANCZOS)
    resized.save(iconset_dir / f"icon_{s}x{s}.png")
    if s <= 512:
        resized2x = img.resize((s * 2, s * 2), Image.LANCZOS)
        resized2x.save(iconset_dir / f"icon_{s}x{s}@2x.png")

# rename to iconutil's expected naming
mapping = {
    "icon_16x16.png": "icon_16x16.png",
    "icon_16x16@2x.png": "icon_16x16@2x.png",
    "icon_32x32.png": "icon_32x32.png",
    "icon_32x32@2x.png": "icon_32x32@2x.png",
    "icon_128x128.png": "icon_128x128.png",
    "icon_128x128@2x.png": "icon_128x128@2x.png",
    "icon_256x256.png": "icon_256x256.png",
    "icon_256x256@2x.png": "icon_256x256@2x.png",
    "icon_512x512.png": "icon_512x512.png",
    "icon_512x512@2x.png": "icon_512x512@2x.png",
}
# remove unneeded sizes (64, 1024 not standard in iconset naming except via @2x of 512)
for extra in ["icon_64x64.png", "icon_64x64@2x.png", "icon_1024x1024.png", "icon_1024x1024@2x.png"]:
    p = iconset_dir / extra
    if p.exists():
        p.unlink()

icns_path = ASSETS / "icon.icns"
subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)], check=True)
print(f"Wrote {icns_path}")


# ── menu bar glyph ───────────────────────────────────────────────────────────
# The status item needs the same mark drawn differently: solid black on
# transparent, no backdrop, no colour. macOS treats that as a template image
# and recolours it for a light or dark menu bar and for the highlighted state
# (ui/tray.py sets QIcon.setIsMask). Colour here would be thrown away, and a
# partial alpha reads as a smudge rather than as detail.
#
# Drawn at 8x and downsampled, because ImageDraw has no antialiasing — the same
# reason the app icon above is rendered once at 1024.
TRAY_SIZES = {"tray.png": 18, "tray@2x.png": 36}
SUPERSAMPLE = 8


def draw_tray(size: int) -> Image.Image:
    """The shield and eye at menu bar scale: silhouette, knocked-out eye, pupil."""
    s = size * SUPERSAMPLE
    glyph = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pen = ImageDraw.Draw(glyph)

    half = s * 0.38
    top, shoulder, flank, tip = s * 0.06, s * 0.22, s * 0.66, s * 0.96
    pen.polygon(
        [
            (s * 0.5, top),
            (s * 0.5 + half, shoulder),
            (s * 0.5 + half, flank),
            (s * 0.5, tip),
            (s * 0.5 - half, flank),
            (s * 0.5 - half, shoulder),
        ],
        fill=(0, 0, 0, 255),
    )

    # Knocked out rather than drawn: ImageDraw writes pixels instead of
    # blending, so a zero alpha clears the shield underneath.
    eye_x, eye_y = s * 0.26, s * 0.15
    pen.ellipse(
        [s * 0.5 - eye_x, s * 0.44 - eye_y, s * 0.5 + eye_x, s * 0.44 + eye_y],
        fill=(0, 0, 0, 0),
    )
    pupil = s * 0.085
    pen.ellipse(
        [s * 0.5 - pupil, s * 0.44 - pupil, s * 0.5 + pupil, s * 0.44 + pupil],
        fill=(0, 0, 0, 255),
    )
    return glyph.resize((size, size), Image.LANCZOS)


for name, px in TRAY_SIZES.items():
    draw_tray(px).save(ASSETS / name)
print(f"Wrote {', '.join(TRAY_SIZES)} to {ASSETS}")
