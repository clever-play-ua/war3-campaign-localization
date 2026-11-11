"""
MPQ reader/writer for Warcraft III campaigns (.w3n) and maps (.w3x): reads any v0
MPQ, opens embedded maps in place, and rewrites an archive byte-for-byte the way
World Editor saves it (embedded maps stored raw), so no manual resave is needed.
Only replaces files that already exist in the archive.
"""

import mmap
import struct
import zlib
from pathlib import Path

_M = 0xFFFFFFFF
_CRYPT = [0] * 0x500


def _prepare_crypt_table():
    seed = 0x00100001
    for i in range(0x100):
        idx = i
        for _ in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            hi = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            _CRYPT[idx] = hi | (seed & 0xFFFF)
            idx += 0x100


_prepare_crypt_table()

F_IMPLODE = 0x00000100
F_COMPRESS = 0x00000200
F_ENCRYPTED = 0x00010000
F_FIX_KEY = 0x00020000
F_SINGLE_UNIT = 0x01000000
F_SECTOR_CRC = 0x04000000
F_EXISTS = 0x80000000

RAW_STORED_EXTENSIONS = (".w3x", ".w3m")


def hash_string(name, hash_type):
    s1, s2 = 0x7FED7FED, 0xEEEEEEEE
    for ch in name.replace("/", "\\").upper().encode("utf-8"):
        s1 = (_CRYPT[(hash_type << 8) + ch] ^ ((s1 + s2) & _M)) & _M
        s2 = (ch + s1 + s2 + (s2 << 5) + 3) & _M
    return s1


def _crypt(data, key, decrypting):
    n = len(data) // 4
    vals = struct.unpack(f"<{n}I", data[:n * 4])
    out = []
    seed = 0xEEEEEEEE
    for v in vals:
        seed = (seed + _CRYPT[0x400 + (key & 0xFF)]) & _M
        r = v ^ ((key + seed) & _M)
        out.append(r)
        key = ((((~key) << 0x15) + 0x11111111) & _M) | (key >> 0x0B)
        seed = ((r if decrypting else v) + seed + (seed << 5) + 3) & _M
    return struct.pack(f"<{n}I", *out) + bytes(data[n * 4:])


def decrypt(data, key):
    return _crypt(data, key, True)


def encrypt(data, key):
    return _crypt(data, key, False)


def _decompress_sector(buf):
    mask = buf[0]
    if mask & ~0x12:
        raise NotImplementedError(f"compression mask 0x{mask:02X}")
    data = buf[1:]
    if mask & 0x10:
        import bz2
        data = bz2.decompress(data)
    if mask & 0x02:
        data = zlib.decompress(data)
    return data


