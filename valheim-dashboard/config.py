"""Local deployment configuration. Only PUBLIC_DISPLAY is rendered into HTML."""
import os
from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parent


def read_env(path):
    values = {}
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)', line)
        if not match:
            raise ValueError(f'Invalid configuration assignment on line {number}')
        tokens = shlex.split(match[2], comments=True)
        if len(tokens) > 1:
            raise ValueError(f'Quote configuration values containing spaces (line {number})')
        values[match[1]] = tokens[0] if tokens else ''
    return values


config_path = Path(os.environ.get('VALHEIM_ENV_FILE', ROOT.parent / '.env'))
if config_path.is_file():
    for key, value in read_env(config_path).items():
        os.environ.setdefault(key, value)
# Docker Compose supplies these same values from the ignored .env file.
if 'VALHEIM_CONTAINER_NAME' in os.environ:
    os.environ.setdefault('VALHEIM_JOURNAL_CONTAINER', os.environ['VALHEIM_CONTAINER_NAME'])
DATA_DIR = Path(os.environ.get('VALHEIM_DASHBOARD_DATA', ROOT / 'data')).expanduser()
SAVE_DIR = Path(os.environ.get('VALHEIM_SAVE_DIR', ROOT.parent / 'valheim' / 'data')).expanduser()
WORLD_NAME = os.environ.get('WORLD_NAME', 'MyWorld')
if not WORLD_NAME or WORLD_NAME in {'.', '..'} or '/' in WORLD_NAME or '\\' in WORLD_NAME:
    raise ValueError('WORLD_NAME must be a single directory name')
WORLD_PATH = Path(os.environ.get('VALHEIM_WORLD_PATH', SAVE_DIR / 'worlds_local' / WORLD_NAME)).expanduser()
URL_PREFIXES = tuple('/' + p.strip('/') for p in os.environ.get('DASHBOARD_URL_PREFIXES', '/valheim').split(',') if p.strip('/'))
PUBLIC_DISPLAY = {
    'DASHBOARD_TITLE': os.environ.get('DASHBOARD_TITLE') or os.environ.get('SERVER_NAME', 'Valheim Server'),
    'DASHBOARD_BRAND': os.environ.get('DASHBOARD_BRAND', 'Valheim'),
    'DASHBOARD_SUBTITLE': os.environ.get('DASHBOARD_SUBTITLE', 'The realm chronicle'),
    'DASHBOARD_TIMEZONE': os.environ.get('DASHBOARD_TIMEZONE', os.environ.get('TZ', 'UTC')),
    'DASHBOARD_LOCALE': os.environ.get('DASHBOARD_LOCALE', 'en-US'),
    'DASHBOARD_DESCRIPTION': os.environ.get('DASHBOARD_DESCRIPTION', 'Player activity and world progress.'),
}
