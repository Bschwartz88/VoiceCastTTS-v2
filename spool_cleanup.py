"""Reclaim only inactive generated spools; never traverse links or unknown content."""
import os
from pathlib import Path
import time

MAX_AGE = 7 * 24 * 3600

class Lease:
    def __init__(self, folder):
        self.file = open(Path(folder) / '.owner.lock', 'a+b')
        try:
            self.file.seek(0, 2)
            if self.file.tell() == 0:
                self.file.write(b'1'); self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.file.close()
            raise
    def close(self):
        self.file.close()  # releases the operating-system file lock, including after a crash

def prune(root):
    root = Path(root)
    base = root.resolve()
    if not root.is_dir() or root.is_symlink() or root.resolve() != base:
        return
    for folder in root.iterdir():
        if len(folder.name) != 32 or any(c not in '0123456789abcdef' for c in folder.name):
            continue
        lease = None
        try:
            if folder.is_symlink() or folder.resolve().parent != base or not folder.is_dir():
                continue
            marker = folder / '.owner.lock'
            # Legacy unmarked directories are left for explicit owner cleanup.
            if not marker.is_file() or marker.is_symlink() or time.time()-marker.stat().st_mtime < MAX_AGE:
                continue
            lease = Lease(folder)  # active owners deny this even if the directory is old
            children = list(folder.iterdir())
            if any(p.is_symlink() or p.resolve().parent != folder.resolve() or not p.is_file() or
                   (p.name != '.owner.lock' and not (p.suffix == '.wav' and len(p.stem) == 8 and p.stem.isdigit()))
                   for p in children):
                continue
            for p in children:
                if p.name != '.owner.lock':
                    p.unlink()
            lease.close(); lease = None
            marker.unlink()
            folder.rmdir()
        except OSError:
            pass
        finally:
            if lease is not None:
                lease.close()