class MpqArchive:
    def __init__(self, path):
        self._file = open(path, "rb")
        self.mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self.base = next(p for p in range(0, len(self.mm), 0x200) if self.mm[p:p + 4] == b"MPQ\x1a")
        (_, self.header_size, _, self.format_version, self.sector_shift,
         self.ht_pos, self.bt_pos, self.ht_size, self.bt_size) = struct.unpack_from("<4sIIHHIIII", self.mm, self.base)
        if self.format_version != 0:
            raise NotImplementedError(f"MPQ format v{self.format_version}")
        self.sector_size = 512 << self.sector_shift
        ht_start = self.base + self.ht_pos
        self.hash_table_raw = bytes(self.mm[ht_start:ht_start + self.ht_size * 16])
        ht = decrypt(self.hash_table_raw, hash_string("(hash table)", 3))
        bt_start = self.base + self.bt_pos
        bt = decrypt(bytes(self.mm[bt_start:bt_start + self.bt_size * 16]), hash_string("(block table)", 3))
        self.hash_table = [struct.unpack_from("<IIHHI", ht, i * 16) for i in range(self.ht_size)]
        self.block_table = [struct.unpack_from("<IIII", bt, i * 16) for i in range(self.bt_size)]

    def close(self):
        self.mm.close()
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def find_block(self, name):
        a, b = hash_string(name, 1), hash_string(name, 2)
        idx = hash_string(name, 0) % self.ht_size
        for _ in range(self.ht_size):
            n1, n2, _, _, bi = self.hash_table[idx]
            if bi == 0xFFFFFFFF:
                return None
            if n1 == a and n2 == b and bi != 0xFFFFFFFE:
                return bi
            idx = (idx + 1) % self.ht_size
        return None

    def raw_block(self, bi):
        off, csize, _, _ = self.block_table[bi]
        start = self.base + off
        return self.mm[start:start + csize]

    def file_key(self, name, offset, file_size, flags):
        key = hash_string(name.replace("/", "\\").split("\\")[-1], 3)
        if flags & F_FIX_KEY:
            key = ((key + offset) ^ file_size) & _M
        return key

    def read_block(self, bi, name=None):
        off, csize, fsize, flags = self.block_table[bi]
        raw = self.raw_block(bi)
        if flags & F_IMPLODE:
            raise NotImplementedError("PKWARE implode")
        key = None
        if flags & F_ENCRYPTED:
            if name is None:
                raise ValueError(f"block {bi} is encrypted; its name is required")
            key = self.file_key(name, off, fsize, flags)
        if flags & F_SINGLE_UNIT:
            data = decrypt(raw, key) if key is not None else bytes(raw)
            return _decompress_sector(data) if (flags & F_COMPRESS and csize < fsize) else data[:fsize]
        ss = self.sector_size
        nsec = (fsize + ss - 1) // ss
        if not flags & F_COMPRESS:
            if key is None:
                return bytes(raw[:fsize])
            return b"".join(decrypt(raw[i * ss:(i + 1) * ss], (key + i) & _M) for i in range(nsec))[:fsize]
        ntab = nsec + 1 + (1 if flags & F_SECTOR_CRC else 0)
        tab_raw = bytes(raw[:ntab * 4])
        if key is not None:
            tab_raw = decrypt(tab_raw, (key - 1) & _M)
        offs = struct.unpack(f"<{ntab}I", tab_raw)
        out = bytearray()
        for i in range(nsec):
            sec = bytes(raw[offs[i]:offs[i + 1]])
            if key is not None:
                sec = decrypt(sec, (key + i) & _M)
            if len(sec) < min(ss, fsize - i * ss):
                sec = _decompress_sector(sec)
            out += sec
        return bytes(out)

    def read(self, name):
        bi = self.find_block(name)
        return None if bi is None else self.read_block(bi, name)

    def names(self):
        listfile = self.read("(listfile)") or b""
        names = [n for n in listfile.decode("utf-8", "replace").replace("\r", "\n").split("\n") if n.strip()]
        return names + ["(listfile)", "(attributes)", "(signature)"]


def _encode(data, sector_size, compress, key):
    """Stored bytes for one block, the way Storm/World Editor writes them."""
    nsec = (len(data) + sector_size - 1) // sector_size
    sectors = [data[i * sector_size:(i + 1) * sector_size] for i in range(nsec)]
    if compress:
        packed = []
        for sec in sectors:
            comp = b"\x02" + zlib.compress(sec, 6)
            packed.append(comp if len(comp) < len(sec) else sec)
        sectors = packed
    if key is not None:
        sectors = [encrypt(sec, (key + i) & _M) for i, sec in enumerate(sectors)]
    if not compress:
        return b"".join(sectors)
    table = [(nsec + 1) * 4]
    for sec in sectors:
        table.append(table[-1] + len(sec))
    table_bytes = struct.pack(f"<{nsec + 1}I", *table)
    if key is not None:
        table_bytes = encrypt(table_bytes, (key - 1) & _M)
    return table_bytes + b"".join(sectors)


def _rekey(raw, flags, fsize, sector_size, old_key, new_key):
    """Same stored bytes, re-encrypted for a block that moved (offset-keyed)."""
    raw = bytes(raw)
    if flags & (F_SINGLE_UNIT | F_SECTOR_CRC):
        raise NotImplementedError(f"re-keying flags 0x{flags:08X}")
    nsec = (fsize + sector_size - 1) // sector_size
    if not flags & F_COMPRESS:
        return b"".join(encrypt(decrypt(raw[i * sector_size:(i + 1) * sector_size], (old_key + i) & _M),
                                (new_key + i) & _M) for i in range(nsec))
    table_plain = decrypt(raw[:(nsec + 1) * 4], (old_key - 1) & _M)
    offs = struct.unpack(f"<{nsec + 1}I", table_plain)
    parts = [encrypt(table_plain, (new_key - 1) & _M)]
    for i in range(nsec):
        parts.append(encrypt(decrypt(raw[offs[i]:offs[i + 1]], (old_key + i) & _M), (new_key + i) & _M))
    return b"".join(parts)


