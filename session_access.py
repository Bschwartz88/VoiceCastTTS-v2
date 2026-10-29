"""Per-launch local session capability; no network or third-party dependencies."""
import json
import os
import secrets
from pathlib import Path

SESSION_FILE = Path(__file__).resolve().parent / '.superpowers' / 'studio-session.json'
_sessions = {}

def register(port, token):
    _sessions[port] = token

def launch_url(port):
    token = _sessions.get(port)
    if not token:
        raise RuntimeError('No active VoiceCastTTS v2 session for this port.')
    return f'http://127.0.0.1:{port}/#session={token}'

def write_session(port, token):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SESSION_FILE.with_suffix('.tmp')
    # Inherits the private project directory ACL on Windows; mode protects Unix copies.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({'port': port, 'token': token}, f)
    os.replace(temp, SESSION_FILE)

def read_session(port):
    try:
        with SESSION_FILE.open(encoding='utf-8') as f:
            data = json.loads(f.read(1024))
        token = data.get('token')
        if data.get('port') == port and isinstance(token, str) and len(token) == 43:
            return token
    except (OSError, ValueError, AttributeError):
        pass
    return None

def clear_session(token):
    try:
        data = json.loads(SESSION_FILE.read_text(encoding='utf-8'))
        if secrets.compare_digest(str(data.get('token', '')), token):
            SESSION_FILE.unlink()
    except (OSError, ValueError, AttributeError):
        pass
