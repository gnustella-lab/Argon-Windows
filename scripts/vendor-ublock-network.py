#!/usr/bin/env python3
"""Vendor the pinned upstream network-engine module graph, without rewriting it."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import platform_compat

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'argon/adblock/ublock'
COMMIT = '3762a26fd65df422080c116e6a70d13bc8d57d62'


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    source = Path(args[0]) if args else ROOT / '.cache/ublock-source'
    git = shutil.which(platform_compat.binary_name('git')) or shutil.which('git') or 'git'
    if subprocess.check_output(
        [git, '-C', str(source), 'rev-parse', 'HEAD'],
        text=True,
        encoding='utf-8',
    ).strip() != COMMIT:
        raise SystemExit('The source checkout must match the pinned commit')

    imports = re.compile(r'''(?:from\s*|import\s*\(\s*|import\s*)['"](\.[^'"]+)['"]''')
    pending = [
        'src/js/static-net-filtering.js',
        'src/js/static-filtering-io.js',
        'src/js/text-utils.js',
        'src/js/hnswitches.js',
        'src/js/url-net-filtering.js',
    ]
    files = {}
    source_root = source.resolve()
    while pending:
        relative = pending.pop()
        if relative in files:
            continue
        path = (source / relative).resolve()
        # Git tree paths must always use forward slashes even on Windows hosts.
        relative = path.relative_to(source_root).as_posix()
        data = subprocess.check_output([git, '-C', str(source), 'show', f'{COMMIT}:{relative}'])
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[relative] = hashlib.sha256(data).hexdigest()
        for ref in imports.findall(data.decode('utf-8')):
            dependency = (path.parent / ref).resolve().relative_to(source_root).as_posix()
            if dependency not in files:
                pending.append(dependency)

    p = DEST / 'provenance.json'
    manifest = json.loads(p.read_text(encoding='utf-8'))
    manifest['files'].update(files)
    platform_compat.write_text_lf(p, json.dumps(manifest, indent=2) + '\n')
    print(f'Vendored {len(files)} unmodified modules at {COMMIT}')


if __name__ == '__main__':
    main()
