"""Forsaken Kingdom: dialogue_clean.json for proofreading (speaker, text, metadata) in every mission of every language.

    python tools/fk_clean.py [--root FK] [--missions 01_HumanRE01_Homecoming ...] [--locales enUS ...]

Order: Blizzard's own order from conversation.json (trigger groups in file order, lines by conversationOrder), the same
for every language. Maps listed in ORDER use a story order worked out by hand from the map script instead.
Text: the language's war3map.wts, facial-animation tags (["Angry" type=quad ...]...[/"Angry"]) removed.
"""
import argparse, glob, json, os, re

from fk_extract import read_wts, FACE

# Story order checked against the decompiled script (HumanRE01): position on the scale of the T (transmission) numbers.
ORDER = {
    "HumanRE01": {
        "anchors": {
            "C010": 5,     # Initialization: Cinematic 01 (M010, no voice) + 02 (C010), then Palace Start Q (T010)
            "C020": 275,   # Palace Enter Check: Cinematic 03 (C020) + 04 (M015), then Palace Throne Room Side Q (T280)
            "M015": 276,
            "M020": 335,   # Palace Throne Room Entrance Enter: Cinematic 05 (M020) -> resume -> Throne Room Start (T340)
            "C030": 395,   # Acolyte Dies: Cinematic 06 (C030) + 07 (M030), then Throne Room Dialogue Exit Q (T400)
            "M030": 396,   # Cinematic 07 resume starts Background Palace (T420/T430)
            "M040": 490,   # Palace Exit Check: Cinematic 08 (M040), then Gardens Guards 01 Q (T500)
            "C040": 580,   # Gardens Exit Check: Cinematic 09 (C040), then Abomination Start; Abomination Stop -> T590
            "M050": 600,   # Victory Abomination Dies: Cinematic 10 (M050), Load Next Level
        },
        "aliases": {"AbominationYes3": ("C040", 20)},          # gg_snd_HRE01_C040_020_FUDGE uses AbominationYes3.flac
        "t_override": {"T470": 295.5, "T480": 295.7},          # palace cultist encounters, enabled after M015
        "groups": {
            "Encounters Palace Guards 01 Q": 295.1, "Encounters Palace Guards 02 Q": 295.2,
            "Encounters Palace Guards 03 Q": 295.3, "Encounters Palace Cultists 01 Q": 295.4,
            "Encounter Palace Skeletons Q": 295.6,
            "Encounter Gardens Skeletons Q": 545.1, "Encounters Gardens Cultists 04 Q": 545.2,
        },
    },
}

REC = re.compile(r"^[A-Za-z0-9]+-([CMT])(\d+)(?:-(\d+))?")


def blizzard_lines(conv_path, wts_path):
    S = read_wts(wts_path)
    conv = json.load(open(conv_path, encoding="utf-8"))["conversation"]
    out = []
    for group, entries in conv.items():
        for e in sorted(entries, key=lambda e: e.get("conversationOrder", 0)):
            label = e.get("animationLabel") or os.path.splitext(os.path.basename(e.get("soundFile") or ""))[0]
            out.append({"group": group, "label": label,
                        "speaker": S.get(e.get("speakerNameId")),
                        "text": FACE.sub("", S.get(e.get("dialogueId"), "")).strip(),
                        "string_id": e.get("dialogueId"), "speaker_string_id": e.get("speakerNameId")})
    return out


def story_position(line, spec):
    m = REC.match(line["label"])
    group = line["group"].split(".")[-1].strip()
    if m:
        series, num, sub = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        if series == "T":
            return spec["t_override"].get("T%03d" % num, float(num))
        return spec["anchors"]["%s%03d" % (series, num)] + sub / 1000.0
    if line["label"] in spec["aliases"]:
        cin, sub = spec["aliases"][line["label"]]
        return spec["anchors"][cin] + sub / 1000.0
    return spec["groups"][group]


def build(mission_dir, conv_path, loc):
    mapname = os.path.basename(mission_dir).split("_")[1]
    title = json.load(open(os.path.join(mission_dir, "dialogue.json"), encoding="utf-8"))["title"]
    lines = blizzard_lines(conv_path, os.path.join(mission_dir, "war3map.wts"))
    if mapname in ORDER:
        spec = ORDER[mapname]
        lines = [x for _, x in sorted(enumerate(lines), key=lambda p: (story_position(p[1], spec), p[0]))]
        note = "Story order taken from the map script. Encounter lines (fights, side events) are placed where they " \
               "usually happen; their exact order depends on the player's route."
    else:
        note = "Blizzard's order from conversation.json (trigger groups as listed, lines by conversationOrder)."
    return {"map": mapname, "title": title, "locale": loc, "note": note,
            "lines": [{"speaker": x["speaker"], "text": x["text"],
                       "metadata": {"string_id": x["string_id"], "speaker_string_id": x["speaker_string_id"],
                                    "dialogue_id": x["label"]}} for x in lines]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="FK")
    ap.add_argument("--missions", nargs="*", default=None)
    ap.add_argument("--locales", nargs="*", default=None)
    a = ap.parse_args()
    count = {}
    for conv in sorted(glob.glob(os.path.join(a.root, "enUS", "*", "*", "conversation.json"))):
        en_dir = os.path.dirname(conv)
        camp, mission = os.path.normpath(en_dir).split(os.sep)[-2:]
        if a.missions and mission not in a.missions:
            continue
        for loc_dir in sorted(glob.glob(os.path.join(a.root, "*", camp, mission))):
            loc = os.path.normpath(loc_dir).split(os.sep)[-3]
            if a.locales and loc not in a.locales:
                continue
            out = build(loc_dir, conv, loc)
            json.dump(out, open(os.path.join(loc_dir, "dialogue_clean.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            count[loc] = count.get(loc, 0) + 1
            if loc == "enUS":
                print("%-45s %4d lines  %s" % (mission, len(out["lines"]), "story order" if out["map"] in ORDER else "Blizzard order"))
    print("files written per language:", count)


if __name__ == "__main__":
    main()
