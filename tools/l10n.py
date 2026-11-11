#!/usr/bin/env python3
"""
Campaign localization engine: string text + its hash is the unit of work.

    l10n.py extract <campaign>                    clean English .wts out of the source .w3n
    l10n.py index   <campaign>                    source .wts -> canonical strings/ (ids kept)
    l10n.py import-wts <campaign> <LANG> <dir>    translated .wts files (same numbering) -> <LANG>/strings
    l10n.py import-db  <campaign> <LANG> <en> <tr> legacy id->text databases -> <LANG>/strings
    l10n.py build   <campaign> <LANG> [--wts-only] translations -> <LANG>/wts + localized .w3n
    l10n.py check   <campaign> [LANG]             coverage, stale hashes, broken tags

<campaign> is a folder name such as CSW-RoA. See FORMAT.md for the layout.
"""

import argparse
import collections
import difflib
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from w3mpq import MpqArchive, open_nested, rebuild_archive  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BLOCK_RE = re.compile(r"(STRING (\d+)[ \t]*\n(?://([^\n]*)\n)?)\{\n([\s\S]*?)\n\}")
PLACEHOLDER_RE = re.compile(r"<([A-Za-z0-9]{4}),([A-Za-z0-9]+)(?:,%)?>")
FUZZY = 0.90


# ---------------------------------------------------------------- helpers
def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def norm_nl(s):
    return s.replace("\r\n", "\n").replace("\r", "\n")


def read_wts(path_or_bytes):
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    return norm_nl(data.decode("utf-8"))


def blocks(text):
    """[(number, comment, body)] in file order."""
    return [(m.group(2), m.group(3) or "", m.group(4)) for m in BLOCK_RE.finditer(text)]


def loose(t):
    return re.sub(r"\s+", " ", t).strip()


