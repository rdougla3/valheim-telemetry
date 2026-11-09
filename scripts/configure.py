#!/usr/bin/env python3
"""Create a private local configuration and empty runtime directories."""
from pathlib import Path
import grp
import os
import secrets
import sys

root = Path(__file__).resolve().parent.parent
config = root / '.env'
if not config.exists():
    text = (root / '.env.example').read_text()
    text = text.replace('CHANGE_ME_BEFORE_STARTING', secrets.token_hex(16))
    text = text.replace('RUN_UID=1000', f'RUN_UID={os.getuid()}')
    text = text.replace('RUN_GID=1000', f'RUN_GID={os.getgid()}')
    try:
        gid = grp.getgrnam('systemd-journal').gr_gid
    except KeyError:
        sys.exit('This Docker setup requires a Linux host with systemd-journald.')
    text = text.replace('JOURNAL_GID=999', f'JOURNAL_GID={gid}')
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(text)
    print('Created .env with a generated password. Edit it before starting the server.')
else:
    config.chmod(0o600)
    print('Kept existing .env.')
for directory in ['valheim/server', 'valheim/data', 'valheim/steamcmd', 'valheim/home', 'valheim-dashboard/data', 'valheim/backups']:
    (root / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
print('Empty runtime directories are ready.')
