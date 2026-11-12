"""Minimal read-only CASC reader for a local Warcraft III install (no external tools).

    python tools/casc.py <install> list <substring>          # list root paths containing substring
    python tools/casc.py <install> get <path> <out_file>     # extract one file

Method: .build.info -> build config -> root/encoding ckeys -> encoding file -> ekey -> .idx -> data.NNN -> BLTE.
"""
import sys, os, re, zlib, struct, glob


class Casc:
    def __init__(self, install):
        self.install = install
        self.data = os.path.join(install, "Data")
        self._load_build()
        self._load_idx()
        self._load_encoding()
        self._root = None

    def _load_build(self):
        lines = open(os.path.join(self.install, ".build.info"), encoding="utf8").read().splitlines()
        cols = [c.split("!")[0] for c in lines[0].split("|")]
        row = dict(zip(cols, lines[1].split("|")))
        k = row["Build Key"]
        cfg = open(os.path.join(self.data, "config", k[:2], k[2:4], k), encoding="utf8").read()
        self.cfg = {}
        for ln in cfg.splitlines():
            if "=" in ln and not ln.startswith("#"):
                a, b = ln.split("=", 1)
                self.cfg[a.strip()] = b.strip().split()

    def _load_idx(self):
        best = {}
        for p in glob.glob(os.path.join(self.data, "data", "*.idx")):
            n = os.path.basename(p)
            b = n[:2]
            if b not in best or n > best[b]:
                best[b] = n
        self.idx = {}
        for n in best.values():
            d = open(os.path.join(self.data, "data", n), "rb").read()
            hs = struct.unpack_from("<I", d, 0)[0]
            pos = (8 + hs + 15) & ~15
            ds = struct.unpack_from("<I", d, pos)[0]
            pos += 8
            for o in range(pos, pos + ds, 18):
                key = d[o:o + 9]
                off = int.from_bytes(d[o + 9:o + 14], "big")
                size = struct.unpack_from("<I", d, o + 14)[0]
                self.idx[key] = (off >> 30, off & 0x3FFFFFFF, size)

    def read_ekey(self, ekey):
        e = self.idx.get(ekey[:9])
        if not e:
            raise KeyError(ekey.hex())
        arc, off, size = e
        with open(os.path.join(self.data, "data", "data.%03d" % arc), "rb") as f:
            f.seek(off + 30)
            return blte(f.read(size - 30))

    def _load_encoding(self):
        ck, ek = self.cfg["encoding"][:2]
        d = self.read_ekey(bytes.fromhex(ek))
        assert d[:2] == b"EN"
        cps = struct.unpack_from(">H", d, 5)[0] * 1024
        cpc = struct.unpack_from(">I", d, 9)[0]
        espec = struct.unpack_from(">I", d, 18)[0]
        pos = 22 + espec + cpc * 32
        self.c2e = {}
        for i in range(cpc):
            pg = d[pos + i * cps: pos + (i + 1) * cps]
            o = 0
            while o + 38 <= len(pg):
                n = pg[o]
                if n == 0:
                    break
                c = pg[o + 6:o + 22]
                self.c2e[c] = pg[o + 22:o + 38]
                o += 22 + 16 * n

    def read_ckey(self, ckey_hex):
        return self.read_ekey(self.c2e[bytes.fromhex(ckey_hex)])

    def root(self):
        if self._root is None:
            txt = self.read_ckey(self.cfg["root"][0]).decode("utf8", "replace")
            self._root = {}
            for ln in txt.splitlines():
                p = ln.split("|")
                if len(p) >= 2:
                    self._root[p[0]] = p[1]
        return self._root

    def get(self, path):
        return self.read_ckey(self.root()[path])


def blte(b):
    assert b[:4] == b"BLTE", b[:8]
    hs = struct.unpack_from(">I", b, 4)[0]
    if hs == 0:
        return _chunk(b[8:])
    n = int.from_bytes(b[9:12], "big")
    out, pos = [], hs
    for i in range(n):
        cs = struct.unpack_from(">I", b, 12 + i * 24)[0]
        out.append(_chunk(b[pos:pos + cs]))
        pos += cs
    return b"".join(out)


def _chunk(c):
    m = c[:1]
    if m == b"N":
        return c[1:]
    if m == b"Z":
        return zlib.decompress(c[1:])
    if m == b"F":
        return blte(c[1:])
    raise ValueError("BLTE mode %r (encrypted?)" % m)


if __name__ == "__main__":
    c = Casc(sys.argv[1])
    if sys.argv[2] == "list":
        s = sys.argv[3].lower()
        for p in sorted(c.root()):
            if s in p.lower():
                print(p)
    elif sys.argv[2] == "get":
        open(sys.argv[4], "wb").write(c.get(sys.argv[3]))
