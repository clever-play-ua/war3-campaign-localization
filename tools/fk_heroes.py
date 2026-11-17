"""Hero texts for translation: unit name/title, every hero ability with all levels, their buffs, and abilities whose
numbers the tooltips borrow — in the 12 game languages, plus the installed Ukrainian override when it has them.

    python tools/fk_heroes.py Hctl [Hctr ...] [--game "F:/Games/Warcraft III"] [--out FK/heroes]

Where these texts live (not in the maps, so the maps' war3map.wts never contains them):
* hero abilities of a unit: CASC War3.w3mod:Units/UnitAbilities.slk (heroAbilList); maps may override with w3u `uhab`;
* texts: CASC War3.w3mod:_Locales/<loc>.w3mod:Units/CampaignUnitStrings.txt ([Hctl]: Name = title "Guard Captain",
  Propernames = "Landen", Revivetip, Awakentip, Hotkey) and CampaignAbilityStrings.txt ([AHhs]: Name, Tip and UberTip with
  one value per level, Researchtip, Researchubertip, Hotkey; buffs [BHic]: Bufftip, Buffubertip);
* numbers for <AHhs,DataA1>, <AHen,Dur1>, <AHhs,DataB1,%> (x100): War3.w3mod:Units/AbilityData.slk, columns DataA1.., Dur1..
* in game the translation is a loose file that replaces the whole string file (AllowLocalFiles): the installed Ukrainian
  localization uses _retail_\\_Units\\campaignabilitystrings.txt and campaignunitstrings.txt. Those files predate Forsaken
  Kingdom, so the FK sections must be ADDED to them (a file without [AHhs] leaves Landen's abilities without text).
"""
import argparse, json, os, re, sys

from casc import Casc

LOCALES = ["enUS", "deDE", "esES", "esMX", "frFR", "itIT", "koKR", "plPL", "ptBR", "ruRU", "zhCN", "zhTW"]
LEVEL_FIELDS = {"tip", "ubertip", "bufftip", "buffubertip", "untip", "unubertip", "editorsuffix"}
UA_DIR = r"_retail_\_Units"


def slk_rows(data):
    """SLK -> {first column value: {column name: value}}."""
    cells, y = {}, 0
    for ln in data.decode("utf-8", "replace").splitlines():
        if not ln.startswith("C;"):
            continue
        x = val = None
        for p in ln.split(";")[1:]:
            if p[:1] == "X":
                x = int(p[1:])
            elif p[:1] == "Y":
                y = int(p[1:])
            elif p[:1] == "K":
                val = p[1:].strip('"')
        cells[(y, x)] = val
    header = {x: v for (yy, x), v in cells.items() if yy == 1}
    rows = {}
    for (yy, x), v in sorted(cells.items()):
        if yy > 1:
            rows.setdefault(yy, {})[header.get(x)] = v
    first = header.get(1)
    return {r.get(first): r for r in rows.values() if r.get(first)}


def sections(text):
    """WC3 .txt -> {section id: [(key, raw value)]} (keys in file order)."""
    out = {}
    for m in re.finditer(r"^\[([^\]]+)\]\s*\n(.*?)(?=^\[|\Z)", text, re.M | re.S):
        out[m.group(1)] = [(k, v) for k, v in re.findall(r"^(\w+)=(.*?)\s*$", m.group(2), re.M)]
    return out


def split_levels(raw):
    """'"a, b","c"' or 'a,b,c' -> ['a, b', 'c'] (commas inside quotes kept; tolerant of a missing quote)."""
    out, cur, q = [], "", False
    for ch in raw:
        if ch == '"':
            q = not q
        elif ch == "," and not q:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def unquote(raw):
    return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] == '"' else raw