def shape(t):
    t = t.replace("|n", "\n")
    return (tuple(sorted(PLACEHOLDER_RE.findall(t))), len(re.findall(r"\|c[0-9a-fA-F]{8}", t)), t.count("|r"), t.count("\n"))


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_json(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Campaign:
    def __init__(self, name):
        self.dir = ROOT / name
        self.cfg = load_json(self.dir / "campaign.json")
        self.tasks = {t["id"]: t for t in self.cfg["tasks"]}
        self.prefix = self.cfg.get("legacy_prefix", "")

    @property
    def ver(self):
        return self.cfg["version"].replace(".", "_")

    def source_dir(self):
        return self.dir / "source" / self.cfg["version"]

    def source_wts(self, task):
        return self.source_dir() / f"{self.cfg['short']}_{self.ver}_{task}_enUS.wts"

    def phys(self, rel, code):
        """strings/<group>/<name>.json (logical) -> <Camp>_<ver>_<name>_<code>.json (on disk)."""
        r = Path(rel)
        return (r.parent / f"{self.cfg['short']}_{self.ver}_{r.stem}_{code}.json").as_posix()

    def logical(self, rel, code):
        r = Path(rel)
        stem = r.stem[len(f"{self.cfg['short']}_{self.ver}_"):-len(f"_{code}")]
        return (r.parent / f"{stem}.json").as_posix()

    def wts_dir(self, lang):
        return self.dir / lang / "wts" / self.cfg["version"]

    def wts_file(self, lang, task):
        cfg = load_json(self.dir / lang / "lang.json")
        tver = cfg["version"].replace(".", "_")
        return self.wts_dir(lang) / f"{self.cfg['short']}_{self.ver}_{task}_{cfg['locale']}_{tver}.wts"

    def lang_dir(self, lang):
        return self.dir / lang


def group_and_file(camp, task, key):
    """Where a legacy id lives: (group, relative file)."""
    rest = key[len(camp.prefix + task) + 1:] if key.startswith(camp.prefix + task + "_") else key
    parts = rest.split("_")
    cat = parts[0]
    if task == camp.cfg["campaign_task"]:
        if cat == "MENU":
            return "global", "menu.json"
        if cat == "HEROES" and len(parts) > 2:
            return "global", f"heroes/{parts[1]}.json"
        return "global", f"{cat.lower()}.json"
    return ("story" if cat == "VO" else "quests"), f"{task}.json"


# ---------------------------------------------------------------- extract
def cmd_extract(camp, args):
    src = Path(args.w3n or camp.cfg["source_w3n"])
    out = Path(args.to) if args.to else camp.source_dir()
    out.mkdir(parents=True, exist_ok=True)
    a = MpqArchive(src)
    for task in camp.cfg["tasks"]:
        data = a.read(camp.cfg["campaign_wts"]) if task["id"] == camp.cfg["campaign_task"] \
            else open_nested(a, task["map"]).read("war3map.wts")
        name = f"{task['id']}.wts" if args.to else camp.source_wts(task["id"]).name
        (out / name).write_bytes(data)
        print(f"  {task['id']:<12} {len(blocks(read_wts(data))):>5} blocks")
    a.close()


# ---------------------------------------------------------------- index
def legacy_entries(camp, task, args):
    """Existing id->metadata for a task: from the canonical files already there,
    or from a legacy EN database (RoA and ToD shapes)."""
    found = {}
    for grp_dir in ("story", "quests", "global"):
        for f in (camp.dir / "strings" / grp_dir).rglob("*.json") if (camp.dir / "strings" / grp_dir).exists() else []:
            for k, e in load_json(f).items():
                if k.startswith(camp.prefix + task + "_") or (task == camp.cfg["campaign_task"] and grp_dir == "global"):
                    found[k] = {"text": e["en"], "type": e.get("type"), "speaker": e.get("speaker")}
    if found:
        return found
    if args.legacy:
        legacy_file = Path(args.legacy) / camp.cfg["legacy_file"].format(task=task)
        if legacy_file.exists():
            data = load_json(legacy_file)
            data = data.get("strings", data)
            for k, e in data.items():
                text = e.get("source") or e.get("source_new")
                if text is not None:
                    found[k] = {"text": text, "type": e.get("type"), "speaker": e.get("unit_name")}
    return found


def cmd_index(camp, args):
    strings = camp.dir / "strings"
    old = {t: legacy_entries(camp, t, args) for t in camp.tasks}
    if strings.exists():
        shutil.rmtree(strings)
    stats = collections.Counter()
    for task_id in camp.tasks:
        bl = blocks(read_wts(camp.source_wts(task_id)))
        by_hash = collections.defaultdict(list)
        for num, comment, body in bl:
            by_hash[text_hash(body)].append((num, comment, body))
        by_loose = {}
        for _, _, b in bl:
            by_loose.setdefault(loose(b), b)
        entries, used = {}, set()
        for key, meta in old[task_id].items():
            h = text_hash(meta["text"])
            body = None
            if h in by_hash:
                body = by_hash[h][0][2]
            elif loose(meta["text"]) in by_loose:
                body = by_loose[loose(meta["text"])]
            else:
                best, score = None, FUZZY
                ph = PLACEHOLDER_RE.findall(meta["text"])
                for hh, items in by_hash.items():
                    cand = items[0][2]
                    if abs(len(cand) - len(meta["text"])) > 0.2 * max(len(cand), 1) + 3:
                        continue
                    r = difflib.SequenceMatcher(None, meta["text"], cand).ratio()
                    if r > score and sorted(PLACEHOLDER_RE.findall(cand)) == sorted(ph):
                        best, score = cand, r
                body = best
            if body is None:
                stats["dropped"] += 1
                continue
            e = {"hash": text_hash(body), "en": body}
            if meta.get("type"):
                e["type"] = meta["type"]
            if meta.get("speaker") and meta["speaker"] not in ("N/A", ""):
                e["speaker"] = meta["speaker"]
            entries[key] = e
            used.add(e["hash"])
        files = collections.defaultdict(dict)
        for key in sorted(entries):
            grp, rel = group_and_file(camp, task_id, key)
            files[(grp, rel)][key] = entries[key]
        for (grp, rel), data in files.items():
            save_json(strings / camp.phys(f"{grp}/{rel}", "enUS"), data)
            stats[grp] += len(data)
        other = {}
        for h, items in by_hash.items():
            if h in used or not items[0][2].strip():
                continue
            other["#" + h] = {"hash": h, "en": items[0][2], "refs": [n for n, _, _ in items],
                              **({"note": items[0][1].strip()} if items[0][1].strip() else {})}
        if other:
            save_json(strings / camp.phys(f"other/{task_id}.json", "enUS"), other)
            stats["other"] += len(other)
        print(f"  {task_id:<12} ids {len(entries):>5} | other {len(other):>5} | blocks {len(bl)}")
    print(dict(stats))


# ---------------------------------------------------------------- canonical access
def load_canon(camp):
    """{relative file: {id: entry}} and {(file, id): (task, hash)}."""
    files, index = {}, {}
    base = camp.dir / "strings"
    for f in sorted(base.rglob("*.json")):
        rel = camp.logical(f.relative_to(base).as_posix(), "enUS")
        data = load_json(f)
        files[rel] = data
        grp = rel.split("/")[0]
        for k, e in data.items():
            if grp == "other":
                task = Path(rel).stem
            elif grp == "global":
                task = camp.cfg["campaign_task"]
            else:
                task = Path(rel).stem
            index[(rel, k)] = (task, e["hash"])
    return files, index


def lang_files(camp, lang):
    base = camp.lang_dir(lang) / "strings"
    return {camp.logical(f.relative_to(base).as_posix(), lang): load_json(f) for f in sorted(base.rglob("*.json"))} if base.exists() else {}


def write_lang(camp, lang, canon, tr_by_id, stale_hash):
    """tr_by_id: id -> (translation, hash of the English it was made for)."""
    base = camp.lang_dir(lang) / "strings"
    if base.exists():
        shutil.rmtree(base)
    n = 0
    for rel, entries in canon.items():
        grp = rel.split("/")[0]
        out = {}
        for k, e in entries.items():
            got = tr_by_id.get((rel, k))
            if got:
                out[k] = {"hash": got[1], "en": e["en"], "tr": got[0]}
                if len(got) > 2 and got[2]:
                    out[k]["variants"] = got[2]
                n += 1
            elif grp != "other":
                out[k] = {"hash": e["hash"], "en": e["en"], "tr": ""}
        if out:
            save_json(base / camp.phys(rel, lang), out)
    return n


# ---------------------------------------------------------------- import
def ref_pairs(camp, ref_dir, tr_dir, credit=None):
    """{task: [(number, english, translation)]} from two folders of <task>.wts that share one numbering."""
    out = {}
    for task_id in camp.tasks:
        rf, tf = Path(ref_dir) / f"{task_id}.wts", Path(tr_dir) / f"{task_id}.wts"
        if not rf.exists() or not tf.exists():
            continue
        tr = {n: b for n, _, b in blocks(read_wts(tf))}
        rows = []
        for n, _, b in blocks(read_wts(rf)):
            t = tr.get(n)
            if t is None or not t.strip() or not b.strip():
                continue
            if task_id == camp.cfg["campaign_task"] and n == camp.cfg["title_block"]:
                continue
            if task_id == camp.cfg["campaign_task"] and n == camp.cfg["description_block"] and credit:
                t = t.replace("\n\n" + credit, "").replace(credit, "").rstrip()
            rows.append((n, b, t))
        out[task_id] = rows
    return out


def cmd_pairs(camp, args):
    """Store exact English/translation pairs of an older release as <LANG>/pairs/<version>/."""
    out = camp.lang_dir(args.lang) / "pairs" / args.version
    ver = args.version.replace(".", "_")
    total = 0
    for task_id, rows in ref_pairs(camp, args.ref_en, args.tr).items():
        data = [{"n": int(n), "hash": text_hash(b), "en": b, "tr": t} for n, b, t in rows]
        save_json(out / f"{camp.cfg['short']}_{ver}_{task_id}_{args.lang}.json", data)
        total += len(data)
    print(f"{args.lang}: {total} pairs for release {args.version}")


def cmd_import_wts(camp, args):
    canon, _ = load_canon(camp)
    if args.ref_en:
        cfg = load_json(camp.lang_dir(args.lang) / "lang.json")
        memory = {}
        for rows in ref_pairs(camp, args.ref_en, args.dir, cfg.get("credit")).values():
            for n, b, t in rows:
                if t != b and sorted(PLACEHOLDER_RE.findall(t)) == sorted(PLACEHOLDER_RE.findall(b)):
                    memory.setdefault(text_hash(b), (b, t))
        apply_memory(camp, args.lang, canon, memory)
        return
    lang = args.lang
    cfg = load_json(camp.lang_dir(lang) / "lang.json") if (camp.lang_dir(lang) / "lang.json").exists() else {}
    credit = cfg.get("credit")
    tm = {}
    for task_id in camp.tasks:
        src = blocks(read_wts(camp.source_wts(task_id)))
        tp = next(Path(args.dir).glob(f"*{task_id}*.wts"), None) if not (Path(args.dir) / f"{task_id}.wts").exists() \
            else Path(args.dir) / f"{task_id}.wts"
        if tp is None:
            print(f"  {task_id}: no translated wts found")
            continue
        tr = {n: b for n, _, b in blocks(read_wts(tp))}
        votes = collections.defaultdict(collections.Counter)
        by_ctx = collections.defaultdict(dict)
        for num, comment, body in src:
            t = tr.get(num)
            if t is None or t == body or not t.strip():
                continue
            if args.check_placeholders and sorted(PLACEHOLDER_RE.findall(t)) != sorted(PLACEHOLDER_RE.findall(body)):
                continue
            if task_id == camp.cfg["campaign_task"]:
                if num == camp.cfg["title_block"]:
                    continue
                if num == camp.cfg["description_block"] and credit:
                    t = t.replace("\n\n" + credit, "").replace(credit, "").rstrip()
            votes[text_hash(body)][t] += 1
            by_ctx[text_hash(body)][comment.strip() or "#" + num] = t
        for h, c in votes.items():
            main = c.most_common(1)[0][0]
            variants = {cm: t for cm, t in by_ctx[h].items() if t != main} if len(c) > 1 else {}
            tm.setdefault(h, {}).setdefault(task_id, (main, variants))
        print(f"  {task_id:<12} {len(votes):>5} translated texts")
    tr_by_id = {}
    for rel, entries in canon.items():
        for k, e in entries.items():
            per_task = tm.get(e["hash"])
            if per_task:
                task = Path(rel).stem if not rel.startswith("global") else camp.cfg["campaign_task"]
                t, variants = per_task.get(task) or next(iter(per_task.values()))
                tr_by_id[(rel, k)] = (t, e["hash"], variants)
    n = write_lang(camp, lang, canon, tr_by_id, None)
    print(f"{lang}: {n} translated entries")


def apply_memory(camp, lang, canon, memory):
    """memory {hash: (english, translation)} -> <LANG>/strings: exact hash first, then >= 90 % near match
    with identical placeholders (marked by the old hash, so they show up as stale)."""
    tr_by_id, fuzzy = {}, 0
    mem_items = list(memory.values())
    for rel, entries in canon.items():
        for k, e in entries.items():
            if e["hash"] in memory:
                tr_by_id[(rel, k)] = (memory[e["hash"]][1], e["hash"])
                continue
            if len(e["en"]) < 12:
                continue
            best, score = None, FUZZY
            sm = difflib.SequenceMatcher(None, autojunk=False)
            sm.set_seq2(e["en"])
            for src, t in mem_items:
                if abs(len(src) - len(e["en"])) > 0.2 * len(e["en"]) + 3:
                    continue
                sm.set_seq1(src)
                if sm.real_quick_ratio() <= score or sm.quick_ratio() <= score:
                    continue
                r = sm.ratio()
                if r > score and shape(src) == shape(e["en"]):
                    best, score = (t, text_hash(src)), r
            if best:
                tr_by_id[(rel, k)] = best
                fuzzy += 1
    n = write_lang(camp, lang, canon, tr_by_id, None)
    print(f"{lang}: {n} translated entries ({fuzzy} fuzzy, marked by an old hash)")


def cmd_import_db(camp, args):
    """Legacy flat databases: id -> translation, with a legacy EN database giving each id's English."""
    canon, _ = load_canon(camp)
    en_dir, tr_dir, lang = Path(args.en), Path(args.tr), args.lang
    code = args.code or lang.lower()
    memory = {}
    for task_id in camp.tasks:
        en_f = en_dir / camp.cfg["legacy_file"].format(task=task_id)
        tr_f = next(tr_dir.glob(camp.cfg["legacy_lang_glob"].format(task=task_id, lang=code)), None)
        if not en_f.exists() or tr_f is None:
            continue
        en = load_json(en_f)
        en = en.get("strings", en)
        tr = load_json(tr_f)
        tr = tr.get("strings", tr)
        for k, e in en.items():
            src = e.get("source") or e.get("source_new")
            t = tr.get(k)
            if isinstance(t, dict):
                t = t.get("translation")
            if src is not None and t:
                memory.setdefault(text_hash(src), (src, t))
    apply_memory(camp, lang, canon, memory)


# ---------------------------------------------------------------- build
def translation_memory(camp, lang):
    """{task: {hash: tr}}, {hash: tr}: fresh translations first, then stale ones."""
    canon, index = load_canon(camp)
    per_task, anywhere, variants = collections.defaultdict(dict), {}, {}
    stale = []
    for rel, entries in lang_files(camp, lang).items():
        for k, e in entries.items():
            if not e.get("tr") or (rel, k) not in index:
                continue
            task, h = index[(rel, k)]
            (stale if e["hash"] != h else []).append(1)
            per_task[task].setdefault(h, e["tr"])
            anywhere.setdefault(h, e["tr"])
            if e.get("variants"):
                variants.setdefault((task, h), e["variants"])
    return per_task, anywhere, variants


def build_wts(camp, lang):
    per_task, anywhere, variants = translation_memory(camp, lang)
    cfg = load_json(camp.lang_dir(lang) / "lang.json")
    camp.wts_dir(lang).mkdir(parents=True, exist_ok=True)
    for task_id in camp.tasks:
        text = read_wts(camp.source_wts(task_id))
        tm = per_task.get(task_id, {})
        done = 0

        def rep(m):
            nonlocal done
            num, body = m.group(2), m.group(4)
            if task_id == camp.cfg["campaign_task"] and num == camp.cfg["title_block"] and cfg.get("title"):
                return f"{m.group(1)}{{\n{cfg['title']}\n}}"
            tr = tm.get(text_hash(body)) or anywhere.get(text_hash(body))
            alt = variants.get((task_id, text_hash(body)), {}).get((m.group(3) or "").strip() or "#" + num)
            if alt:
                tr = alt
            if task_id == camp.cfg["campaign_task"] and num == camp.cfg["description_block"] and cfg.get("credit"):
                tr = (tr or body) + "\n\n" + cfg["credit"]
            if tr:
                done += 1
                return f"{m.group(1)}{{\n{tr}\n}}"
            return m.group(0)

        res = BLOCK_RE.sub(rep, text)
        camp.wts_file(lang, task_id).write_bytes(res.replace("\n", "\r\n").encode("utf-8"))
        print(f"  {task_id:<12} {done:>5}/{len(blocks(text))} blocks")


def build_campaign(camp, lang, out_dir):
    cfg = load_json(camp.lang_dir(lang) / "lang.json")
    lang_dir = camp.lang_dir(lang)
    src = Path(camp.cfg["source_w3n"])
    out = Path(out_dir or cfg.get("output_dir") or camp.cfg.get("output_dir")) / cfg["output"]
    replacements = {camp.cfg["campaign_wts"]: camp.wts_file(lang, camp.cfg["campaign_task"]).read_bytes()}
    for internal, rel in cfg.get("campaign_files", {}).items():
        replacements[internal] = (lang_dir / rel).read_bytes()
    work = Path(tempfile.mkdtemp(prefix="l10n_"))
    try:
        a = MpqArchive(src)
        for task in camp.cfg["tasks"]:
            if not task.get("map"):
                continue
            tmp_in, tmp_out = work / task["map"], work / ("loc_" + task["map"])
            tmp_in.write_bytes(a.read(task["map"]))
            files = {"war3map.wts": camp.wts_file(lang, task["id"]).read_bytes()}
            for internal, rel in cfg.get("map_files", {}).get(task["map"], {}).items():
                files[internal] = (lang_dir / rel).read_bytes()
            rebuild_archive(tmp_in, tmp_out, files)
            replacements[task["map"]] = tmp_out.read_bytes()
        a.close()
        out.parent.mkdir(parents=True, exist_ok=True)
        rebuild_archive(src, out, replacements)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"saved {out} ({out.stat().st_size:,} bytes)")


