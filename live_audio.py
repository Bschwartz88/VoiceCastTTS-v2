"""v2 live listening spool. PCM WAV pieces stay on disk, not in a RAM queue."""
import shutil
import spool_cleanup
import uuid
from pathlib import Path

class LiveAudioSpool:
    def __init__(self, output_dir):
        self.output_root = Path(output_dir).resolve()
        self.root = self.output_root / 'live-listening'
        self.current = None
        self.lease = None
        self.count = 0
        self.bytes_written = 0

    def begin(self):
        if self.root.resolve().parent != self.output_root:
            raise OSError('Live audio directory escapes output folder')
        self.clear()
        self.root.mkdir(parents=True, exist_ok=True)
        spool_cleanup.prune(self.root)
        self.current = self.root / uuid.uuid4().hex
        self.current.mkdir()
        self.lease = spool_cleanup.Lease(self.current)
        self.count = 0
        self.bytes_written = 0

    def clear(self):
        if self.current is not None:
            target = self.current.resolve()
            # Recursive removal is limited to this object's exact generated directory.
            if target.parent != self.root.resolve() or len(target.name) != 32 or any(c not in '0123456789abcdef' for c in target.name):
                raise ValueError('Invalid live audio directory')
            if self.lease is not None:
                self.lease.close()
                self.lease = None
            shutil.rmtree(target, ignore_errors=True)
            self.current = None

    def append(self, audio, rate, encode):
        """Yield at most two seconds per file; keep only tiny URL metadata in events."""
        if self.current is None:
            return
        for start in range(0, len(audio), rate * 2):
            piece = audio[start:start + rate * 2]
            filename = f'{self.count:08d}.wav'
            encoded = encode(piece, rate)
            if self.bytes_written + len(encoded) > 256 * 1024 * 1024:
                raise OSError('Live listening reached its 256 MiB temporary-file limit')
            (self.current / filename).write_bytes(encoded)
            self.bytes_written += len(encoded)
            self.count += 1
            yield {'url': f'/output/live-listening/{self.current.name}/{filename}',
                   'duration': len(piece) / rate}
