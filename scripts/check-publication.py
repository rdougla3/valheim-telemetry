#!/usr/bin/env python3
"""Check files eligible for a source release, without inspecting private runtime data."""
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
if (root / '.git').exists():
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            cwd=root, check=True, capture_output=True)
    paths = sorted(set(p.decode() for p in result.stdout.split(b'\0') if p))
else:
    # A source archive has no Git history. Check every file instead.
    paths = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file())
blocked_directories = [
    'valheim/server/', 'valheim/data/', 'valheim/steamcmd/', 'valheim/home/',
    'valheim/runtime/', 'valheim/backups/', 'valheim/staging/',
    'valheim-dashboard/data/', 'valheim-dashboard/vendor/',
    'valheim-dashboard/plugin/bin/', 'valheim-dashboard/plugin/obj/',
]
blocked_suffixes = {'.sqlite', '.sqlite3', '.db', '.db2', '.fwl', '.fwl2', '.chunk',
                    '.ok', '.log', '.jsonl', '.dll', '.so', '.exe', '.zip', '.gz',
                    '.png', '.jpg', '.jpeg', '.webp', '.pyc', '.pem', '.key'}
patterns = {
    'absolute home-directory path': re.compile(rb'/home/[A-Za-z0-9_.-]+/'),
    'real-range Steam account ID': re.compile(rb'7656119[0-9]{10}'),
    'private-key material': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}
failures = []
for name in paths:
    path = root / name
    if path.is_symlink():
        failures.append((name, 'symlinks must be reviewed before publication'))
        continue
    if name.startswith(tuple(blocked_directories)) or path.suffix.lower() in blocked_suffixes:
        failures.append((name, 'runtime/build/binary data in source release'))
        continue
    if path.name.endswith('.env') or (path.name.startswith('.env.') and path.name != '.env.example'):
        failures.append((name, 'private environment configuration'))
        continue
    if not path.is_file():
        continue
    content = path.read_bytes()
    for label, pattern in patterns.items():
        if pattern.search(content):
            failures.append((name, label))
if failures:
    for name, reason in failures:
        print(f'{name}: {reason}', file=sys.stderr)
    sys.exit(1)
print(f'Publication checks passed for {len(paths)} source files.')
print('Review new deployment-specific content before every release; this check is not a complete PII detector.')