def cmd_build(camp, args):
    build_wts(camp, args.lang)
    if not args.wts_only:
        build_campaign(camp, args.lang, args.out_dir)


# ---------------------------------------------------------------- check
def cmd_check(camp, args):
    canon, index = load_canon(camp)
    langs = [args.lang] if args.lang else sorted(p.name for p in camp.dir.iterdir() if (p / "lang.json").exists())
    for lang in langs:
        files = lang_files(camp, lang)
        tot, done, stale, broken = collections.Counter(), collections.Counter(), 0, []
        for rel, entries in canon.items():
            grp = rel.split("/")[0]
            for k, e in entries.items():
                tot[grp] += 1
                t = files.get(rel, {}).get(k)
                if t and t.get("tr"):
                    done[grp] += 1
                    stale += t["hash"] != e["hash"]
                    tr = t["tr"]
                    bad = sorted(PLACEHOLDER_RE.findall(tr)) != sorted(PLACEHOLDER_RE.findall(e["en"])) or                         len(re.findall(r"\|c[0-9a-fA-F]{8}", tr)) - tr.count("|r") >                         max(0, len(re.findall(r"\|c[0-9a-fA-F]{8}", e["en"])) - e["en"].count("|r"))
                    if bad:
                        broken.append(k)
        row = " | ".join(f"{g} {done[g]}/{tot[g]}" for g in ("story", "quests", "global", "other"))
        print(f"{lang}: {row} | stale {stale} | broken tags {len(broken)} {broken[:4]}")


