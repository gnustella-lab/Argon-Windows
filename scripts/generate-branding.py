#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate the Argon branding raster assets and brand kit showcase sheet.

The Argon mark is a flat rounded square in Obsidian (#1E1E1E) carrying a warm
Ivory (#FBF8F4) geometric "A" with:
  - a circularly rounded apex cap,
  - flat horizontal baseline feet,
  - a sharp inner V vertex, and
  - a detached floating trapezoidal crossbar separated from both legs.

Rasters are drawn on a 128-unit coordinate grid at 4x supersampling and
downsampled with Lanczos so 16 px icons stay crisp. SVG files in
browser/branding/argon/content/branding/ and brand-kit/ use the exact same
128-unit coordinates.

Usage:
    python3 scripts/generate-branding.py
"""

from __future__ import annotations

import argparse
import io
import os
import pathlib
import struct
import sys

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ModuleNotFoundError:  # pragma: no cover - documents the requirement
    sys.exit("Pillow is required to regenerate assets: pip install Pillow")

ROOT = pathlib.Path(__file__).resolve().parents[1]
BRANDING = ROOT / "browser" / "branding" / "argon"
CONTENT = BRANDING / "content" / "branding"
BRAND_KIT = ROOT / "brand-kit"

# Brand Core palette (derived from the canonical Argon mark).
OBSIDIAN = "#1E1E1E"       # Primary mark container (rgb 30, 30, 30)
IVORY = "#FBF8F4"          # Primary mark glyph (rgb 251, 248, 244)
ACCENT = "#6478F5"         # Interactive UI & private-browsing accent
DARK_CHROME = "#15171B"    # Browser dark chrome surface

# Canonical 128-unit geometry of the Argon mark.
GRID = 128.0
SUPERSAMPLE = 4
MASTER_SIZE = 512
CORNER_RADIUS = 20.0       # 15.625% of square width
_MARK_MASTER_CACHE: dict[tuple[str | None, str], Image.Image] = {}

# Outer legs & flat feet + inner V vertex on the 128x128 grid.
CHEVRON_POINTS = [
    (16.0, 106.0),         # Outer left foot
    (56.4, 27.5),          # Left tangent of the rounded apex
    (71.6, 27.5),          # Right tangent of the rounded apex
    (112.0, 106.0),        # Outer right foot
    (97.0, 106.0),         # Inner right foot
    (64.0, 41.5),          # Sharp inner V vertex
    (31.0, 106.0),         # Inner left foot
]
APEX_CENTER = (64.0, 31.4)
APEX_RADIUS = 8.55         # Top of cap sits at y = 22.85

# Detached floating trapezoidal crossbar (parallel to inner leg slopes).
CROSSBAR_POINTS = [
    (50.5, 78.0),          # Top-left
    (77.5, 78.0),          # Top-right
    (82.0, 87.0),          # Bottom-right
    (46.0, 87.0),          # Bottom-left
]


def _scale_points(
    points: list[tuple[float, float]],
    scale: float,
    offset: tuple[float, float] = (0.0, 0.0),
) -> list[tuple[float, float]]:
    ox, oy = offset
    return [(ox + x * scale, oy + y * scale) for x, y in points]


def _render_mark_canvas(
    pixel_size: int,
    background: str | None = OBSIDIAN,
    glyph: str = IVORY,
) -> Image.Image:
    scale = pixel_size / GRID
    image = Image.new("RGBA", (pixel_size, pixel_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if background is not None:
        radius = round(CORNER_RADIUS * scale)
        draw.rounded_rectangle((0, 0, pixel_size - 1, pixel_size - 1), radius=radius, fill=background)

    # 1. Chevron legs (flat feet, sharp inner vertex) + rounded apex cap.
    draw.polygon(_scale_points(CHEVRON_POINTS, scale), fill=glyph)
    cx, cy = APEX_CENTER[0] * scale, APEX_CENTER[1] * scale
    r = APEX_RADIUS * scale
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=glyph)

    # 2. Detached floating trapezoidal crossbar.
    draw.polygon(_scale_points(CROSSBAR_POINTS, scale), fill=glyph)
    return image


def draw_mark(
    size: int,
    background: str | None = OBSIDIAN,
    glyph: str = IVORY,
) -> Image.Image:
    """Draw the canonical Argon mark on a transparent rounded square.

    Rasters are drawn once per `(background, glyph)` at `MASTER_SIZE * SUPERSAMPLE`
    (2048 px) and downsampled with Lanczos so repeated target sizes share one master.
    """
    if size > MASTER_SIZE:
        return _render_mark_canvas(size * SUPERSAMPLE, background, glyph).resize(
            (size, size), Image.Resampling.LANCZOS
        )

    key = (background, glyph)
    master = _MARK_MASTER_CACHE.get(key)
    if master is None:
        master = _render_mark_canvas(MASTER_SIZE * SUPERSAMPLE, background, glyph)
        _MARK_MASTER_CACHE[key] = master
    return master.resize((size, size), Image.Resampling.LANCZOS)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    win_fonts = pathlib.Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts"
    candidates = [
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # Windows
        str(win_fonts / ("segoeuib.ttf" if bold else "segoeui.ttf")),
        str(win_fonts / ("arialbd.ttf" if bold else "arial.ttf")),
        str(win_fonts / ("calibrib.ttf" if bold else "calibri.ttf")),
        # macOS
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for candidate in candidates:
        if pathlib.Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def draw_brand_sheet() -> Image.Image:
    """Render the 1600x1100 Argon Brand Kit overview sheet."""
    w, h = 1600, 1100
    sheet = Image.new("RGBA", (w, h), DARK_CHROME)
    draw = ImageDraw.Draw(sheet)

    f_title = _load_font(36, bold=True)
    f_sub = _load_font(18, bold=False)
    f_sec = _load_font(16, bold=True)
    f_label = _load_font(16, bold=True)
    f_meta = _load_font(14, bold=False)
    f_wordmark = _load_font(52, bold=True)

    draw.text((64, 48), "ARGON BROWSER — BRAND KIT", fill=IVORY, font=f_title)
    draw.text(
        (64, 96),
        "Canonical mark geometry (128u grid), lockups, color system & icon scale",
        fill="#A6ADBD",
        font=f_sub,
    )
    draw.line([(64, 136), (w - 64, 136)], fill="#2F333C", width=2)

    draw.text((64, 160), "01 / MARK VARIATIONS", fill=ACCENT, font=f_sec)
    cards = [
        ("Primary Mark", "Obsidian #1E1E1E / Ivory #FBF8F4", "#23262D", draw_mark(168, OBSIDIAN, IVORY)),
        ("Inverted Mark", "Ivory #FBF8F4 / Obsidian #1E1E1E", "#23262D", draw_mark(168, IVORY, OBSIDIAN)),
        ("Private Browsing", "Dark Chrome #15171B / Accent #6478F5", "#23262D", draw_mark(168, DARK_CHROME, ACCENT)),
        ("Standalone Glyph", "Uncontained Ivory #FBF8F4 on Dark", OBSIDIAN, draw_mark(168, None, IVORY)),
    ]
    card_w, card_h = 344, 270
    for idx, (title, subtitle, card_bg, mark_img) in enumerate(cards):
        cx = 64 + idx * (card_w + 32)
        cy = 196
        draw.rounded_rectangle(
            (cx, cy, cx + card_w, cy + card_h),
            radius=16,
            fill=card_bg,
            outline="#2F333C",
            width=2,
        )
        sheet.alpha_composite(mark_img, (cx + (card_w - mark_img.width) // 2, cy + 24))
        draw.text((cx + 20, cy + 208), title, fill=IVORY, font=f_label)
        draw.text((cx + 20, cy + 232), subtitle, fill="#A6ADBD", font=f_meta)

    draw.text((64, 500), "02 / GEOMETRIC CONSTRUCTION (128u GRID)", fill=ACCENT, font=f_sec)
    draw.text((620, 500), "03 / HORIZONTAL WORDMARK LOCKUPS", fill=ACCENT, font=f_sec)

    gx, gy, gw, gh = 64, 536, 512, 260
    draw.rounded_rectangle((gx, gy, gx + gw, gy + gh), radius=16, fill="#23262D", outline="#2F333C", width=2)
    c_mark = draw_mark(192, OBSIDIAN, IVORY)
    cmx, cmy = gx + 28, gy + 34
    sheet.alpha_composite(c_mark, (cmx, cmy))
    s192 = 192.0 / GRID
    for y_u in (22.85, 41.5, 78.0, 87.0, 106.0):
        yy = round(cmy + y_u * s192)
        draw.line([(cmx - 10, yy), (cmx + 202, yy)], fill=ACCENT, width=1)
    for x_u in (16.0, 64.0, 112.0):
        xx = round(cmx + x_u * s192)
        draw.line([(xx, cmy - 10), (xx, cmy + 202)], fill=ACCENT, width=1)

    specs = [
        "Grid: 128 x 128 u",
        "Corner radius: 20 u (15.6%)",
        "Apex arc: r = 8.55 u at (64, 31.4)",
        "Inner vertex: (64, 41.5)",
        "Flat baseline: y = 106 u",
        "Foot width: 15 u (x=16..31, 97..112)",
        "Floating bar: y = 78..87 u",
        "Bar air gap: 6.5 u each side",
    ]
    for i, line in enumerate(specs):
        draw.text((gx + 244, gy + 32 + i * 25), line, fill=IVORY if i == 0 else "#A6ADBD", font=f_meta)

    lx, ly, lw, lh = 620, 536, 916, 122
    draw.rounded_rectangle((lx, ly, lx + lw, ly + lh), radius=16, fill=OBSIDIAN, outline="#2F333C", width=2)
    sheet.alpha_composite(draw_mark(76, IVORY, OBSIDIAN), (lx + 28, ly + 23))
    draw.text((lx + 124, ly + 31), "Argon", fill=IVORY, font=f_wordmark)
    draw.text((lx + lw - 270, ly + 50), "Dark Lockup (#1E1E1E / #FBF8F4)", fill="#A6ADBD", font=f_meta)

    ly2 = ly + lh + 16
    draw.rounded_rectangle((lx, ly2, lx + lw, ly2 + lh), radius=16, fill=IVORY, outline="#DDE1E7", width=2)
    sheet.alpha_composite(draw_mark(76, OBSIDIAN, IVORY), (lx + 28, ly2 + 23))
    draw.text((lx + 124, ly2 + 31), "Argon", fill=OBSIDIAN, font=f_wordmark)
    draw.text((lx + lw - 270, ly2 + 50), "Light Lockup (#FBF8F4 / #1E1E1E)", fill="#5A6070", font=f_meta)

    draw.text((64, 830), "04 / COLOR PALETTE", fill=ACCENT, font=f_sec)
    draw.text((980, 830), "05 / APPLICATION ICON SCALE", fill=ACCENT, font=f_sec)

    swatches = [
        ("Obsidian", OBSIDIAN, IVORY),
        ("Ivory", IVORY, OBSIDIAN),
        ("Accent", ACCENT, IVORY),
        ("Dark BG", DARK_CHROME, IVORY),
        ("Surface", "#23262D", IVORY),
        ("Light BG", "#F7F8FA", DARK_CHROME),
    ]
    sw_w, sw_h = 136, 176
    for idx, (name, hex_val, text_col) in enumerate(swatches):
        sx = 64 + idx * (sw_w + 12)
        sy = 866
        draw.rounded_rectangle((sx, sy, sx + sw_w, sy + sw_h), radius=14, fill=hex_val, outline="#2F333C", width=2)
        draw.text((sx + 14, sy + sw_h - 56), name, fill=text_col, font=f_label)
        draw.text((sx + 14, sy + sw_h - 32), hex_val, fill=text_col, font=f_meta)

    ix, iy, iw, ih = 980, 866, 556, 176
    draw.rounded_rectangle((ix, iy, ix + iw, iy + ih), radius=16, fill="#23262D", outline="#2F333C", width=2)
    cur_x = ix + 28
    for sz in (128, 64, 48, 32, 16):
        ic = draw_mark(sz, OBSIDIAN, IVORY)
        sheet.alpha_composite(ic, (cur_x, iy + 18 + (128 - sz)))
        draw.text((cur_x, iy + 150), f"{sz}px", fill="#A6ADBD", font=f_meta)
        cur_x += sz + 36

    return sheet


def write_png(image: Image.Image, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def write_bmp(image: Image.Image, path: pathlib.Path, background: str = DARK_CHROME) -> None:
    """Write an uncompressed 24-bit RGB Windows BMP (NSIS-compatible)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if image.mode != "RGB":
        canvas = Image.new("RGBA", image.size, background)
        if image.mode == "RGBA":
            canvas.alpha_composite(image)
        else:
            canvas.paste(image.convert("RGBA"))
        rgb = canvas.convert("RGB")
    else:
        rgb = image
    rgb.save(path, format="BMP")


def _encode_ico_dib_32(image: Image.Image) -> bytes:
    """Encode an RGBA image as a 32-bit BGRA DIB + 1-bit AND mask for Windows ICO."""
    rgba = image.convert("RGBA")
    w, h = rgba.size
    pixels = rgba.tobytes("raw", "BGRA")
    # ICO DIBs store rows bottom-up, with biHeight = 2 * h (XOR + AND mask).
    xor_rows = b"".join(
        pixels[y * w * 4 : (y + 1) * w * 4] for y in range(h - 1, -1, -1)
    )
    and_stride = ((w + 31) // 32) * 4
    and_rows = bytearray()
    alpha_bytes = rgba.getchannel("A").tobytes()
    for y in range(h - 1, -1, -1):
        row = bytearray(and_stride)
        row_off = y * w
        for x in range(w):
            if alpha_bytes[row_off + x] == 0:
                row[x >> 3] |= 0x80 >> (x & 7)
        and_rows.extend(row)

    header = struct.pack(
        "<IIIHHIIIIII",
        40,          # biSize
        w,           # biWidth
        h * 2,       # biHeight (XOR + AND)
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression (BI_RGB)
        len(xor_rows),
        0,
        0,
        0,
        0,
    )
    return header + xor_rows + bytes(and_rows)


def _encode_ico_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_ico(frames: list[Image.Image], path: pathlib.Path) -> None:
    """Write a multi-size Windows ICO using 32-bit DIBs (<256 px) and PNG (256 px)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(frames, key=lambda im: im.width)
    payloads: list[tuple[int, int, bytes]] = []
    for im in ordered:
        w, h = im.size
        if w != h or w < 1 or w > 256:
            raise ValueError(f"Unsupported ICO frame size: {w}x{h}")
        raw = _encode_ico_png(im) if w == 256 else _encode_ico_dib_32(im)
        payloads.append((w, h, raw))

    count = len(payloads)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + count * 16
    directory = bytearray()
    for w, h, raw in payloads:
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                0 if w == 256 else w,
                0 if h == 256 else h,
                0,
                0,
                1,
                32,
                len(raw),
                offset,
            )
        )
        offset += len(raw)

    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(directory)
        for _, _, raw in payloads:
            handle.write(raw)


def draw_document_icon(size: int, badge: str | None = None) -> Image.Image:
    """Render an Argon document (or PDF) icon at `size` px from supersampled geometry."""
    ss = size * SUPERSAMPLE
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = ss / 32.0

    # Paper body matching browser/branding/argon/content/branding/document_pdf.svg
    paper = [(6 * s, 4 * s), (19 * s, 4 * s), (24 * s, 9 * s), (24 * s, 28 * s), (6 * s, 28 * s)]
    stroke_w = max(2, round(1.6 * s))
    draw.polygon(paper, fill=IVORY)
    draw.line([*paper, paper[0]], fill=OBSIDIAN, width=stroke_w, joint="curve")
    # Folded corner crease
    draw.line([(19 * s, 4 * s), (19 * s, 9 * s), (24 * s, 9 * s)], fill=OBSIDIAN, width=stroke_w)

    # Centered Argon glyph inside the sheet
    glyph_sz = max(8, round(12.8 * s))
    glyph = draw_mark(glyph_sz, background=None, glyph=OBSIDIAN)
    gx = round(9.0 * s)
    gy = round((10.0 if badge else 11.5) * s)
    img.alpha_composite(glyph, (gx, gy))

    if badge:
        bar_y0 = round(21.0 * s)
        bar_y1 = round(27.0 * s)
        draw.rectangle((round(7.5 * s), bar_y0, round(22.5 * s), bar_y1), fill=ACCENT)
        if size >= 32:
            font = _load_font(max(8, round(4.5 * s)), bold=True)
            bbox = draw.textbbox((0, 0), badge, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            tx = round(15.0 * s - tw / 2.0) - bbox[0]
            ty = round((bar_y0 + bar_y1) / 2.0 - th / 2.0) - bbox[1]
            draw.text((tx, ty), badge, fill=IVORY, font=font)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def draw_newwindow_icon(size: int) -> Image.Image:
    """Render a browser window icon carrying the Argon accent titlebar."""
    ss = size * SUPERSAMPLE
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = ss / 32.0
    pad = round(2.5 * s)
    radius = max(2, round(3.5 * s))
    stroke = max(2, round(1.8 * s))
    draw.rounded_rectangle(
        (pad, round(4 * s), ss - pad - 1, round(28 * s)),
        radius=radius,
        fill=IVORY,
        outline=OBSIDIAN,
        width=stroke,
    )
    draw.rounded_rectangle(
        (pad, round(4 * s), ss - pad - 1, round(10.5 * s)),
        radius=radius,
        fill=OBSIDIAN,
    )
    draw.rectangle((pad, round(8 * s), ss - pad - 1, round(11 * s)), fill=OBSIDIAN)
    # Accent window dot
    r = max(2, round(1.2 * s))
    cx, cy = round(6.5 * s), round(7.5 * s)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ACCENT)
    glyph_sz = max(8, round(12.0 * s))
    img.alpha_composite(draw_mark(glyph_sz, background=None, glyph=OBSIDIAN), (round(10 * s), round(13 * s)))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def draw_newtab_icon(size: int) -> Image.Image:
    """Render a crisp '+' new-tab icon inside an Argon rounded tile."""
    ss = size * SUPERSAMPLE
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = round(CORNER_RADIUS * (ss / GRID))
    draw.rounded_rectangle((0, 0, ss - 1, ss - 1), radius=radius, fill=OBSIDIAN)
    cx = ss // 2
    arm = round(ss * 0.24)
    half_t = max(2, round(ss * 0.065))
    draw.rectangle((cx - half_t, cx - arm, cx + half_t, cx + arm), fill=IVORY)
    draw.rectangle((cx - arm, cx - half_t, cx + arm, cx + half_t), fill=IVORY)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def draw_tile_png(size: int, mark_size: int, background: str | None, glyph: str) -> Image.Image:
    """Render a centered Windows Start tile PNG on a transparent canvas."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mark = draw_mark(mark_size, background=background, glyph=glyph)
    offset = ((size - mark_size) // 2, (size - mark_size) // 2)
    canvas.alpha_composite(mark, offset)
    return canvas


def draw_nsis_header(rtl: bool = False) -> Image.Image:
    """Render the 150x57 NSIS wizard header bitmap."""
    w, h = 150, 57
    img = Image.new("RGBA", (w, h), DARK_CHROME)
    draw = ImageDraw.Draw(img)
    draw.line([(0, h - 1), (w - 1, h - 1)], fill="#2F333C", width=1)
    mark = draw_mark(38, OBSIDIAN, IVORY)
    mx = 12 if rtl else (w - 38 - 12)
    my = (h - 38) // 2
    img.alpha_composite(mark, (mx, my))
    return img


def draw_nsis_watermark() -> Image.Image:
    """Render the 164x314 NSIS welcome/finish sidebar watermark bitmap."""
    w, h = 164, 314
    img = Image.new("RGBA", (w, h), DARK_CHROME)
    draw = ImageDraw.Draw(img)
    # Subtle top accent stripe and right separator
    draw.rectangle((0, 0, w - 1, 4), fill=ACCENT)
    draw.line([(w - 1, 0), (w - 1, h - 1)], fill="#2F333C", width=1)
    # Framed primary Argon mark
    draw.rounded_rectangle((22, 34, w - 23, 154), radius=14, fill="#23262D", outline="#2F333C", width=1)
    mark = draw_mark(84, OBSIDIAN, IVORY)
    img.alpha_composite(mark, ((w - 84) // 2, 52))
    # Wordmark + subtitle
    f_word = _load_font(22, bold=True)
    f_tag = _load_font(11, bold=False)
    wb = draw.textbbox((0, 0), "Argon", font=f_word)
    draw.text(((w - (wb[2] - wb[0])) // 2 - wb[0], 176), "Argon", fill=IVORY, font=f_word)
    tb = draw.textbbox((0, 0), "Private Web Browser", font=f_tag)
    draw.text(((w - (tb[2] - tb[0])) // 2 - tb[0], 208), "Private Web Browser", fill="#A6ADBD", font=f_tag)
    return img


def generate_windows_assets() -> None:
    """Generate Windows ICO, NSIS BMP, and Start-tile VisualElements assets."""
    # Windows multi-size .ico icons required by browser/app/moz.build & splash.rc.
    write_ico([draw_mark(sz) for sz in (16, 32, 48, 256)], BRANDING / "firefox.ico")
    write_ico([draw_mark(sz) for sz in (16, 32, 48, 64)], BRANDING / "firefox64.ico")
    write_ico([draw_document_icon(sz) for sz in (16, 32, 48, 256)], BRANDING / "document.ico")
    write_ico([draw_document_icon(sz, badge="PDF") for sz in (16, 32, 48, 256)], BRANDING / "document_pdf.ico")
    write_ico([draw_newwindow_icon(sz) for sz in (16, 32, 48)], BRANDING / "newwindow.ico")
    write_ico([draw_newtab_icon(sz) for sz in (16, 32, 48)], BRANDING / "newtab.ico")
    write_ico(
        [draw_mark(sz, background=DARK_CHROME, glyph=ACCENT) for sz in (16, 32, 48, 256)],
        BRANDING / "pbmode.ico",
    )

    # Windows NSIS wizard bitmaps required by python/mozbuild/mozbuild/nsis.py.
    write_bmp(draw_nsis_header(rtl=False), BRANDING / "wizHeader.bmp")
    write_bmp(draw_nsis_header(rtl=True), BRANDING / "wizHeaderRTL.bmp")
    write_bmp(draw_nsis_watermark(), BRANDING / "wizWatermark.bmp")

    # Windows Start tile VisualElements PNGs required by branding-common.mozbuild.
    write_png(draw_tile_png(300, 180, OBSIDIAN, IVORY), BRANDING / "VisualElements_150.png")
    write_png(draw_tile_png(142, 92, OBSIDIAN, IVORY), BRANDING / "VisualElements_70.png")
    write_png(draw_tile_png(300, 180, DARK_CHROME, ACCENT), BRANDING / "PrivateBrowsing_150.png")
    write_png(draw_tile_png(142, 92, DARK_CHROME, ACCENT), BRANDING / "PrivateBrowsing_70.png")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Argon branding raster assets.")
    parser.add_argument(
        "--windows-only",
        action="store_true",
        help="Generate only Windows ICO/BMP/VisualElements assets under browser/branding/argon/.",
    )
    args = parser.parse_args(argv)

    BRANDING.mkdir(parents=True, exist_ok=True)
    generate_windows_assets()
    if args.windows_only:
        print(f"Generated Windows Argon branding assets under {BRANDING}")
        return 0

    CONTENT.mkdir(parents=True, exist_ok=True)
    BRAND_KIT.mkdir(parents=True, exist_ok=True)

    # Application icons: exact sizes Firefox's branding system packages.
    for size in (16, 32, 48, 64, 128, 256):
        write_png(draw_mark(size), BRANDING / f"default{size}.png")

    # About dialog artwork (Primary: Obsidian container + Ivory glyph).
    write_png(draw_mark(256), CONTENT / "about-logo.png")
    write_png(draw_mark(512), CONTENT / "about-logo@2x.png")
    write_png(draw_mark(192), CONTENT / "about.png")

    # Private browsing uses the same geometry with the Argon accent glyph.
    write_png(draw_mark(256, background=DARK_CHROME, glyph=ACCENT), CONTENT / "about-logo-private.png")
    write_png(draw_mark(512, background=DARK_CHROME, glyph=ACCENT), CONTENT / "about-logo-private@2x.png")

    # Brand Kit standalone high-resolution rasters (512 px) and overview sheet.
    write_png(draw_mark(512, background=OBSIDIAN, glyph=IVORY), BRAND_KIT / "mark-primary-512.png")
    write_png(draw_mark(512, background=IVORY, glyph=OBSIDIAN), BRAND_KIT / "mark-inverted-512.png")
    write_png(draw_mark(512, background=DARK_CHROME, glyph=ACCENT), BRAND_KIT / "mark-private-512.png")
    write_png(draw_mark(512, background=None, glyph=IVORY), BRAND_KIT / "glyph-ivory-512.png")
    write_png(draw_mark(512, background=None, glyph=OBSIDIAN), BRAND_KIT / "glyph-obsidian-512.png")
    write_png(draw_brand_sheet(), BRAND_KIT / "argon-brand-sheet.png")

    print(f"Generated Argon branding assets under {BRANDING} and {BRAND_KIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
