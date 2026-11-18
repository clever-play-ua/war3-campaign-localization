"""Pack a mission translation (STRING number -> text) into the repository format and check it.

    python tools/l10n_pack.py CSW-RoA IT WC2Human01 A01_M01
    python tools/l10n_pack.py CSW-RoA IT --global      (heroes/units/structures/artifacts: work/global_tr.json by hash)

Reads  <Camp>/<LANG>/work/<task>_tr.json   {"speakers": {en: tr}, "strings": {STRING number: tr}}
       <Camp>/site/index.json + index.html  (string texts and the quest/storyline structure of the site)
Writes <Camp>/<LANG>/strings/story/..._<task>_<LANG>.json, quests/..., other/...  ({id: {hash, en, tr}}, ids of the
       canonical English files matched by hash; speaker names go to other/ as #hash)
       <Camp>/<LANG>/review/<task>_<LANG>.json   storyline and quests in site order, English next to the translation
Checks colour codes, line breaks, <AAAA,DataA1> placeholders and %, quest integrity (every objective line of a quest
message is one of the quest's objectives, word for word, and the title line is the quest title) and the glossary.
"""
import argparse, collections, hashlib, html, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|n|<[A-Za-z0-9]{4},[^>]+>|%")


def text_hash(t):
    return hashlib.sha256(t.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:10]


def strip_codes(t):
    return re.sub(r"\|c[0-9a-fA-F]{8}|\|r", "", t).strip()


def site_structure(camp_dir, map_name):
    """(strings {sid: text}, storyline [(fragment, [(speaker, sid)])], quests [(title, kind, [(label, sid)])],
    speaker string ids {sid: name})."""
    data = json.load(open(os.path.join(camp_dir, "site", "index.json"), encoding="utf-8"))
    m = next(x for x in data["maps"] if x["map"] == map_name)
    S = {int(k): v["text"] for k, v in m["strings"].items()}
    speakers = {int(k): v["text"] for k, v in m["strings"].items() if v["detail"] == "speaker name"}
    page = open(os.path.join(camp_dir, "site", "index.html"), encoding="utf-8").read()
    slug = map_name.lower()
    view = page.split('data-slug="%s"' % slug)[1].split('<section class="view"')[0]
    strip = lambda x: html.unescape(re.sub(r"<[^>]+>", "", x)).strip()
    ids = lambda s: [int(x) for x in re.search(r"strings? ([\d+]+)", s).group(1).split("+")] if re.search(r"strings? \d", s) else []
    story = []
    frag = view.split('id="panel-%s-fragments"' % slug)[1].split('id="panel-%s-quests"' % slug)[0]
    for sc in re.finditer(r'<section class="scene[^"]*"><h3 class="scene-head">(.*?)</h3><div class="lines">(.*?)</div></section>', frag, re.S):
        lines = []
        for ln in re.finditer(r'<div class="name[^"]*">(.*?)</div>.*?<div class="ids">([^<]*)</div>', sc.group(2), re.S):
            name = strip(re.sub(r'<span class="vo".*?</span></span>', "", ln.group(1), flags=re.S))
            if ids(ln.group(2)):
                lines.append((name, ids(ln.group(2))[0]))
        story.append((strip(sc.group(1)), lines))
    quests = []
    qp = view.split('id="panel-%s-quests"' % slug)[1].split('id="panel-%s-factions"' % slug)[0]
    for art in re.findall(r'<article class="quest[^"]*">.*?</article>', qp, re.S):
        title = strip(re.search(r"<h3>(.*?)</h3>", art).group(1))
        kind = strip(re.search(r'<span class="badge[^"]*">(.*?)</span>', art).group(1))
        rows = []
        for st in re.finditer(r'<div class="step"><div class="step-label">(.*?)</div><div>(.*?)<div class="ids">([^<]*)</div>', art, re.S):
            for sid in ids(st.group(3))[:1]:
                rows.append((strip(st.group(1)), sid))
        quests.append((title, kind, rows))
    return S, story, quests, speakers


