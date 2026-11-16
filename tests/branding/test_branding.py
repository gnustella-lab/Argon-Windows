"""Branding package contract tests.

These validate the asset inventory Argon's Linux branding system requires, the
declared pixel dimensions, PNG structure, and that the Argon name and
vendor strings are independent of Mozilla's trademarks. Stdlib only: PNG
headers are parsed directly so the suite does not need Pillow.
"""

from __future__ import annotations

import re
import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
BRANDING = ROOT / "browser" / "branding" / "argon"
CONTENT = BRANDING / "content" / "branding"
LOCALES = BRANDING / "locales" / "en-US"
BRAND_KIT = ROOT / "brand-kit"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

REQUIRED_ICONS = {16, 32, 48, 64, 128}
REQUIRED_CONTENT = [
    "about.png", "about-logo.png", "about-logo.svg", "about-logo@2x.png",
    "about-wordmark.svg", "about-logo-private.png", "about-logo-private@2x.png",
    "document_pdf.svg", "firefox-wordmark.svg", "aboutDialog.css",
]
REQUIRED_BUILD_FILES = [
    "moz.build", "configure.sh", "content/moz.build", "content/jar.mn",
    "locales/moz.build", "locales/jar.mn", "locales/en-US/brand.ftl",
    "locales/en-US/brand.properties", "pref/firefox-branding.js",
]

WINDOWS_ICO_SIZES = {
    "firefox.ico": [16, 32, 48, 256],
    "firefox64.ico": [16, 32, 48, 64],
    "document.ico": [16, 32, 48, 256],
    "document_pdf.ico": [16, 32, 48, 256],
    "newwindow.ico": [16, 32, 48],
    "newtab.ico": [16, 32, 48],
    "pbmode.ico": [16, 32, 48, 256],
}
WINDOWS_BMP_DIMENSIONS = {
    "wizHeader.bmp": (150, 57),
    "wizHeaderRTL.bmp": (150, 57),
    "wizWatermark.bmp": (164, 314),
}
WINDOWS_VISUAL_ELEMENT_PNGS = {
    "VisualElements_150.png": (300, 300),
    "VisualElements_70.png": (142, 142),
    "PrivateBrowsing_150.png": (300, 300),
    "PrivateBrowsing_70.png": (142, 142),
}


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == PNG_MAGIC, f"{path} is not a PNG"
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def parse_ico(path: Path) -> list[tuple[int, int, str]]:
    data = path.read_bytes()
    assert len(data) >= 6, f"{path.name} is truncated"
    reserved, ico_type, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0 and ico_type == 1 and count > 0, f"{path.name} is not a valid ICO"
    frames: list[tuple[int, int, str]] = []
    for index in range(count):
        off = 6 + index * 16
        assert off + 16 <= len(data), f"{path.name} directory truncated"
        w_byte, h_byte, _, _, _, bpp, size, data_offset = struct.unpack_from("<BBBBHHII", data, off)
        w = 256 if w_byte == 0 else w_byte
        h = 256 if h_byte == 0 else h_byte
        assert data_offset + size <= len(data), f"{path.name} frame {w}x{h} truncated"
        payload = data[data_offset : data_offset + size]
        if payload[:8] == PNG_MAGIC:
            pw, ph = struct.unpack(">II", payload[16:24])
            assert (pw, ph) == (w, h), f"{path.name} PNG frame size mismatch: {(pw, ph)} != {(w, h)}"
            frames.append((w, h, "PNG"))
        else:
            assert len(payload) >= 40, f"{path.name} DIB frame {w}x{h} missing BITMAPINFOHEADER"
            bi_size, bi_w, bi_h, bi_planes, bi_bpp, bi_comp = struct.unpack_from("<IiiHHI", payload, 0)
            assert bi_size == 40, f"{path.name} unexpected DIB header size {bi_size}"
            assert bi_w == w and bi_h == h * 2, f"{path.name} DIB size mismatch: {(bi_w, bi_h)}"
            assert bi_planes == 1 and bi_bpp == 32 and bi_comp == 0, (
                f"{path.name} DIB must be 32-bit uncompressed (got bpp={bi_bpp}, comp={bi_comp})"
            )
            assert bpp in (0, 32), f"{path.name} unexpected directory bpp={bpp}"
            frames.append((w, h, "DIB32"))
    return frames


def parse_bmp(path: Path) -> tuple[int, int, int, int]:
    data = path.read_bytes()
    assert len(data) >= 54 and data[:2] == b"BM", f"{path.name} is not a Windows BMP"
    _, bi_w, bi_h, _, bi_bpp, bi_comp = struct.unpack_from("<IiiHHI", data, 14)
    return bi_w, abs(bi_h), bi_bpp, bi_comp


