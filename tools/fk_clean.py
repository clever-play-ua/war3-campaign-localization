"""Forsaken Kingdom: dialogue.json -> dialogue_clean.json (story order, only speaker/text/metadata, for proofreading).

    python tools/fk_clean.py <mission folder name> [--root FK] [--locales enUS ...]
    e.g. python tools/fk_clean.py 01_HumanRE01_Homecoming

The order comes from the map script (decompiled war3map.lua), written down per map in ORDER below:
  * cinematics (C) and in-engine cutscenes (M) sit where the script plays them (CinematicStudio.PlayCinematic, queued by
    the triggers named in the comments), anchored to the gameplay transmission (T) numbers around them;
  * T lines keep their number unless the script plays them elsewhere (override);
  * unit barks without a recording id are placed with the encounter that plays them.
Optional encounters (side quests, fights) depend on the player's route, so their place is the usual one, not a fixed one.
"""
import argparse, glob, json, os, re

ORDER = {
    "HumanRE01": {
        # cinematic id -> position on the T scale
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
        # sound labels that are part of a cinematic: label -> (cinematic, line number)
        "aliases": {"AbominationYes3": ("C040", 20)},          # gg_snd_HRE01_C040_020_FUDGE uses AbominationYes3.flac
        # T lines played elsewhere than their number suggests
        "t_override": {"T470": 295.5, "T480": 295.7},          # palace cultist encounters, enabled after M015
        # barks by trigger group (palace encounters start after M015, garden encounters after M040)
        "groups": {
            "Encounters Palace Guards 01 Q": 295.1, "Encounters Palace Guards 02 Q": 295.2,
            "Encounters Palace Guards 03 Q": 295.3, "Encounters Palace Cultists 01 Q": 295.4,
            "Encounter Palace Skeletons Q": 295.6,
            "Encounter Gardens Skeletons Q": 545.1, "Encounters Gardens Cultists 04 Q": 545.2,
        },
    },
}

REC = re.compile(r"^[A-Za-z0-9]+-([CMT])(\d+)(?:-(\d+))?")


def position(line, spec):
    m = REC.match(line["id"])
    group = line["scene"].split(".")[-1].strip()
    if m:
        series, num, sub = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        if series == "T":
            return spec["t_override"].get("T%03d" % num, float(num))
        key = "%s%03d" % (series, num)
        if key not in spec["anchors"]:
            raise KeyError("no anchor for " + key)
        return spec["anchors"][key] + sub / 1000.0
    if line["id"] in spec["aliases"]:
        cin, sub = spec["aliases"][line["id"]]
        return spec["anchors"][cin] + sub / 1000.0
    if group in spec["groups"]:
        return spec["groups"][group]
    raise KeyError("no place for %s (%s)" % (line["id"], group))


def clean(src, mapname):
    d = json.load(open(src, encoding="utf-8"))
    spec = ORDER[mapname]
    lines = sorted(d["lines"], key=lambda x: (position(x, spec), x["n"]))
    return {
        "map": d["map"],
        "title": d["title"],
        "locale": d.get("locale", "enUS"),
        "note": "Story order taken from the map script. Encounter lines (fights, side events) are placed where they "
                "usually happen; their exact order depends on the player's route.",
        "lines": [{"speaker": x["speaker"], "text": x["text"],
                   "metadata": {"string_id": x["string_id"], "speaker_string_id": x["speaker_string_id"],
                                "dialogue_id": x["id"]}} for x in lines],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mission", help="mission folder name, e.g. 01_HumanRE01_Homecoming")
    ap.add_argument("--root", default="FK")
    ap.add_argument("--locales", nargs="*", default=None)
    a = ap.parse_args()
    mapname = a.mission.split("_")[1]
    for src in sorted(glob.glob(os.path.join(a.root, "*", "*", a.mission, "dialogue.json"))):
        loc = os.path.normpath(src).split(os.sep)[-4]
        if a.locales and loc not in a.locales:
            continue
        out = clean(src, mapname)
        dst = os.path.join(os.path.dirname(src), "dialogue_clean.json")
        json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(dst, len(out["lines"]))


if __name__ == "__main__":
    main()
