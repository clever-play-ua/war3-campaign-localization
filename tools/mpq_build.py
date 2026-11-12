"""Build a plain Warcraft III map (.w3x) from a folder of extracted members.

    python tools/mpq_build.py <src_dir> <out.w3x> [--name MAPNAME] [--drop NAME ...] [--put NAME=FILE ...]

Writes a 512-byte HM3W header followed by an MPQ v1 archive: every member zlib-compressed in 4096-byte sectors
with a sector offset table, a fresh (listfile), no (attributes) and no signature. Member names use backslashes,
exactly as they appear in the folder relative to <src_dir>. Used to turn a decrypted Forsaken Kingdom .w3xd into a
loadable .w3x (the game refuses compiled war3map.luac outside .w3xd maps, so the decompiled war3map.lua goes in).
"""
import argparse, os, struct, sys, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from w3mpq import hash_string, encrypt  # noqa: E402

F_EXISTS, F_COMPRESS = 0x80000000, 0x00000200
SECTOR_SHIFT = 3
SECTOR = 512 << SECTOR_SHIFT


def _store(data):
    nsec = max(1, (len(data) + SECTOR - 1) // SECTOR)
    secs = []
    for i in range(nsec):
        raw = data[i * SECTOR:(i + 1) * SECTOR]
        comp = b"\x02" + zlib.compress(raw, 6)
        secs.append(comp if len(comp) < len(raw) else raw)
    table = [(nsec + 1) * 4]
    for s in secs:
        table.append(table[-1] + len(s))
    return struct.pack("<%dI" % (nsec + 1), *table) + b"".join(secs)


def build(members, map_name="Map", players=1):
    """members: list of (name, bytes); returns the bytes of a .w3x."""
    names = [n for n, _ in members]
    listfile = ("\r\n".join(names) + "\r\n").encode("utf-8")
    members = list(members) + [("(listfile)", listfile)]
    n = len(members)
    ht_size = 16
    while ht_size < n * 2:
        ht_size *= 2
    blocks, body = [], bytearray()
    pos = 32
    for name, data in members:
        if len(data) == 0:
            blocks.append((pos, 0, 0, F_EXISTS))
            continue
        st = _store(data)
        blocks.append((pos, len(st), len(data), F_EXISTS | F_COMPRESS))
        body += st
        pos += len(st)
    ht = [(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF)] * ht_size
    for bi, (name, _) in enumerate(members):
        idx = hash_string(name, 0) % ht_size
        while ht[idx][4] != 0xFFFFFFFF:
            idx = (idx + 1) % ht_size
        ht[idx] = (hash_string(name, 1), hash_string(name, 2), 0, 0, bi)
    ht_bytes = encrypt(b"".join(struct.pack("<IIHHI", *e) for e in ht), hash_string("(hash table)", 3))
    bt_bytes = encrypt(b"".join(struct.pack("<IIII", *e) for e in blocks), hash_string("(block table)", 3))
    ht_pos = pos
    bt_pos = ht_pos + len(ht_bytes)
    arch = bt_pos + len(bt_bytes)
    mpq = struct.pack("<4sIIHHIIII", b"MPQ\x1a", 32, arch, 0, SECTOR_SHIFT, ht_pos, bt_pos, ht_size, len(blocks))
    mpq += bytes(body) + ht_bytes + bt_bytes
    hdr = (b"HM3W" + b"\0" * 4 + map_name.encode("utf-8") + b"\0" + struct.pack("<II", 0, players)).ljust(512, b"\0")
    return hdr + mpq


def collect(src, drop=(), put=None):
    put = dict(put or {})
    drop = {d.lower() for d in drop} | {"(listfile)", "(attributes)", "(signature)"}
    out, seen = [], set()
    for root, _, files in os.walk(src):
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(root, f), src).replace("/", "\\")
            if rel.lower() in drop:
                continue
            data = put.pop(rel, None)
            if data is None:
                data = open(os.path.join(root, f), "rb").read()
            out.append((rel, data))
            seen.add(rel.lower())
    for rel, data in put.items():
        out.append((rel, data))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--name", default="Map")
    ap.add_argument("--drop", nargs="*", default=[])
    ap.add_argument("--put", nargs="*", default=[], help="NAME=FILE, NAME with backslashes")
    a = ap.parse_args()
    put = {}
    for p in a.put:
        k, v = p.split("=", 1)
        put[k.replace("/", "\\")] = open(v, "rb").read()
    members = collect(a.src, a.drop, put)
    data = build(members, a.name)
    open(a.out, "wb").write(data)
    print("written", a.out, len(data), "bytes,", len(members), "members")


if __name__ == "__main__":
    main()
