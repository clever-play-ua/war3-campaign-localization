"""Reference database of Warcraft III (Reforged, RoC + TFT) strings: English next to one translation, for lookup and
consistency checks while translating campaigns.

    python tools/war3_ref.py build cz --folder "F:\\ЗАГРУЗКИ\\Warcraft III Reforged čeština 0.9" --label "..."
    python tools/war3_ref.py build it --casc itIT          (official locale from the game's CASC storage)
    python tools/war3_ref.py search cz "Holy Light"       (English or translated text, case-insensitive substring)
    python tools/war3_ref.py search cz --regex "^Uther"

Output RoC_TFT/<lang>.json:
    {"lang", "source", "built", "count",
     "strings": {hash: {"en", "tr", "refs": [ref, ...], "variants": {other_tr: [ref, ...]}}}}
hash = sha256(English)[:10], the same hash the campaign string files use, so a campaign string is found by its hash.
ref = "units/HumanUnitStrings.txt|Hblm|Name"  (string file, object id, field)
    | "ui/FrameDef/GlobalStrings.fdf|KEY"
    | "Campaign/Reforged/ROC/Human01.w3x#123"   (map, STRING number in war3map.wts)
The English side always comes from the enUS files of the installed game (CASC), never from the translation package.
"""
import argparse, collections, datetime, hashlib, json, os, re, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
OUT = os.path.join(ROOT, "RoC_TFT")
GAME = r"F:\Games\Warcraft III"
EN = "war3.w3mod:_locales/enus.w3mod:"


def text_hash(t):
    return hashlib.sha256(t.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:10]


def ini(b):
    out, cur = {}, None
    for ln in b.decode("utf-8", "replace").splitlines():
        m = re.match(r"\[(.+?)\]", ln.strip())
        if m:
            cur = m.group(1); continue
        if cur and "=" in ln and not ln.lstrip().startswith("//"):
            k, v = ln.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] == '"':
                v = v[1:-1]
            out[(cur, k.strip())] = v
    return out


def fdf(b):
    return dict(re.findall(r'^\s*(\w+)\s+"((?:[^"\\]|\\.)*)"', b.decode("utf-8", "replace"), re.M))


def wts(b):
    t = b.decode("utf-8-sig", "replace")
    return {int(m.group(1)): m.group(2).replace("\r\n", "\n")
            for m in re.finditer(r"STRING (\d+)\s*(?://[^\n]*\n)?\s*\{\r?\n(.*?)\r?\n\}", t, re.S)}


def map_wts(data):
    from w3mpq import MpqArchive
    f = os.path.join(tempfile.gettempdir(), "war3ref_map")
    with open(f, "wb") as h:
        h.write(data)
    a = MpqArchive(f)
    try:
        s = a.read("war3map.wts")
    finally:
        a.close()
    return wts(s) if s else {}


def pairs_from(casc, low, rel_lower, en_bytes, tr_bytes, display):
    """[(en, tr, ref)] for one file."""
    out = []
    if rel_lower.endswith(".txt"):
        E, T = ini(en_bytes), ini(tr_bytes)
        for k, e in E.items():
            t = T.get(k)
            if e and t:
                out.append((e, t, "%s|%s|%s" % (display, k[0], k[1])))
    elif rel_lower.endswith(".fdf"):
        E, T = fdf(en_bytes), fdf(tr_bytes)
        for k, e in E.items():
            if k in T and e:
                out.append((e, T[k], "%s|%s" % (display, k)))
    else:
        E, T = map_wts(en_bytes), map_wts(tr_bytes)
        for k, e in E.items():
            if k in T and e.strip():
                out.append((e, T[k], "%s#%d" % (display, k)))
    return out


def collect_folder(folder):
    from casc import Casc
    c = Casc(GAME)
    low = {p.lower(): p for p in c.root()}
    rows, missing = [], []
    for dirpath, _, files in os.walk(folder):
        for fn in sorted(files):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, folder).replace("\\", "/")
            lo = rel.lower()
            if lo.endswith((".txt", ".fdf")):
                ep = low.get(EN + lo)
                display = ep[len(EN):] if ep else rel
            elif lo.endswith((".w3x", ".w3m")) and lo.startswith("campaigns/"):
                ep = low.get("war3.w3mod:campaign/" + lo[len("campaigns/"):])
                display = ep.split(":", 1)[1] if ep else rel
            else:
                continue
            if not ep:
                missing.append(rel); continue
            try:
                with open(full, "rb") as h:
                    rows += pairs_from(c, low, lo, c.get(ep), h.read(), display)
            except Exception as ex:
                missing.append("%s (%s)" % (rel, ex))
    return rows, missing


