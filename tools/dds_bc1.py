"""PNG/JPG -> DDS (DXT1 / BC1, full mip chain), the format of the campaign loading screens (1920x1080, 11 levels).

    python tools/dds_bc1.py in.png out.dds [in2.png out2.dds ...]

Encoder: per 4x4 block the endpoints lie on the block's main colour axis (power iteration), 4-colour mode, nearest
palette index per pixel. Needs numpy and Pillow (only to make the files; the campaign build just copies the .dds).
"""
import struct, sys
import numpy as np
from PIL import Image


def _rgb565(c):
    c = c.astype(np.int32)
    return ((c[..., 0] >> 3) << 11) | ((c[..., 1] >> 2) << 5) | (c[..., 2] >> 3)


def _unpack565(v):
    r = ((v >> 11) & 31) * 255 // 31
    g = ((v >> 5) & 63) * 255 // 63
    b = (v & 31) * 255 // 31
    return np.stack([r, g, b], -1).astype(np.int32)


def bc1(img):
    """RGB uint8 array (h, w, 3) -> BC1 data of one mip level."""
    h, w, _ = img.shape
    ph, pw = (h + 3) // 4 * 4, (w + 3) // 4 * 4
    pad = np.zeros((ph, pw, 3), np.uint8)
    pad[:h, :w] = img
    pad[h:, :w] = img[h - 1:h, :w]
    pad[:, w:] = pad[:, w - 1:w]
    blocks = pad.reshape(ph // 4, 4, pw // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3).astype(np.float32)
    mean = blocks.mean(1, keepdims=True)
    cen = blocks - mean
    cov = np.einsum("bni,bnj->bij", cen, cen)
    axis = np.ones((len(blocks), 3), np.float32)
    for _ in range(6):
        axis = np.einsum("bij,bj->bi", cov, axis)
        axis /= np.linalg.norm(axis, axis=1, keepdims=True) + 1e-6
    proj = np.einsum("bni,bi->bn", cen, axis)
    lo = mean[:, 0] + axis * proj.min(1, keepdims=True)
    hi = mean[:, 0] + axis * proj.max(1, keepdims=True)
    c0 = _rgb565(np.clip(hi + 0.5, 0, 255))
    c1 = _rgb565(np.clip(lo + 0.5, 0, 255))
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    eq = c0 == c1
    c1 = np.where(eq & (c0 > 0), c0 - 1, c1)
    c0 = np.where(eq & (c0 == 0), 1, c0)
    p0, p1 = _unpack565(c0), _unpack565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], 1)
    idx = ((blocks[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1).argmin(-1).astype(np.uint32)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    out = np.zeros((len(blocks), 8), np.uint8)
    out[:, 0:2] = np.frombuffer(c0.astype("<u2").tobytes(), np.uint8).reshape(-1, 2)
    out[:, 2:4] = np.frombuffer(c1.astype("<u2").tobytes(), np.uint8).reshape(-1, 2)
    out[:, 4:8] = np.frombuffer(bits.astype("<u4").tobytes(), np.uint8).reshape(-1, 4)
    return out.tobytes()


def header(w, h, levels):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000          # caps height width pixelformat mipmapcount linearsize
    linear = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
    caps = 0x1000 | 0x400000 | 0x8                                   # texture mipmap complex
    return (b"DDS " + struct.pack("<7I", 124, flags, h, w, linear, 0, levels) + b"\0" * 44
            + struct.pack("<2I4s5I", 32, 0x4, b"DXT1", 0, 0, 0, 0, 0)
            + struct.pack("<5I", caps, 0, 0, 0, 0))


def encode(path_in):
    im = Image.open(path_in).convert("RGB")
    w, h = im.size
    levels = max(w, h).bit_length()
    data = b"".join(bc1(np.asarray(im if lv == 0 else im.resize((max(1, w >> lv), max(1, h >> lv)), Image.LANCZOS)))
                    for lv in range(levels))
    return header(w, h, levels) + data


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or len(a) % 2:
        sys.exit(__doc__)
    for src, dst in zip(a[::2], a[1::2]):
        d = encode(src)
        open(dst, "wb").write(d)
        print("%s -> %s (%d bytes)" % (src, dst, len(d)))