class BrandingAssetTests(unittest.TestCase):
    def test_application_icons_exist_at_the_exact_required_sizes(self):
        for size in REQUIRED_ICONS:
            path = BRANDING / f"default{size}.png"
            self.assertTrue(path.is_file(), f"missing {path.name}")
            self.assertEqual(png_size(path), (size, size), f"{path.name} must be exactly {size}x{size}")

    def test_content_assets_exist(self):
        for name in REQUIRED_CONTENT:
            self.assertTrue((CONTENT / name).is_file(), f"missing content/branding/{name}")

    def test_build_integration_files_exist(self):
        for name in REQUIRED_BUILD_FILES:
            self.assertTrue((BRANDING / name).is_file(), f"missing {name}")

    def test_every_jar_entry_points_at_a_real_file(self):
        jar_dir = BRANDING / "content"
        jar = (jar_dir / "jar.mn").read_text(encoding="utf-8")
        entries = [line.strip() for line in jar.splitlines()
                   if line.startswith("  content/branding/")]
        self.assertTrue(entries, "jar.mn must list branding content entries")
        for line in entries:
            match = re.fullmatch(r"(\S+)(?:\s+\(([^)]+)\))?", line)
            self.assertIsNotNone(match, f"malformed jar.mn entry: {line}")
            output, source = match.group(1), match.group(2)
            # JarManifestParser resolves an omitted source as basename(output)
            # relative to the directory holding jar.mn.
            rel_source = source or output.rsplit("/", 1)[-1]
            resolved = (jar_dir / rel_source).resolve()
            self.assertTrue(resolved.is_file(),
                            f"jar.mn entry {output} points at missing {jar_dir / rel_source}")

    def test_branding_strings_use_the_argon_name_and_no_mozilla_marks(self):
        ftl = (LOCALES / "brand.ftl").read_text(encoding="utf-8")
        props = (LOCALES / "brand.properties").read_text(encoding="utf-8")
        combined = f"{ftl}\n{props}"
        self.assertIn("-brand-short-name = Argon", ftl)
        self.assertIn("brandShortName=Argon", props)
        self.assertNotIn("Firefox", combined.replace("brand-product-name", ""))
        self.assertNotIn("Mozilla", combined)

    def test_branding_prefs_do_not_advertise_a_production_update_server(self):
        prefs = (BRANDING / "pref" / "firefox-branding.js").read_text(encoding="utf-8")
        urls = re.findall(r'"(https?://[^"]+)"', prefs)
        for url in urls:
            self.assertNotRegex(url, r"mozilla\.org|firefox\.com",
                                "branding prefs must not point at Mozilla update endpoints")

    def test_configure_sets_an_independent_display_name(self):
        configure = (BRANDING / "configure.sh").read_text(encoding="utf-8")
        self.assertIn("MOZ_APP_DISPLAYNAME=Argon", configure)
        self.assertNotIn("MOZ_MACBUNDLE_ID", configure)

    def test_identity_sources_are_original_svg(self):
        for name in ("about-logo.svg", "about-wordmark.svg", "document_pdf.svg"):
            svg = (CONTENT / name).read_text(encoding="utf-8")
            self.assertIn("<svg", svg, f"{name} must be SVG")
            self.assertNotIn("firefox", svg.lower(), f"{name} must not embed Firefox marks")
            self.assertNotIn("mozilla", svg.lower(), f"{name} must not embed Mozilla marks")
        logo = (CONTENT / "about-logo.svg").read_text(encoding="utf-8")
        self.assertIn("#1E1E1E", logo, "primary mark must use the Obsidian container colour")
        self.assertIn("#FBF8F4", logo, "primary mark must use the Ivory glyph colour")

    def test_new_tab_wordmark_is_text_only_and_inherits_the_page_color(self):
        """New Tab supplies a separate 64 px logo and the contrasting fill."""
        svg = ElementTree.fromstring((CONTENT / "firefox-wordmark.svg").read_text())
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        self.assertFalse(svg.findall(".//svg:rect", namespace),
                         "The wordmark must not duplicate the standalone logo")
        text = svg.find("svg:text", namespace)
        self.assertIsNotNone(text)
        assert text is not None
        self.assertEqual(text.text, "Argon")
        self.assertEqual(text.get("fill"), "context-fill",
                         "Hardcoded Ivory disappears against a light New Tab background")
        _, _, width, height = map(float, svg.attrib["viewBox"].split())
        self.assertLessEqual(width / height, 4,
                             "The wordmark must fill its native 105 px background, not shrink a lockup")

    def test_brand_kit_assets_and_tokens_exist(self):
        for name in (
            "argon-brand-sheet.png",
            "mark-primary.svg",
            "mark-inverted.svg",
            "mark-private.svg",
            "glyph-ivory.svg",
            "glyph-obsidian.svg",
            "lockup-dark.svg",
            "lockup-light.svg",
            "construction-grid.svg",
            "brand-tokens.json",
            "mark-primary-512.png",
            "mark-inverted-512.png",
            "mark-private-512.png",
        ):
            self.assertTrue((BRAND_KIT / name).is_file(), f"missing brand-kit/{name}")
        self.assertEqual(png_size(BRAND_KIT / "mark-primary-512.png"), (512, 512))
        self.assertEqual(png_size(BRAND_KIT / "argon-brand-sheet.png"), (1600, 1100))


