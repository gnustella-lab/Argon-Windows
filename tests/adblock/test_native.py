"""Exercise the native C ABI with the actual Rust library (and optional Windows DLL)."""
import ctypes
import json
import os
from pathlib import Path
import platform
import unittest


class NativeEngine(unittest.TestCase):
    def test_native_request_and_exception(self):
        root = Path(__file__).resolve().parents[2]
        adblock_dir = root / "argon" / "adblock"
        self.assertTrue((adblock_dir / "engine.wasm").is_file(), "WASM adblock engine must exist")

        system = platform.system()
        if system == "Windows":
            path = adblock_dir / "argon_adblock.dll"
            if not path.is_file():
                # Windows C-ABI DLL is optional; vendored JS uBlock engine is default.
                native_js = (adblock_dir / "native.sys.mjs").read_text(encoding="utf-8")
                self.assertIn("argon_adblock.dll", native_js)
                self.assertIn("WINNT", native_js)
                self.skipTest("Optional Windows argon_adblock.dll is not built; uBlock JS engine is default")
        elif system == "Darwin":
            path = adblock_dir / "libargon_adblock.dylib"
            if not path.is_file():
                self.skipTest("Optional macOS libargon_adblock.dylib is not built")
        else:
            path = adblock_dir / "libargon_adblock.so"
            self.assertTrue(path.is_file(), "Native engine library must be built")

        lib = ctypes.CDLL(str(path))
        call = lib.execute_json
        call.argtypes = [ctypes.c_char_p]
        call.restype = ctypes.c_char_p

        def invoke(value):
            return json.loads(call(json.dumps(value).encode("utf-8")))

        invoke({"op": "load", "lists": ["||ads.test^\n@@||ads.test/allowed"]})
        self.assertTrue(
            invoke({"op": "check", "url": "https://ads.test/ad", "source": "https://page.test", "type": "script"})[
                "matched"
            ]
        )
        self.assertFalse(
            invoke(
                {"op": "check", "url": "https://ads.test/allowed", "source": "https://page.test", "type": "script"}
            )["matched"]
        )


if __name__ == "__main__":
    unittest.main()