COMMANDS = {"pairs": cmd_pairs, "extract": cmd_extract, "index": cmd_index, "import-wts": cmd_import_wts,
            "import-db": cmd_import_db, "build": cmd_build, "check": cmd_check}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        p.add_argument("campaign")
        if name == "extract":
            p.add_argument("--w3n", help="another campaign file (e.g. a translated one) instead of the English source")
            p.add_argument("--to", help="write the wts files here instead of source/enUS")
        if name == "index":
            p.add_argument("--legacy", help="folder with legacy EN databases, used only for the first run")
        if name == "import-wts":
            p.add_argument("lang")
            p.add_argument("dir")
            p.add_argument("--ref-en", help="folder of <task>.wts with the English the translation was made from (older release)")
            p.add_argument("--check-placeholders", action="store_true",
                           help="skip pairs whose <AAAA,DataA1> placeholders differ (translation from another game version)")
        if name == "pairs":
            p.add_argument("lang")
            p.add_argument("ref_en")
            p.add_argument("tr")
            p.add_argument("--version", required=True)
        if name == "import-db":
            p.add_argument("lang")
            p.add_argument("en")
            p.add_argument("tr")
            p.add_argument("--code", help="language code used in the legacy file names (default: lower-case LANG)")
        if name == "build":
            p.add_argument("lang")
            p.add_argument("--wts-only", action="store_true")
            p.add_argument("--out-dir")
        if name == "check":
            p.add_argument("lang", nargs="?")
    args = ap.parse_args()
    COMMANDS[args.cmd](Campaign(args.campaign), args)


if __name__ == "__main__":
    main()