class WindowsBrandingAssetTests(unittest.TestCase):
    def test_windows_ico_icons_exist_with_required_multi_size_frames(self):
        for name, expected_sizes in WINDOWS_ICO_SIZES.items():
            path = BRANDING / name
            self.assertTrue(path.is_file(), f"missing Windows ICO {name}")
            frames = parse_ico(path)
            self.assertEqual(
                sorted(w for w, _, _ in frames),
                sorted(expected_sizes),
                f"{name} frame sizes mismatch",
            )
            for w, h, fmt in frames:
                self.assertEqual(w, h)
                if w == 256:
                    self.assertEqual(fmt, "PNG", f"{name} 256x256 frame must be PNG-compressed")
                else:
                    self.assertEqual(fmt, "DIB32", f"{name} {w}x{h} frame must be 32-bit DIB")

    def test_windows_nsis_bitmaps_are_uncompressed_24bit_rgb_at_exact_dimensions(self):
        for name, (expected_w, expected_h) in WINDOWS_BMP_DIMENSIONS.items():
            path = BRANDING / name
            self.assertTrue(path.is_file(), f"missing NSIS bitmap {name}")
            w, h, bpp, comp = parse_bmp(path)
            self.assertEqual((w, h), (expected_w, expected_h), f"{name} dimension mismatch")
            self.assertEqual(bpp, 24, f"{name} must be 24-bit RGB for NSIS compatibility")
            self.assertEqual(comp, 0, f"{name} must be uncompressed BI_RGB")

    def test_windows_visual_elements_manifests_and_tile_pngs_are_valid(self):
        for name, expected_dims in WINDOWS_VISUAL_ELEMENT_PNGS.items():
            path = BRANDING / name
            self.assertTrue(path.is_file(), f"missing VisualElements tile {name}")
            self.assertEqual(png_size(path), expected_dims, f"{name} dimension mismatch")

        expected_manifests = {
            "firefox.VisualElementsManifest.xml": (
                r"browser\VisualElements\VisualElements_150.png",
                r"browser\VisualElements\VisualElements_70.png",
            ),
            "private_browsing.VisualElementsManifest.xml": (
                r"browser\VisualElements\PrivateBrowsing_150.png",
                r"browser\VisualElements\PrivateBrowsing_70.png",
            ),
        }
        for fname, (logo_150, logo_70) in expected_manifests.items():
            path = BRANDING / fname
            self.assertTrue(path.is_file(), f"missing {fname}")
            root = ElementTree.fromstring(path.read_text(encoding="utf-8"))
            ve = root.find("VisualElements")
            self.assertIsNotNone(ve, f"{fname} missing <VisualElements>")
            assert ve is not None
            self.assertEqual(ve.attrib.get("Square150x150Logo"), logo_150)
            self.assertEqual(ve.attrib.get("Square70x70Logo"), logo_70)
            self.assertEqual(ve.attrib.get("ShowNameOnSquare150x150Logo"), "on")
            self.assertEqual(ve.attrib.get("ForegroundText"), "light")
            self.assertEqual(ve.attrib.get("BackgroundColor", "").lower(), "#15171b")

    def test_windows_nsis_branding_defines_use_argon_identity(self):
        nsi_path = BRANDING / "branding.nsi"
        self.assertTrue(nsi_path.is_file(), "missing browser/branding/argon/branding.nsi")
        text = nsi_path.read_text(encoding="utf-8")
        self.assertIn('!define BrandFullNameInternal "Argon"', text)
        self.assertIn('!define BrandFullName         "Argon Browser"', text)
        self.assertIn('!define CompanyName           "Argon"', text)
        self.assertIn('!define Channel                "argon"', text)
        active = "\n".join(
            line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", ";"))
        ).lower()
        self.assertNotIn("firefox", active)
        self.assertNotIn("mozilla.org", active)
        self.assertNotIn("mozilla corporation", active)

    def test_content_jar_manifest_packages_windows_document_icon(self):
        jar_text = (BRANDING / "content" / "jar.mn").read_text(encoding="utf-8")
        self.assertIn("content/branding/document.ico", jar_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