def check(S, tr, quests, glossary):
    problems = []
    for sid, t in tr.items():
        en = S.get(sid)
        if en is None:
            problems.append("%d: not a string of this map" % sid)
            continue
        if collections.Counter(CODE.findall(en)) != collections.Counter(CODE.findall(t)):
            problems.append("%d: codes differ  en=%s  tr=%s" % (sid, CODE.findall(en), CODE.findall(t)))
        if en.rstrip("\n ").count("\n") != t.rstrip("\n ").count("\n"):
            problems.append("%d: line breaks differ" % sid)
    # quest integrity
    for title, kind, rows in quests:
        if kind not in ("Main quest", "Optional quest"):
            continue
        objectives = {strip_codes(tr[s]) for lbl, s in rows if lbl in ("Requirement", "Requirement text") and s in tr}
        objectives = {re.sub(r"\s*\((Completato|Fallito)\)$", "", o) for o in objectives}
        objectives = {re.sub(r"\s*\(\d+ ?/ ?\d+[^)]*\)$", "", o) for o in objectives}
        qtitle = next((tr[s] for lbl, s in rows if lbl == "Title" and s in tr), None)
        for lbl, s in rows:
            if not lbl.startswith("Quest ") or s not in tr:
                continue
            lines = tr[s].split("\n")
            if qtitle and strip_codes(lines[1]) != qtitle:
                problems.append("%d (%s): title line %r is not the quest title %r" % (s, title, lines[1], qtitle))
            for ln in lines[2:]:
                o = re.sub(r"\s*\((Completato|Fallito)\)$", "", strip_codes(ln).lstrip("- ").strip())
                o = re.sub(r"\s*\(\d+ ?/ ?\d+[^)]*\)$", "", o)
                if o and o not in objectives:
                    problems.append("%d (%s): line %r is not one of the objectives" % (s, title, o))
    # glossary: English term present -> Italian form present
    for sid, t in tr.items():
        en = S.get(sid, "")
        for e_term, i_form in glossary:
            if re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(e_term), en) and i_form.lower() not in t.lower():
                problems.append("%d: '%s' should be '%s'" % (sid, e_term, i_form))
    return problems


GLOSSARY_CHECK = [("Lightbringer", "Araldo della Luce"), ("Bronzebeard", "Barbabronzea"), ("Wildhammer", "Granmartello"),
                  ("Doomhammer", "Martelfato"), ("Proudmoore", "Marefiero"), ("Hillsbrad", "Colletorto"),
                  ("Fenris Isle", "Isola di Fenris"), ("Lordamere", "Lago Lordamere"), ("Warchief", "Capoguerra"),
                  ("Horde", "Orda"), ("Alliance", "Alleanza"), ("Silver Hand", "Mano d'Argento"), ("Kul Tiras", "Kul Tiras"),
                  ("Dalaran", "Dalaran"), ("Lordaeron", "Lordaeron"), ("Alterac", "Alterac"), ("MAIN QUEST", "MISSIONE PRINCIPALE"),
                  ("OPTIONAL QUEST", "MISSIONE FACOLTATIVA"), ("HINT", "SUGGERIMENTO"), ("REINFORCEMENT", "RINFORZI"),
                  ("Footmen", "Fanti"), ("Royal Guard", "Guardia Reale"), ("Barracks", "caserma"), ("Shipyard", "Cantiere Navale"),
                  ("gryphon", "grifon"), ("Light", "Luce")]

GLOSSARIES = {"IT": GLOSSARY_CHECK}   # glossary forms are checked only for languages listed here


def pack_global(campaign, lang):
    """<Camp>/<LANG>/work/global_tr.json {"strings": {English hash: tr}} -> <LANG>/strings/global/** (every canonical id,
    empty tr where untranslated, like the other languages) and, for hashes that live in map files, the matching
    <LANG>/strings/<group>/ file (merged into what is there)."""
    camp_dir = os.path.join(ROOT, campaign)
    work = json.load(open(os.path.join(camp_dir, lang, "work", "global_tr.json"), encoding="utf-8"))["strings"]
    canon_dir = os.path.join(camp_dir, "strings")
    placed, counts = set(), collections.Counter()
    en_by_hash = {}
    for dirpath, _, files in os.walk(canon_dir):
        for fn in sorted(files):
            if not fn.endswith("_enUS.json"):
                continue
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, canon_dir)
            grp = rel.replace("\\", "/").split("/")[0]
            canon = json.load(open(src, encoding="utf-8"))
            for e in canon.values():
                en_by_hash.setdefault(e["hash"], e["en"])
            dst = os.path.join(camp_dir, lang, "strings", rel[:-len("_enUS.json")] + "_%s.json" % lang)
            if grp == "global":
                old = json.load(open(dst, encoding="utf-8")) if os.path.exists(dst) else {}
                out = {}
                for k, e in canon.items():          # a translation already in the file wins over the work file
                    have = old.get(k) or {}
                    out[k] = dict(have) if have.get("tr") else {"hash": e["hash"], "en": e["en"], "tr": work.get(e["hash"], "")}
            else:
                hits = {k: e for k, e in canon.items() if e["hash"] in work}
                if not hits:
                    continue
                out = json.load(open(dst, encoding="utf-8")) if os.path.exists(dst) else {}
                for k, e in hits.items():
                    if not (out.get(k) or {}).get("tr"):
                        out[k] = {"hash": e["hash"], "en": e["en"], "tr": work[e["hash"]]}
            got = {e["hash"] for e in out.values() if e.get("tr") and e["hash"] in work}
            placed |= got
            counts[grp] += len(got)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    missing = sorted(set(work) - placed)
    keys = sorted(work)
    S = {i: en_by_hash.get(h, "") for i, h in enumerate(keys)}
    tr = {i: work[h] for i, h in enumerate(keys) if h in en_by_hash}
    problems = ["%s: %s" % (keys[int(p.split(":")[0])], p.split(":", 1)[1]) for p in check(S, tr, [], GLOSSARIES.get(lang, []))]
    print("global texts %d | placed by hash: %s | not in any canonical file: %d %s" % (
        len(work), dict(counts), len(missing), missing[:8]))
    print("problems: %d" % len(problems))
    for p in problems:
        print("  ", p)
    return 1 if problems else 0


