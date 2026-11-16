#!/usr/bin/env python3
"""Generate the content-realm adapter, verifying the pinned source hashes first."""
import hashlib
import json
from pathlib import Path
import sys

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import platform_compat

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'argon/adblock'


def main():
    provenance = json.loads((BASE / 'ublock/provenance.json').read_text(encoding='utf-8'))
    for path, expected in provenance['files'].items():
        actual = hashlib.sha256((BASE / 'ublock' / path).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f'uBO source drift: {path}')
    template = (BASE / 'procedural-bootstrap.js').read_text(encoding='utf-8')
    assert template.count('/* UBO_SOURCE */') == 1
    source = (BASE / 'ublock/src/js/contentscript-extra.js').read_text(encoding='utf-8')
    platform_compat.write_text_lf(
        BASE / 'procedural.js',
        template.replace('/* UBO_SOURCE */', source),
    )
    print('Generated browser-owned uBO procedural interpreter:', provenance['commit'])


if __name__ == '__main__':
    main()
