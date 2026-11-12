"""Forsaken Kingdom: decrypted maps -> FK/<locale>/<campaign>/<mission>/ (war3map.wts, dialogue.json; conversation.json
in enUS only, it is the same for every language) + FK/<locale>/GLOBAL/GlobalStrings_FK.fdf.txt (menu names from CASC).

    python tools/fk_extract.py <decrypted_dir> [--out FK] [--locales enUS deDE ...]

<decrypted_dir> holds the `<Map>.files/` folders written by w3xd-toolkit (`python -m w3xd batch DIR -x`).
Only English text goes into the repository; decrypted maps stay outside it (they are bound to the account license).

dialogue.json lists every conversation line with its text. Order: recording ids `<map>-<C|M|T><num>` sorted by series
(C cinematics, M in-engine cutscenes, T gameplay transmissions) and number; lines without such an id (unit barks) follow
the earliest line of their trigger group. The interleaving of the C/M/T series is not proven by the script.
"""
import argparse, json, os, re, shutil

CAMPAIGNS = [
    ("000_The_Last_Days_of_Lordaeron", [
        ("HumanRE01", "Homecoming"),
        ("HumanRE02", "Treachery"),
        ("HumanRE02Interlude", "Lightbringer"),
        ("HumanRE03", "Siege"),
        ("HumanRE04", "Vengeance"),
    ]),
    ("001_Forsaken_Kingdom", [
        ("UndeadRE01", "City_of_the_Dead"),
        ("UndeadRE01_02", "Capital_Ruins"),
        ("UndeadRE01_03", "Tirisfal_Glades"),
        ("UndeadRE01_04", "Silverpine_Sprint"),
        ("UndeadRE01_05", "Arcane_Sanctuary"),
        ("UndeadRE01_06", "Cathedral"),
        ("UndeadRE02", "A_Scarlet_Flame"),
        ("UndeadRE02_01", "Forgotten_Hollow"),
        ("UndeadRE02_02", "Tirisfal_Glades_Villages"),
        ("UndeadRE02_03", "Blackrock_Encampment"),
        ("UndeadRE02_04", "Dawns_Watch"),
        ("UndeadRE02_06", "Scarlet_Monastery"),
        ("UndeadRE03Interlude", "A_Looming_Shadow"),
        ("UndeadRE03a", "War_of_the_Damned"),
        ("UndeadRE03b", "Naxxramas"),
    ]),
]

WTS = re.compile(r"STRING (\d+)\r?\n(?://[^\r\n]*\r?\n)?\{\r?\n(.*?)\r?\n\}", re.S)
FACE = re.compile(r'\[/?"[^"]*"[^\]]*\]')          # facial-animation tags: ["Angry" type=quad ...]...[/"Angry"]
REC = re.compile(r"^[A-Za-z0-9]+-([CMT])(\d+)")


def read_wts(path):
    t = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    return {int(a): b for a, b in WTS.findall(t)}


def wts_path(files, loc):
    if loc == "enUS":
        return os.path.join(files, "war3map.wts")
    return os.path.join(files, "_Locales", loc + ".w3mod", "war3map.wts")


def dialogue(mapname, title, files, loc="enUS"):
    S = read_wts(wts_path(files, loc))
    conv = json.load(open(os.path.join(files, "conversation.json"), encoding="utf-8"))["conversation"]
    rows, seq = [], 0
    for group, entries in conv.items():
        for e in entries:
            label = e.get("animationLabel") or os.path.splitext(os.path.basename(e.get("soundFile") or ""))[0]
            m = REC.match(label)
            key = ({"C": 0, "M": 1, "T": 2}[m.group(1)], int(m.group(2))) if m else None
            raw = S.get(e.get("dialogueId"), "")
            row = {"id": label,
                   "scene": group,
                   "speaker": S.get(e.get("speakerNameId")),
                   "text": FACE.sub("", raw).strip()}
            if FACE.search(raw):
                row["raw"] = raw
            row.update({"string_id": e.get("dialogueId"), "speaker_string_id": e.get("speakerNameId"),
                        "sound": e.get("soundFile")})
            rows.append((key, group, seq, row))
            seq += 1
    first = {}
    for key, group, s, _ in rows:
        if key is not None and (group not in first or key < first[group]):
            first[group] = key
    def sort_key(r):
        key, group, s, _ = r
        if key is not None:
            return (key, 0, s)
        return (first.get(group, (9, 0)), 1, s)
    rows.sort(key=sort_key)
    lines = []
    for n, (_, _, _, row) in enumerate(rows, 1):
        lines.append({"n": n, **row})
    return {"map": mapname, "title": title.replace("_", " "), "locale": loc,
            "order_note": "Sorted by recording id: C (cinematics), M (in-engine cutscenes), T (gameplay transmissions); "
                          "unit barks follow their trigger group. Interleaving of C/M/T is not confirmed.",
            "lines": lines}


LOCALES = ["enUS", "deDE", "esES", "esMX", "frFR", "itIT", "koKR", "plPL", "ptBR", "ruRU", "zhCN", "zhTW"]
GAME = "F:/Games/Warcraft III"
_CASC = None
MENU = re.compile(r"\b(CAMP_\w*(HUMANRE|UNDEADRE)\w*|CAMP_CIN_(INTRO|OUTRO)RE)\b")


def menu_strings(loc):
    """CAMP_* lines of Forsaken Kingdom from the locale's GlobalStrings.fdf in CASC (None if that locale is not installed)."""
    global _CASC
    from casc import Casc
    path = "War3.w3mod:_Locales/%s.w3mod:UI/FrameDef/GlobalStrings.fdf" % loc
    if _CASC is None:
        _CASC = Casc(GAME)
    c = _CASC
    if path not in c.root():
        return None
    try:
        t = c.get(path).decode("utf-8-sig", "replace")
    except KeyError:
        return None
    keep = [ln.strip() for ln in t.splitlines() if MENU.search(ln)]
    return "// Campaign menu strings of Forsaken Kingdom, from %s\n" % path + "\n".join(keep) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decrypted")
    ap.add_argument("--out", default="FK")
    ap.add_argument("--locales", nargs="*", default=LOCALES)
    a = ap.parse_args()
    for loc in a.locales:
        root = os.path.join(a.out, loc)
        total = 0
        for camp, maps in CAMPAIGNS:
            for i, (mapname, title) in enumerate(maps, 1):
                src = os.path.join(a.decrypted, mapname + ".files")
                dst = os.path.join(root, camp, "%02d_%s_%s" % (i, mapname, title))
                os.makedirs(dst, exist_ok=True)
                shutil.copyfile(wts_path(src, loc), os.path.join(dst, "war3map.wts"))
                if os.path.exists(os.path.join(src, "conversation.json")):
                    if loc == "enUS":   # identical for every language; kept once
                        shutil.copyfile(os.path.join(src, "conversation.json"), os.path.join(dst, "conversation.json"))
                    d = dialogue(mapname, title, src, loc)
                    json.dump(d, open(os.path.join(dst, "dialogue.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                    total += len(d["lines"])
        menu = menu_strings(loc)
        if menu:
            os.makedirs(os.path.join(root, "GLOBAL"), exist_ok=True)
            open(os.path.join(root, "GLOBAL", "GlobalStrings_FK.fdf.txt"), "w", encoding="utf-8").write(menu)
        print("%-5s maps=%d dialogue lines=%d menu=%s" % (loc, sum(len(m) for _, m in CAMPAIGNS), total,
                                                          "yes" if menu else "not installed"))


if __name__ == "__main__":
    main()