def main():
    if len(sys.argv) >= 4 and sys.argv[3] == "--global":
        return pack_global(sys.argv[1], sys.argv[2])
    ap = argparse.ArgumentParser()
    ap.add_argument("campaign")
    ap.add_argument("lang")
    ap.add_argument("map")
    ap.add_argument("task")
    a = ap.parse_args()
    camp_dir = os.path.join(ROOT, a.campaign)
    cfg = json.load(open(os.path.join(camp_dir, "campaign.json"), encoding="utf-8"))
    pre = "%s_%s" % (cfg["short"], cfg["version"].replace(".", "_"))
    work = json.load(open(os.path.join(camp_dir, a.lang, "work", "%s_tr.json" % a.task), encoding="utf-8"))
    tr = {int(k): v for k, v in work["strings"].items()}
    S, story, quests, speakers = site_structure(camp_dir, a.map)
    for sid, name in speakers.items():                   # speaker names by English text
        if name in work["speakers"]:
            tr.setdefault(sid, work["speakers"][name])

    wanted = {sid for _, lines in story for _, sid in lines} | {s for _, k, rows in quests for _, s in rows}
    missing = sorted(wanted - set(tr))
    problems = check(S, tr, quests, GLOSSARIES.get(a.lang, []))
    print("strings translated %d of %d wanted (+%d speaker names) | missing %s" % (
        len(wanted & set(tr)), len(wanted), len(set(speakers) & set(tr)), missing[:10]))

    # repository files: canonical ids by English hash
    by_hash = collections.defaultdict(list)
    for sid, t in tr.items():
        by_hash[text_hash(S[sid])].append(t)
    for h, ts in by_hash.items():
        if len(set(ts)) > 1:
            problems.append("hash %s: one English text, %d different translations %s" % (h, len(set(ts)), sorted(set(ts))[:2]))
    out_counts, used = {}, set()
    for kind in ("story", "quests"):
        canon_f = os.path.join(camp_dir, "strings", kind, "%s_%s_enUS.json" % (pre, a.task))
        canon = json.load(open(canon_f, encoding="utf-8"))
        out = {}
        for k, e in canon.items():
            if e["hash"] in by_hash:
                out[k] = {"hash": e["hash"], "en": e["en"], "tr": by_hash[e["hash"]][0]}
                used.add(e["hash"])
        path = os.path.join(camp_dir, a.lang, "strings", kind, "%s_%s_%s.json" % (pre, a.task, a.lang))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        out_counts[kind] = len(out)
    rest = {"#" + h: {"hash": h, "en": S[next(s for s in tr if text_hash(S[s]) == h)], "tr": ts[0]}
            for h, ts in by_hash.items() if h not in used}
    path = os.path.join(camp_dir, a.lang, "strings", "other", "%s_%s_%s.json" % (pre, a.task, a.lang))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(rest, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    out_counts["other"] = len(rest)

    review = {"map": a.map, "task": a.task, "lang": a.lang,
              "storyline": [{"fragment": f, "lines": [{"string": s, "speaker": n, "speaker_tr": work["speakers"].get(n, n),
                                                       "en": S[s], "tr": tr.get(s)} for n, s in lines]} for f, lines in story],
              "quests": [{"quest": t, "kind": k, "rows": [{"label": l, "string": s, "en": S[s], "tr": tr.get(s)} for l, s in rows]}
                         for t, k, rows in quests]}
    path = os.path.join(camp_dir, a.lang, "review", "%s_%s.json" % (a.task, a.lang))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(review, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written:", out_counts, "+ review")
    print("problems: %d" % len(problems))
    for p in problems:
        print("  ", p)
    return 1 if problems or missing else 0


if __name__ == "__main__":
    sys.exit(main())