def rebuild_archive(src, dst, replacements=None, bump_save_counter=True):
    """Writes dst = src with `replacements` ({internal name: new bytes})
    swapped in, laid out exactly like a World Editor save."""
    replacements = dict(replacements or {})
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError("src and dst must differ")

    with MpqArchive(src) as a:
        block_names = {}
        for name in a.names():
            bi = a.find_block(name)
            if bi is not None:
                block_names[bi] = name

        new_data = {}
        for name, data in replacements.items():
            bi = a.find_block(name)
            if bi is None:
                raise KeyError(f"{name!r} is not in {src.name}; only existing files can be replaced")
            new_data[bi] = bytes(data)

        w3f_bi = a.find_block("war3campaign.w3f")
        if bump_save_counter and w3f_bi is not None and w3f_bi not in new_data:
            w3f = bytearray(a.read_block(w3f_bi, "war3campaign.w3f"))
            struct.pack_into("<I", w3f, 4, (struct.unpack_from("<I", w3f, 4)[0] + 1) & _M)
            new_data[w3f_bi] = bytes(w3f)

        attr_bi = a.find_block("(attributes)")
        if attr_bi is not None:
            attrs = bytearray(a.read_block(attr_bi, "(attributes)"))
            version, attr_flags = struct.unpack_from("<II", attrs, 0)
            if version != 100:
                raise NotImplementedError(f"(attributes) version {version}")
            if attr_flags & ~0x1:
                raise NotImplementedError(f"(attributes) flags 0x{attr_flags:X}: only CRC32 is supported")
            for bi, data in new_data.items():
                struct.pack_into("<I", attrs, 8 + 4 * bi, zlib.crc32(data) & _M)
            new_data[attr_bi] = bytes(attrs)

        tmp = dst.with_name(dst.name + ".tmp")
        new_bt = []
        with open(tmp, "wb") as out:
            out.write(a.mm[:a.base])
            out.write(b"\0" * 32)
            pos = 32
            for bi, (off, csize, fsize, flags) in enumerate(a.block_table):
                name = block_names.get(bi)
                if bi in new_data:
                    data = new_data[bi]
                    if name.lower().endswith(RAW_STORED_EXTENSIONS):
                        flags = F_EXISTS
                    elif not flags & F_ENCRYPTED:
                        flags = F_EXISTS | F_COMPRESS
                    key = a.file_key(name, pos, len(data), flags) if flags & F_ENCRYPTED else None
                    stored = _encode(data, a.sector_size, bool(flags & F_COMPRESS), key)
                    fsize = len(data)
                elif flags & F_ENCRYPTED and flags & F_FIX_KEY and pos != off:
                    if name is None:
                        raise ValueError(f"block {bi} is offset-keyed but unnamed; cannot re-key it")
                    stored = _rekey(a.raw_block(bi), flags, fsize, a.sector_size,
                                    a.file_key(name, off, fsize, flags), a.file_key(name, pos, fsize, flags))
                else:
                    stored = a.raw_block(bi)
                out.write(stored)
                new_bt.append((pos, len(stored), fsize, flags))
                pos += len(stored)

            ht_pos = pos
            out.write(a.hash_table_raw)
            bt_pos = ht_pos + a.ht_size * 16
            bt_plain = b"".join(struct.pack("<IIII", *e) for e in new_bt)
            out.write(encrypt(bt_plain, hash_string("(block table)", 3)))
            archive_size = bt_pos + a.bt_size * 16
            out.seek(a.base)
            out.write(struct.pack("<4sIIHHIIII", b"MPQ\x1a", 32, archive_size, 0, a.sector_shift,
                                  ht_pos, bt_pos, a.ht_size, a.bt_size))

    tmp.replace(dst)
    return dst


def open_nested(archive, name):
    """Open an embedded map (stored raw inside a campaign) in place, read-only."""
    import struct
    bi = archive.find_block(name)
    if bi is None:
        raise KeyError(name)
    off = archive.block_table[bi][0]
    inner = MpqArchive.__new__(MpqArchive)
    inner.mm, inner.base = archive.mm, archive.base + off
    if bytes(inner.mm[inner.base:inner.base + 4]) != b"MPQ":
        inner.base += 0x200
    (_, inner.header_size, _, inner.format_version, inner.sector_shift,
     inner.ht_pos, inner.bt_pos, inner.ht_size, inner.bt_size) = struct.unpack_from("<4sIIHHIIII", inner.mm, inner.base)
    inner.sector_size = 512 << inner.sector_shift
    base = inner.base
    ht = decrypt(bytes(inner.mm[base + inner.ht_pos:base + inner.ht_pos + inner.ht_size * 16]), hash_string("(hash table)", 3))
    bt = decrypt(bytes(inner.mm[base + inner.bt_pos:base + inner.bt_pos + inner.bt_size * 16]), hash_string("(block table)", 3))
    inner.hash_table = [struct.unpack_from("<IIHHI", ht, i * 16) for i in range(inner.ht_size)]
    inner.block_table = [struct.unpack_from("<IIII", bt, i * 16) for i in range(inner.bt_size)]
    return inner
