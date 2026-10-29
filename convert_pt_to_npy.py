"""Convert Kokoro .pt voicepacks to .npy without PyTorch.
Usage: python convert_pt_to_npy.py input.pt output.npy
"""
import sys, zipfile
import numpy as np

src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as z:
    data = [n for n in z.namelist() if "/data/" in n and not n.endswith("/")]
    if len(data) != 1:
        sys.exit(f"Unexpected .pt layout in {src}: {data}")
    info = z.getinfo(data[0])
    if info.file_size > 64 * 1024 * 1024:
        sys.exit('Voicepack exceeds the 64 MiB decompressed limit')
    with z.open(info) as member:
        raw = member.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        sys.exit('Voicepack exceeds the 64 MiB decompressed limit')

arr = np.frombuffer(raw, dtype="<f4")
if arr.size % 256:
    sys.exit(f"{src} is not a Kokoro voicepack ({arr.size} values)")
arr = arr.reshape(-1, 1, 256).copy()
np.save(dst, arr)
print(f"Saved {dst}  shape={arr.shape}")
