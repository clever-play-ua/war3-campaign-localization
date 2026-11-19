"""Loading screen fields of war3map.w3i (map info): find and set the loading screen text, title and subtitle.

The game draws these three strings over the loading screen (also over a custom loading screen model): title small on
top, subtitle large under it, text below. Values are usually "TRIGSTR_n" references into war3map.wts.

w3i layout up to these fields (format 25+, Reforged writes 31..39):
    int version, [int saves, int editor version] (v16+), [4 x int game version] (v27+),
    str name, str author, str description, str players recommended,
    8 x float camera bounds, 4 x int camera complements, int playable width, int playable height, int flags,
    char main ground type, int campaign background number, int loading screen number, str loading screen model path,
    str loading screen TEXT, str loading screen TITLE, str loading screen SUBTITLE, ...
Everything after is read sequentially by the game, so replacing these strings with longer ones is safe.
"""
import struct


def _cstr(b, o):
    e = b.index(b"\0", o)
    return b[o:e], e + 1


def loading_fields(w3i):
    """-> (offset of TEXT, offset after SUBTITLE, model path, [text, title, subtitle] as bytes)."""
    o = 0
    ver = struct.unpack_from("<i", w3i, o)[0]; o += 4
    if ver >= 16:
        o += 8
    if ver >= 27:
        o += 16
    for _ in range(4):
        _, o = _cstr(w3i, o)
    o += 8 * 4 + 4 * 4 + 4 * 3          # camera bounds, complements, playable w/h, flags
    o += 1                               # main ground type
    o += 8                               # campaign background number, loading screen number
    model, o = _cstr(w3i, o)
    start, vals = o, []
    for _ in range(3):
        v, o = _cstr(w3i, o)
        vals.append(v)
    return start, o, model.decode("utf-8", "replace"), vals


def set_loading_text(w3i, text, title, subtitle):
    start, end, _, _ = loading_fields(w3i)
    new = b"".join(s.encode("utf-8") + b"\0" for s in (text, title, subtitle))
    return w3i[:start] + new + w3i[end:]