class GameStrings:
    def __init__(self, game):
        self.casc = Casc(game)
        self.root = self.casc.root()
        self.files, self.missing = {}, set()
        for loc in LOCALES:
            for p in self.root:
                m = re.match(r"War3\.w3mod:_Locales/%s\.w3mod:Units/(\w+Strings)\.txt$" % loc, p)
                if m:
                    try:
                        self.files[(loc, m.group(1))] = sections(self.casc.get(p).decode("utf-8", "replace"))
                    except KeyError:              # listed in the root, data not downloaded (language not installed)
                        self.missing.add(loc)
        self.ua = {}
        ua_dir = os.path.join(game, UA_DIR)
        if os.path.isdir(ua_dir):
            for f in os.listdir(ua_dir):
                if f.lower().endswith("strings.txt"):
                    self.ua[f] = sections(open(os.path.join(ua_dir, f), encoding="utf-8-sig", errors="replace").read())
        self.ability_data = slk_rows(self.casc.get("War3.w3mod:Units/AbilityData.slk"))
        self.unit_abilities = slk_rows(self.casc.get("War3.w3mod:Units/UnitAbilities.slk"))
        skin = sections(self.casc.get("War3.w3mod:Units/AbilitySkin.txt").decode("utf-8", "replace"))
        uskin = sections(self.casc.get("War3.w3mod:Units/UnitSkin.txt").decode("utf-8", "replace"))
        self.art = {k: dict(v).get("Art") for k, v in list(skin.items()) + list(uskin.items())}

    def section(self, sid):
        """{locale: (file name, [(key, raw)])} for every locale that has the section; "ukUA" from the installed override."""
        out = {}
        for (loc, fname), secs in self.files.items():
            if sid in secs and loc not in out:
                out[loc] = (fname + ".txt", secs[sid])
        for fname, secs in self.ua.items():
            if sid in secs:
                out["ukUA"] = (fname, secs[sid])
        return out


def entry(gs, sid, kind, note=None):
    per_loc = gs.section(sid)
    if "enUS" not in per_loc:
        return None
    fname, en = per_loc["enUS"]
    fields = []
    for key, raw_en in en:
        if not unquote(raw_en).strip():
            continue                                  # empty in English (e.g. Landen's unit Ubertip): nothing to translate
        multi = key.lower() in LEVEL_FIELDS and kind != "unit" and not kind.startswith("summoned")
        values = {}
        for loc, (_, kv) in per_loc.items():
            raw = dict(kv).get(key)
            if raw is None:
                continue
            values[loc] = [unquote(x) for x in split_levels(raw)] if multi else unquote(raw)
        fields.append({"key": key, "per_level": multi, "values": values})
    return {"id": sid, "kind": kind, "file": fname, "note": note, "fields": fields,
            "icon": gs.art.get(sid), "data": {k: v for k, v in gs.ability_data.get(sid, {}).items()
                                              if k and v not in (None, "-", "") and re.match(r"(Data[A-I]|Dur|HeroDur|Cool|Cost|Area|Rng)\d$", k)}}


def hero(gs, unit):
    row = gs.unit_abilities.get(unit, {})
    abil = [a for a in (row.get("heroAbilList") or "").split(",") if a]
    out = {"unit": unit, "name": None, "title": None, "sections": []}
    u = entry(gs, unit, "unit")
    if u:
        f = {x["key"]: x["values"].get("enUS") for x in u["fields"]}
        out["name"], out["title"] = f.get("Propernames"), f.get("Name")
        out["sections"].append(u)
    seen = {unit}
    for a in abil:
        e = entry(gs, a, "ability")
        if not e:
            continue
        out["sections"].append(e)
        seen.add(a)
        data = gs.ability_data.get(a, {})
        levels = int(data.get("levels") or 1)
        extra = []
        for k, v in data.items():                    # only real levels: rows keep leftovers of their base ability
            m = re.match(r"(BuffID|UnitID)(\d+)$", k or "")
            if m and int(m.group(2)) <= levels and v and v != "-":
                extra += [("buff" if m.group(1) == "BuffID" else "summoned unit", b) for b in v.split(",")]
        text = json.dumps(e["fields"])
        extra += [("ability used in the tooltip", r) for r in re.findall(r"<(\w{4}),", text) if r != a]
        for kind, sid in extra:
            if sid not in seen:
                seen.add(sid)
                x = entry(gs, sid, kind, note="of %s" % a)
                if x:
                    out["sections"].append(x)
    return out


def fill(text, ability_data):
    """<AHhs,DataA1> -> 100, <AHhs,DataB1,%> -> 25 (x100), from AbilityData.slk; unknown placeholders stay."""
    def rep(m):
        v = ability_data.get(m.group(1), {}).get(m.group(2) + m.group(3))
        try:
            x = float(v) * (100 if m.group(4) else 1)
        except (TypeError, ValueError):
            return m.group(0)
        return "%d" % round(x) if abs(x - round(x)) < 1e-6 else ("%.2f" % x).rstrip("0").rstrip(".")
    return re.sub(r"<(\w{4}),([A-Za-z]+?)(\d+)(,%)?>", rep, text)


