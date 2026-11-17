"""Forsaken Kingdom: one-file HTML page with a mission's dialogue in story order (speaker icon, name, line).

    python tools/fk_html.py FK/enUS/000_The_Last_Days_of_Lordaeron/01_HumanRE01_Homecoming
        [--game "F:/Games/Warcraft III"] [--map-files <decrypted map folder>] [--icons de|hd] [--fragment] [--out <file>]

Lines and order come from fk_clean.ordered_lines (the same as dialogue_clean.json).
Icons: speakerUnitID from conversation.json -> icon (dialogue_page.Icons). --icons de (default): _DE first = classic-style
art at 256px for every FK speaker; --icons hd: _HD (Reforged art) first. New FK characters (Landen, Garek, City Guard,
cultists) exist only as base 64px + _DE 256px of the same picture, so they get the _DE one in both modes.
Map-only units (e.g. nvfa, a copy of nvlw) need --map-files to find their base unit in war3map.w3u.
--fragment: no doctype/html/head/body, for publishing as an Artifact. Default output: dialogue.html in the mission folder.
"""
import argparse, collections, json, os, re

from dialogue_page import Icons, Cast, block_html, document, merge_objects, name_class, read_w3u, webp
from fk_clean import ordered_lines, REC

KIND = {"C": "Cinematic", "M": "Cutscene", "T": "Gameplay"}


def campaign_name(loc_root, camp):
    path = os.path.join(loc_root, "GLOBAL", "GlobalStrings_FK.fdf.txt")
    key = "CAMP_HUMANRE_NAME" if camp.startswith("000") else "CAMP_UNDEADRE_NAME"
    if os.path.exists(path):
        m = re.search(r"^%s\s+\"([^\"]*)\"" % key, open(path, encoding="utf-8").read(), re.M)
        if m:
            return m.group(1)
    return camp.split("_", 1)[1].replace("_", " ")


def speakers(lines, en_lines):
    """Final speaker name per line, plus name -> unit for icons.

    Blizzard's conversation.json sometimes names the wrong speaker (HumanRE01 M030-010 is Landen's recording, unit Hctl,
    but speakerNameId says Uther). Fixed when the recording label ends with the unit's usual (English) speaker."""
    major = lambda pairs: {k: c.most_common(1)[0][0] for k, c in pairs.items()}
    en_by_unit, loc_by_unit = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for x, en in zip(lines, en_lines):
        en_by_unit[x["unit"]][en["speaker"]] += 1
        loc_by_unit[x["unit"]][x["speaker"]] += 1
    en_major, loc_major = major(en_by_unit), major(loc_by_unit)
    names, fixed = [], []
    for x, en in zip(lines, en_lines):
        name, usual = x["speaker"], en_major[x["unit"]]
        if en["speaker"] != usual and x["label"].rsplit("-", 1)[-1].upper() == (usual or "").upper():
            name = loc_major[x["unit"]]
            fixed.append(x["label"])
        names.append(name or "?")
    by_name = collections.defaultdict(collections.Counter)
    for name, x in zip(names, lines):
        by_name[name][x["unit"]] += 1
    return names, major(by_name), fixed


def scenes(lines):
    """Group lines into blocks: each cinematic (C) / cutscene (M) recording is its own block, gameplay (T) runs merge."""
    out, key = [], None
    for x in lines:
        m = REC.match(x["label"])
        if m:
            series = m.group(1)
            key = ("T", None) if series == "T" else (series, "%s%03d" % (series, int(m.group(2))))
        elif key is None:
            key = ("T", None)
        if not out or out[-1]["key"] != key:
            out.append({"key": key, "lines": []})
        out[-1]["lines"].append(x)
    return out


def page(mission_dir, game, map_files=None, style="de", fragment=False):
    mission_dir = os.path.normpath(mission_dir)
    parts = mission_dir.split(os.sep)
    loc, camp, mission = parts[-3], parts[-2], parts[-1]
    root = os.sep.join(parts[:-3])
    en_dir = os.path.join(root, "enUS", camp, mission)
    conv = os.path.join(en_dir, "conversation.json")
    mapname, lines, note = ordered_lines(mission_dir, conv)
    en_lines = lines if loc == "enUS" else ordered_lines(en_dir, conv)[1]
    names, unit_of, fixed = speakers(lines, en_lines)
    title = json.load(open(os.path.join(mission_dir, "dialogue.json"), encoding="utf-8"))["title"]
    number = int(mission.split("_")[0])

    objects = merge_objects(*[read_w3u(os.path.join(map_files, f)) for f in ("war3map.w3u", "war3mapSkin.w3u")
                              if map_files and os.path.exists(os.path.join(map_files, f))])
    icons = Icons(game, style, objects)
    cast, missing = Cast(), []
    for n in names:
        if n not in cast.people:
            im, _ = icons.lookup(unit_of[n])
            if im is None:
                missing.append("%s (%s)" % (n, unit_of[n]))
            cast.register(n, name_class(unit_of[n], "human"), webp(im) if im is not None else None)

    body = []
    for sc in scenes([dict(x, name=n) for x, n in zip(lines, names)]):
        series, code = sc["key"]
        body.append(block_html(cast, KIND[series], code, series in ("C", "M"), [
            (x["name"], x["text"], "%s · string %s · name %s" % (x["label"], x["string_id"], x["speaker_string_id"]))
            for x in sc["lines"]]))
    order_note = ("Order follows the mission script. Lines from optional encounters can play in a different order "
                  "depending on the player's route." if "Story order" in note else
                  "Order follows Blizzard's conversation list for this map; it groups lines by trigger and may not "
                  "match the order they play in.")
    camp_name = campaign_name(os.path.join(root, loc), camp)
    doc = document(
        lang=loc[:2] + "-" + loc[2:], title="%s Dialogue" % title, heading=title, fragment=fragment,
        description="%s, mission %d: %s. Every line of dialogue in story order." % (camp_name, number, title),
        eyebrow="%s · Mission %d" % (camp_name, number), cast=cast, body="\n".join(body),
        notes=[order_note, "Warcraft III: Reforged — Forsaken Kingdom. Dialogue, character names and icons "
                           "© Blizzard Entertainment."])
    return doc, fixed, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mission_dir")
    ap.add_argument("--game", default=r"F:\Games\Warcraft III")
    ap.add_argument("--map-files", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--icons", choices=("de", "hd"), default="de")
    ap.add_argument("--fragment", action="store_true", help="no doctype/html/head/body (for publishing as an Artifact)")
    a = ap.parse_args()
    doc, fixed, missing = page(a.mission_dir, a.game, a.map_files, a.icons, a.fragment)
    out = a.out or os.path.join(a.mission_dir, "dialogue.html")
    open(out, "w", encoding="utf-8", newline="\n").write(doc)
    print("wrote %s (%d KB)" % (out, len(doc.encode()) // 1024))
    if fixed:
        print("speaker fixed from recording label:", ", ".join(fixed))
    if missing:
        print("no icon:", ", ".join(missing))


if __name__ == "__main__":
    main()