def collect_casc(locale):
    from casc import Casc
    c = Casc(GAME)
    pre = "war3.w3mod:_locales/%s.w3mod:" % locale.lower()
    low = {p.lower(): p for p in c.root()}
    rows, missing = [], []
    for lo, p in sorted(low.items()):
        if not lo.startswith(pre):
            continue
        rel = lo[len(pre):]
        if not rel.endswith((".txt", ".fdf", ".w3x", ".w3m")):
            continue
        ep = low.get(EN + rel)
        if not ep:
            missing.append(rel); continue
        try:
            rows += pairs_from(c, low, rel, c.get(ep), c.get(p), ep[len(EN):])
        except Exception as ex:
            missing.append("%s (%s)" % (rel, ex))
    return rows, missing


def build(args):
    rows, missing = collect_folder(args.folder) if args.folder else collect_casc(args.casc)
    by = collections.OrderedDict()
    for e, t, ref in rows:
        h = text_hash(e)
        x = by.setdefault(h, {"en": e, "tr_refs": collections.OrderedDict()})
        x["tr_refs"].setdefault(t, []).append(ref)
    strings = {}
    for h, x in by.items():
        ranked = sorted(x["tr_refs"].items(), key=lambda kv: -len(kv[1]))
        tr, refs = ranked[0]
        e = {"en": x["en"], "tr": tr, "refs": refs}
        if len(ranked) > 1:
            e["variants"] = {t: r for t, r in ranked[1:]}
        strings[h] = e
    src = args.label or (args.folder and os.path.basename(os.path.normpath(args.folder))) or ("Reforged CASC " + args.casc)
    doc = {"lang": args.lang, "source": src, "english": "Reforged enUS (CASC, %s)" % GAME,
           "built": datetime.date.today().isoformat(), "count": len(strings), "strings": strings}
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "%s.json" % args.lang)
    with open(path, "w", encoding="utf-8") as h:
        json.dump(doc, h, ensure_ascii=False, indent=1)
    kinds = collections.Counter(("map" if "#" in r else r.split("/", 1)[0].lower()) for s in strings.values() for r in s["refs"])
    print("%s: %d English texts (%d with variants), refs by kind %s -> %s" % (
        args.lang, len(strings), sum("variants" in s for s in strings.values()), dict(kinds), path))
    if missing:
        print("no English counterpart for %d files: %s" % (len(missing), missing[:5]))


def search(args):
    doc = json.load(open(os.path.join(OUT, "%s.json" % args.lang), encoding="utf-8"))
    q = args.query
    rx = re.compile(q, re.I) if args.regex else None
    n = 0
    for h, e in doc["strings"].items():
        hay = [e["en"], e["tr"]] + list(e.get("variants", {}))
        ok = any(rx.search(x) for x in hay) if rx else any(q.lower() in x.lower() for x in hay)
        if ok:
            n += 1
            if n <= args.limit:
                print("%s  EN: %s\n            %s: %s\n            refs: %s%s" % (
                    h, e["en"][:200], args.lang.upper(), e["tr"][:200], ", ".join(e["refs"][:3]),
                    ("  (+%d variants)" % len(e["variants"])) if e.get("variants") else ""))
    print("%d matches" % n)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("lang")
    g = b.add_mutually_exclusive_group(required=True)
    g.add_argument("--folder", help="translated files laid out like the game (units/, ui/, Campaigns/)")
    g.add_argument("--casc", help="official locale in the installed game, e.g. itIT")
    b.add_argument("--label", help="source description stored in the file")
    s = sub.add_parser("search")
    s.add_argument("lang")
    s.add_argument("query")
    s.add_argument("--regex", action="store_true")
    s.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()
    build(a) if a.cmd == "build" else search(a)


if __name__ == "__main__":
    main()