def hero_cards(h, ability_data, icon_of, loc="enUS"):
    """Cards for the site (dialogue_page.quest_html layout): the hero's names, then each ability with every level, how
    it is learned, its buffs and summoned units. Numbers filled in; raw text with placeholders under "Show IDs"."""
    from dialogue_page import Cast, quest_html
    cast = Cast()
    by_kind = lambda kind, of: [s for s in h["sections"] if s["kind"].startswith(kind) and s["note"] == "of %s" % of]

    def val(s, key):
        f = next((f for f in s["fields"] if f["key"].lower() == key.lower()), None)
        return (f["key"], f["values"].get(loc)) if f else (key, None)

    def step(s, key, label, level=None):
        k, v = val(s, key)
        if v is None:
            return None
        raw = v[level] if isinstance(v, list) else v
        if not raw.strip():
            return None
        shown = fill(raw, ability_data).replace("%d", "N")
        ids = "%s · %s%s · %s · raw: %s" % (s["id"], k, "" if level is None else " [level %d]" % (level + 1), s["file"], raw)
        return ("msg", label, shown, ids)

    unit = h["sections"][0]
    names = [step(unit, k, label) for k, label in (("Propernames", "Name"), ("Name", "Title"), ("Revivetip", "Revive button"),
                                                   ("Awakentip", "Awaken button"), ("Hotkey", "Hotkey"))]
    cards = [quest_html(cast, {"kind": "Hero", "title": h["name"] or h["unit"], "desc": h["title"] or "",
                               "icon": icon_of(unit["icon"]), "items": [], "ids": "%s · %s" % (h["unit"], unit["file"]),
                               "phases": [("Names", h["unit"], [x for x in names if x])]})]
    for s in h["sections"]:
        if s["kind"] != "ability":
            continue
        tips = val(s, "Tip")[1] or []
        levels = max(len(tips) if isinstance(tips, list) else 1, 1)
        phases = []
        for i in range(levels):
            rows = [step(s, "Tip", "Button", i), step(s, "UberTip", "Tooltip", i)]
            phases.append(("Level %d" % (i + 1) if levels > 1 else "Ability", s["id"], [x for x in rows if x]))
        learn = [step(s, "Researchtip", "Learn button"), step(s, "Researchubertip", "Learn tooltip")]
        phases.append(("Learning", s["id"], [x for x in learn if x]))
        for b in by_kind("buff", s["id"]):
            rows = [step(b, "Bufftip", "Buff name", 0), step(b, "Buffubertip", "Buff tooltip", 0)]
            phases.append(("Buff", b["id"], [x for x in rows if x]))
        for u in by_kind("summoned", s["id"]):
            phases.append(("Summoned unit", u["id"], [x for x in [step(u, "Name", "Unit name")] if x]))
        refs = by_kind("ability used", s["id"])
        name = val(s, "Name")[1] or s["id"]
        hot = val(s, "Hotkey")[1] or ""
        cards.append(quest_html(cast, {
            "kind": ("Ultimate" if levels == 1 else "Ability") + (" · hotkey %s" % hot if hot else ""),
            "title": name, "desc": "", "icon": icon_of(s["icon"]), "items": [],
            "ids": "%s · %s%s" % (s["id"], s["file"], (" · tooltip numbers also from " + ", ".join(
                "%s (%s)" % (r["id"], val(r, "Name")[1]) for r in refs)) if refs else ""),
            "phases": [p for p in phases if p[2]]}))
    return cards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("units", nargs="+")
    ap.add_argument("--game", default=r"F:\Games\Warcraft III")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "FK", "heroes"))
    a = ap.parse_args()
    gs = GameStrings(a.game)
    if gs.missing:
        print("languages not installed in this game copy (no data in CASC):", ", ".join(sorted(gs.missing)))
    os.makedirs(a.out, exist_ok=True)
    for unit in a.units:
        h = hero(gs, unit)
        h["install"] = ("Loose files replace whole string files: put the translated CampaignAbilityStrings.txt and "
                        "CampaignUnitStrings.txt (all original sections + these) into %s (installed Ukrainian base-game "
                        "localization uses this folder; its files have no Forsaken Kingdom sections yet)." % UA_DIR)
        path = os.path.join(a.out, "%s_%s.json" % (unit, re.sub(r"\W+", "_", h["name"] or unit)))
        json.dump(h, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("wrote", os.path.normpath(path))
        for s in h["sections"]:
            locs = sorted({l for f in s["fields"] for l in f["values"]})
            print("  %-6s %-28s %-28s %d fields, languages: %s" % (
                s["id"], s["kind"] + (" " + s["note"] if s["note"] else ""), s["file"], len(s["fields"]),
                ",".join(locs)))


if __name__ == "__main__":
    main()
