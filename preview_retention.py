"""Only disposable generated preview WAVs are eligible for automatic cache pruning."""
from pathlib import Path
import time
MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 512
MAX_AGE = 7 * 24 * 3600

def prune(folder, keep=None):
    root = Path(folder).resolve()
    if not root.is_dir():
        return
    entries = []
    now = time.time()
    for path in root.glob('preview_*.wav'):
        try:
            if path.is_symlink() or path.resolve().parent != root or not path.is_file():
                continue
            info = path.stat()
            entries.append((info.st_mtime, info.st_size, path))
        except OSError:
            continue
    entries.sort()
    total = sum(size for _, size, _ in entries)
    count = len(entries)
    for modified, size, path in entries:
        if keep is not None and path == Path(keep):
            continue
        if now - modified <= MAX_AGE and total <= MAX_BYTES and count <= MAX_FILES:
            continue
        try:
            path.unlink()
            total -= size
            count -= 1
        except OSError:
            pass
