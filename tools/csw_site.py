"""Chronicles of the Second War (LoreCraft) campaign site: every string of the campaign sorted into what it is.

    python tools/csw_site.py [--w3n <campaign.w3n>] [--game "F:/Games/Warcraft III"] [--out <folder>] [--data <json>]

Home: Missions (by act), Heroes, Units, Structures, Artifacts, Objects, Other (campaign-level strings,
war3campaign.wts). Each map: Fragments (dialogue scenes and captions in story order), Quests (per quest: Text = log entry
and quest messages, Dialogues, Hints; plus general messages), Factions, Buttons (dialog boxes, leaderboards, timers),
Map objects (the map's own units/items/destructables/doodads/abilities), Other (whatever is left, with the reason).

Every string id of every wts is assigned to exactly one place; the JSON lists them all with their category, so nothing is
lost and a translation editor can be built on it.

Where strings come from:
* object data (war3campaign.* and war3map.*, incl. Skin files): fields holding "TRIGSTR_n" (units, items, abilities with
  levels, buffs, upgrades, destructables, doodads);
* war3map.j: dialogue (localization items -> SetDialogueTextKey), quests, QuestMessageBJ, dialogs, leaderboards, timers,
  captions, SetPlayerName, // Force: comments, SetMapName/Description, StringIdentity;
* war3campaign.w3f: campaign title, description, chapter names.
"""
import argparse, collections, html, json, os, re, struct, sys

from dialogue_page import (CSS, CAMPAIGN_CSS, Icons, LooseFiles, Cast, block_html, caption_html, line_html, merge_objects,
                           name_class, read_w3u, webp, wc3_html, initials)
from tod_html import Map, read_wts_bytes, scene_label, mission_titles
from w3mpq import MpqArchive

FIELD = {"unam": "Name", "upro": "Proper names", "utip": "Tooltip", "utub": "Extended tooltip", "ides": "Description",
         "utpr": "Revive tooltip", "uawt": "Awaken tooltip", "unsf": "Editor suffix", "anam": "Name", "atp1": "Tooltip",
         "aub1": "Extended tooltip", "aret": "Learn tooltip", "arut": "Learn extended tooltip", "aut1": "Tooltip (off)",
         "auu1": "Extended tooltip (off)", "ansf": "Editor suffix", "fnam": "Name", "ftip": "Tooltip",
         "fube": "Extended tooltip", "fnsf": "Editor suffix", "gnam": "Name", "gtip": "Tooltip", "gtub": "Extended tooltip", "gtp1": "Tooltip",
         "gub1": "Extended tooltip",
         "gnsf": "Editor suffix", "bnam": "Name", "bsuf": "Editor suffix", "dnam": "Name", "dsuf": "Editor suffix"}
EDITOR_ONLY = {"unsf", "ansf", "fnsf", "gnsf", "bsuf", "dsuf", "gef1", "gef2", "gef3", "gef4"}   # gef: effect codes
HOTKEYS = {"uhot", "ahky", "auhk", "arhk", "ghk1", "uhkt"}           # hotkey letters: not shown, not translated
DEBUG_RE = re.compile(r"(?i)\bdebug\b|cheat|checkpoint|checlpoint|time to dance")   # developer/cheat messages
KINDS = {"w3u": "unit", "w3t": "item", "w3a": "ability", "w3h": "buff", "w3q": "upgrade", "w3b": "destructable",
         "w3d": "doodad"}
HOME_TABS = [("missions", "Missions"), ("heroes", "Heroes"), ("units", "Units"), ("structures", "Structures"),
             ("artifacts", "Artifacts"), ("objects", "Objects"), ("other", "Other")]


# ------------------------------------------------------------------------------------------------- object data
def read_objects(data, levels):
    """Object data file -> [(id, base id, field, level, value)]. levels: w3a/w3d/w3q carry level and data ints."""
    out = []
    if not data:
        return out
    p = 0

    def take(fmt):
        nonlocal p
        v = struct.unpack_from(fmt, data, p)[0]
        p += struct.calcsize(fmt)
        return v

    ver = take("<i")
    for table in range(2):
        if p >= len(data):
            break
        for _ in range(take("<i")):
            orig, new = take("4s").decode("latin1"), take("4s").decode("latin1")
            oid = new if table else orig
            for _ in range(take("<i") if ver >= 3 else 1):
                if ver >= 3:
                    take("<i")
                for _ in range(take("<i")):
                    fid, typ = take("4s").decode("latin1"), take("<i")
                    lvl = 0
                    if levels:
                        lvl, _ = take("<i"), take("<i")
                    if typ == 0:
                        val = take("<i")
                    elif typ in (1, 2):
                        val = take("<f")
                    else:
                        e = data.index(b"\0", p)
                        val, p = data[p:e].decode("utf-8", "replace"), e + 1
                    take("4s")
                    out.append((oid, orig, fid, lvl, val))
    return out


class Objects:
    """All object data of one scope (campaign or one map): objects[kind][id] = {"base", "fields": {field: [(level, val)]}}."""

    def __init__(self, read, prefix):
        self.objects = {k: {} for k in KINDS.values()}
        for ext, kind in KINDS.items():
            for name in ("%s.%s" % (prefix, ext), "%sSkin.%s" % (prefix, ext)):
                for oid, base, fid, lvl, val in read_objects(read(name), ext in ("w3a", "w3d", "w3q")):
                    o = self.objects[kind].setdefault(oid, {"base": base, "fields": collections.defaultdict(dict)})
                    o["fields"][fid][lvl] = val

    def strings(self):
        """[(string id, kind, object id, field, level)] for TRIGSTR values."""
        out = []
        for kind, objs in self.objects.items():
            for oid, o in objs.items():
                for fid, by_lvl in o["fields"].items():
                    for lvl, val in by_lvl.items():
                        if isinstance(val, str):
                            for m in re.finditer(r"TRIGSTR_(\d+)", val):
                                out.append((int(m.group(1)), kind, oid, fid, lvl))
        return out

    def get(self, kind, oid, fid, lvl=None):
        f = self.objects[kind].get(oid, {}).get("fields", {}).get(fid)
        if not f:
            return None
        return f.get(lvl if lvl is not None else min(f)) if lvl is not None else f[min(f)]


# ------------------------------------------------------------------------------------------------ string registry
class Registry:
    """id -> text for one wts, and the place each id ends up in (first claim wins)."""

    def __init__(self, strings, scope):
        self.S, self.scope, self.place = strings, scope, {}
        self.override = None                         # "unused" while cards of unreachable objects are made
        self.template = {}                           # string id -> (first id of its level group, text with {L})

    def info(self, i):
        t = self.template.get(i)
        return {"same_as": t[0], "template": t[1]} if t else {}

    def claim(self, sid, place, detail=""):
        if sid is None:
            return
        for one in str(sid).split("+"):
            if one.isdigit() and int(one) in self.S and int(one) not in self.place:
                self.place[int(one)] = (place if place == "editor" else self.override or place, detail)

    def text(self, sid):
        return self.S.get(int(sid), "") if sid is not None and str(sid).isdigit() else ""

    def left(self):
        return [i for i in sorted(self.S) if i not in self.place]

    def counts(self):
        c = collections.Counter(p for p, _ in self.place.values())
        c["other"] += len(self.left())
        return c


FACTIONS = ["WC2 Human", "Ironforge", "DarkIron", "Wildhammer", "High Elves", "Dalaran"]


def group_name(raw):
    """Editor suffix -> structure group: "(Ironforge) TH1", "(WC2) (Ironforge)" -> Ironforge; "(WC2) Human Techtree" ->
    WC2 Human; "(Darkiron)" -> DarkIron; a lone "(WC2)" -> WC2; anything else as written without brackets."""
    parens = [p.strip() for p in re.findall(r"\(([^)]*)\)", raw)]
    rest = re.sub(r"\([^)]*\)", " ", raw)
    named = [p for p in parens if p.lower() != "wc2"]
    pick = named[0] if named else ("WC2 Human" if "human" in raw.lower() else parens[0] if parens else raw.strip())
    pick = re.sub(r"\bTH\d\b|\b\d\b", "", pick).strip() or pick
    for f in FACTIONS:
        if pick.lower().replace(" ", "") == f.lower().replace(" ", ""):
            return f
    return pick


def level_template(text, lvl):
    """Tooltip of one ability level with its level number replaced by {L}: "<APdh,DataC2,%>" -> "<APdh,DataC{L},%>",
    "<hPH2,realHP>" -> "<hPH{L},realHP>", "Level 2" -> "Level {L}". Levels with the same template are one text."""
    L = lambda m, k: m.group(k) + ("{L}" if int(m.group(k + 1)) == lvl else m.group(k + 1))
    t = re.sub(r"(<\w{4},(?:Data[A-Z]|Dur|HeroDur|Area|Cool|Cost|Rng|Cast))(\d+)(?=[,>])", lambda m: L(m, 1), text)
    t = re.sub(r"(<\w{3})(\d)(?=,)", lambda m: L(m, 1), t)
    return re.sub(r"(?i)((?<![a-z])level\s*)(\d+)", lambda m: L(m, 1), t)


def trigstr(v):
    m = re.fullmatch(r"TRIGSTR_(\d+)", v or "")
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------------------------------- rendering
esc = html.escape
ICON_POOL = {}                                     # webp base64 -> css class, so a shared icon is stored once


def icon_class(b64):
    if b64 not in ICON_POOL:
        ICON_POOL[b64] = "ic%d" % len(ICON_POOL)
    return ICON_POOL[b64]


def card(kind, title, icon, desc, sections, ids="", main=False):
    """Card in the quest-card layout. sections: [(heading, sub, rows)], row = (label, html, ids) or ("lines", html)."""
    ico = ('<span class="icon %s" aria-hidden="true"></span>' % icon_class(icon)
           if icon else '<span class="icon mono" aria-hidden="true">%s</span>' % esc(initials(title or "?")))
    parts, also_title = [], ""
    title_html = wc3_html(title or "?").strip()
    for k, sec in enumerate(sections):                 # a Name row equal to the card title: the title is that text
        if sec[0] == "Texts":
            rows = [r for r in sec[2] if r]
            nm = next((r for r in rows if r[0] in ("Name", "Name · level 1") and r[1].strip() == title_html), None)
            if nm:
                same = [r for r in rows if r is not nm and r[0] != "lines" and r[1].strip() == title_html]
                rows = [r for r in rows if r is not nm and not any(r is x for x in same)]
                sections = sections[:k] + [(sec[0], sec[1], rows) + tuple(sec[3:])] + sections[k + 1:]
                ids = " | ".join([nm[2]] + [x[2] for x in same]) + (" · " + ids if ids else "")
                if same:
                    also_title = '<p class="title-also">Name, also %s</p>' % esc(", ".join(x[0] for x in same))
            break
    for sec in sections:
        heading, sub, rows = sec[:3]
        sic = '<span class="icon sm %s" aria-hidden="true"></span>' % icon_class(sec[3]) if len(sec) > 3 and sec[3] else ""
        rows = [r for r in rows if r]
        head_html = esc(heading)
        kind_, _, name_ = heading.partition(": ")
        name_row = next((r for r in rows if r[0] == "Name"), None) if name_ else None
        if name_ and not name_row:                     # research: "Name · level 1" equal to the heading
            first = name_.split(" → ")[0].strip()
            name_row = next((r for r in rows if r[0].startswith("Name") and r[0] != "lines"
                             and re.sub("<[^>]+>", "", r[1]).strip() == first), None)
        if name_row:                                   # "Hero ability: X" + a Name row -> the name stands by the icon
            same = [r for r in rows if r is not name_row and r[0] != "lines" and r[1].strip() == name_row[1].strip()]
            rows = [r for r in rows if r is not name_row and not any(r is x for x in same)]
            head_html = ('<span class="ab-name">%s</span><span class="ab-kind">%s%s</span><span class="idc">%s</span>' % (
                name_row[1], esc(kind_.lower()), (" · name, also " + ", ".join(esc(x[0]) for x in same)) if same else "",
                esc(" | ".join([name_row[2]] + [x[2] for x in same]))))
        elif name_ and sic:                            # name not translatable here: same look, no row to fold
            head_html = '<span class="ab-name">%s</span><span class="ab-kind">%s</span>' % (
                wc3_html(name_), esc(kind_.lower()))
        merged = []                                    # rows with the same text: one row, every label
        for r in rows:
            twin = next((m for m in merged if r[0] != "lines" and m[0] != "lines" and m[1].strip() == r[1].strip()), None)
            if twin:
                merged[merged.index(twin)] = (twin[0] + " + " + r[0], twin[1], twin[2] + " | " + r[2])
            else:
                merged.append(r)
        rows = merged
        out = []
        for r in rows:
            if r[0] == "lines":
                out.append('<div class="step"><div class="step-label">%s</div><div>%s</div></div>' % (esc(r[2]), r[1]))
            else:
                lbl = esc(r[0]).replace(" + ", '<span class="same"> = </span>')
                out.append('<div class="step"><div class="step-label">%s</div><div><p class="msg">%s</p>'
                           '<div class="ids">%s</div></div></div>' % (lbl, r[1], esc(r[2])))
        if out or name_row:
            parts.append('<div class="phase"><h4 class="phase-head">%s%s<span class="idc">%s</span></h4>%s</div>' % (
                sic, head_html, esc(sub), "".join(out)))
    return ('<article class="quest%s"><div class="quest-head">%s<div><span class="badge%s">%s</span><h3>%s</h3>%s%s'
            '<div class="ids">%s</div></div></div>%s</article>' % (
                "" if parts else " bare", ico, " main" if main else "", esc(kind), wc3_html(title or "?"), also_title,
                '<p class="quest-desc">%s</p>' % wc3_html(desc) if desc else "", esc(ids), "".join(parts)))


def row(reg, label, sid, extra=""):
    t = reg.text(sid)
    if not t.strip():
        return None
    return (label, wc3_html(t), "string %s%s" % (sid, (" · " + extra) if extra else ""))


# --------------------------------------------------------------------------------------------------- entity cards
class Entities:
    """Cards for objects with translatable fields. icon_of(kind, oid, obj) -> webp b64 or None; base_name(oid) -> str."""

    def __init__(self, objs, reg, icon_of, base_name, scope):
        self.o, self.reg, self.icon_of, self.base_name, self.scope = objs, reg, icon_of, base_name, scope

    def text_rows(self, kind, oid, place):
        """Rows for all TRIGSTR fields of one object, grouped by level; claims the strings."""
        o = self.o.objects[kind].get(oid)
        rows = []
        if not o:
            return rows
        for fid in sorted(o["fields"], key=lambda f: (list(FIELD).index(f) if f in FIELD else 99, f)):
            groups = collections.OrderedDict()           # level template -> [(level, sid)]
            for lvl, val in sorted(o["fields"][fid].items()):
                for sid in [int(x) for x in re.findall(r"TRIGSTR_(\d+)", val)] if isinstance(val, str) else []:
                    if fid in EDITOR_ONLY or fid in HOTKEYS:  # editor suffixes and hotkeys are not shown
                        self.reg.claim(sid, "editor", "%s %s %s" % (kind, oid, fid))
                        continue
                    self.reg.claim(sid, place, "%s %s %s" % (kind, oid, fid))
                    t = self.reg.text(sid)
                    if t.strip():
                        groups.setdefault(level_template(t, lvl).strip() if lvl else ("\0%d" % sid), []).append((lvl, sid))
            for tmpl, members in groups.items():
                lvls, sids = [m[0] for m in members], [m[1] for m in members]
                label = FIELD.get(fid, fid)
                if len(members) == 1:
                    rows.append(row(self.reg, label + (" · level %d" % lvls[0] if lvls[0] else ""), sids[0],
                                    "%s %s · %s" % (kind, oid, fid)))
                    continue
                for sid in sids:                         # one translation fills every level
                    self.reg.template[sid] = (sids[0], tmpl)
                span = ("levels %d–%d" % (lvls[0], lvls[-1]) if lvls == list(range(lvls[0], lvls[-1] + 1))
                        else "levels " + ", ".join(map(str, lvls)))
                shown = re.sub(r"\{L\}", '<b class="lvl">{L}</b>', wc3_html(tmpl))
                rows.append(("%s · %s" % (label, span), shown + '<span class="lvl-note">One text for %s: {L} becomes the '
                             'level number.</span>' % span,
                             "strings %s · %s %s · %s" % ("+".join(map(str, sids)), kind, oid, fid)))
        return rows

    def name(self, kind, oid):
        for fid in ("unam", "anam", "fnam", "gnam", "bnam", "dnam"):
            v = self.o.get(kind, oid, fid, None)
            sid = trigstr(v) if isinstance(v, str) else None
            if sid is not None:
                return self.reg.text(sid) or oid
            if isinstance(v, str) and v:
                return v
        return self.base_name(self.o.objects[kind].get(oid, {}).get("base", oid)) or oid

    def has_text(self, kind, oid):
        o = self.o.objects[kind].get(oid)
        return bool(o) and any(isinstance(v, str) and "TRIGSTR_" in v for f in o["fields"].values() for v in f.values())

    def suffix(self, kind, oid):
        """Editor suffix text, e.g. "(Ironforge)" -> "Ironforge" (used to group structures)."""
        v = self.o.get(kind, oid, {"unit": "unsf", "ability": "ansf", "upgrade": "gnsf"}.get(kind, "unsf"), None)
        if isinstance(v, str):
            v = self.reg.text(trigstr(v)) if trigstr(v) is not None else v
        return group_name(v or "")

    def proper(self, oid):
        """First proper name of a hero ("Khadgar"), or ""."""
        v = self.o.get("unit", oid, "upro", None)
        if isinstance(v, str):
            v = self.reg.text(trigstr(v)) if trigstr(v) is not None else v
        return (v or "").split(",")[0].strip()

    def unit_card(self, oid, place, abil_lists, also=(), title=None, badge=None):
        o = self.o.objects["unit"][oid]
        sections = [("Texts", oid, self.text_rows("unit", oid, place))]
        for fid, heading in (("uhab", "Hero ability"), ("uabi", "Ability")):
            for aid in abil_lists(oid, fid):
                if self.has_text("ability", aid):
                    sections.append(("%s: %s" % (heading, self.name("ability", aid)), aid,
                                     self.ability_rows(aid, place), self.ability_icon(aid)))
        for gid in abil_lists(oid, "ures"):                # upgrades researched here, every level
            if self.has_text("upgrade", gid):
                g = self.o.objects["upgrade"][gid]
                sections.append(("Research: %s" % re.sub(r"\s*,\s*", " → ", self.name("upgrade", gid)), gid, self.text_rows("upgrade", gid, place),
                                 self.icon_of("upgrade", gid, g)))
        return card(badge or ("Hero" if place == "heroes" else "Structure" if place == "structures" else "Unit"),
                    title or self.name("unit", oid), self.icon_of("unit", oid, o), "", sections,
                    ids="unit %s · based on %s%s" % (oid, o["base"], (" · same name: " + ", ".join(also)) if also else ""))

    def ability_icon(self, aid):
        o = self.o.objects["ability"].get(aid) or {"base": aid, "fields": {}}
        return self.icon_of("ability", aid, o)

    def hero(self, name, oids, alist):
        """One hero = every unit version sharing a proper name. Texts that all versions share appear once; texts that
        differ get one row per version. Abilities are merged by identical text, each with its icon."""
        multi = len(oids) > 1
        groups = collections.OrderedDict()               # label -> text html -> ([oids], [ids])
        for oid in oids:
            for lbl, h, i in self.text_rows("unit", oid, "heroes"):
                e = groups.setdefault(lbl, collections.OrderedDict()).setdefault(h, ([], []))
                e[0].append(oid)
                e[1].append(i)
        texts = []
        for lbl, variants_ in groups.items():
            for h, (who, ids) in variants_.items():
                who = list(dict.fromkeys(who))
                tag = " · " + ", ".join(who) if multi and len(who) < len(oids) else ""
                texts.append((lbl + tag, h, " | ".join(ids)))
        abil, cache, per_unit = collections.OrderedDict(), {}, collections.OrderedDict()
        for oid in oids:
            per_unit[oid] = []
            for fid, heading in (("uhab", "Hero ability"), ("uabi", "Ability")):
                for aid in alist(oid, fid):
                    if not self.has_text("ability", aid):
                        continue
                    if aid not in cache:
                        cache[aid] = self.ability_rows(aid, "heroes")
                    nm = self.name("ability", aid)
                    per_unit[oid].append(nm)
                    key = (heading, nm, tuple((l, h) for l, h, i in cache[aid]))
                    e = abil.setdefault(key, {"aids": [], "oids": []})
                    if aid not in e["aids"]:
                        e["aids"].append(aid)
                    e["oids"].append(oid)
        sections = [("Texts", " ".join(oids), texts)]
        for (heading, nm, _), e in abil.items():
            rows = [(l, h, " | ".join(cache[a][k][2] for a in e["aids"])) for k, (l, h, i) in enumerate(cache[e["aids"][0]])]
            who = list(dict.fromkeys(e["oids"]))
            tag = " · only " + ", ".join(who) if multi and len(who) < len(oids) else ""
            sections.append(("%s: %s%s" % (heading, nm, tag), " ".join(e["aids"]), rows, self.ability_icon(e["aids"][0])))
        classes = list(dict.fromkeys(self.name("unit", oid).strip() for oid in oids))
        first = self.o.objects["unit"][oids[0]]
        return {"name": name, "classes": classes, "icon": self.icon_of("unit", oids[0], first), "oids": oids,
                "versions": [(oid, self.name("unit", oid), per_unit[oid]) for oid in oids],
                "lines": sum(len(r) for s_ in sections for r in [s_[2]]), "abilities": len(abil), "sections": sections}

    def ability_rows(self, aid, place):
        rows = self.text_rows("ability", aid, place)
        buffs = set()
        for lvl, v in self.o.objects["ability"].get(aid, {}).get("fields", {}).get("abuf", {}).items():
            buffs.update(x for x in str(v).split(",") if x)
        for b in sorted(buffs):
            if self.has_text("buff", b):
                rows += [(lbl.replace("Name", "Buff name").replace("Tooltip", "Buff tooltip"), h, i)
                         for lbl, h, i in self.text_rows("buff", b, place)]
        return rows

    def compact_rows(self, kind, oid, place):
        """Rows for a list without icons: '<name> · <field>' labels."""
        nm = self.name(kind, oid)
        return [("%s" % lbl, h, "%s · %s" % (i, nm)) for lbl, h, i in self.text_rows(kind, oid, place)]

    def simple_card(self, kind, oid, place, badge):
        o = self.o.objects[kind][oid]
        return card(badge, self.name(kind, oid), self.icon_of(kind, oid, o), "",
                    [("Texts", oid, self.text_rows(kind, oid, place))], ids="%s %s · based on %s" % (kind, oid, o["base"]))


# -------------------------------------------------------------------------------------------- reachability
ID4 = re.compile(rb"'([A-Za-z0-9]{4})'|FourCC\(\"([A-Za-z0-9]{4})\"\)|gg_(?:unit|item|dest)_([A-Za-z0-9]{4})_\d")


def map_seeds(arc):
    """Object ids a map uses: ids in its script, and every 4-byte window of the placed-objects files and w3i (item
    tables). The map's own w3u/w3t copies are not usage."""
    out = set()
    for f in ("war3map.j", "war3map.lua"):
        b = arc.read(f)
        if b:
            out |= {(m.group(1) or m.group(2) or m.group(3)).decode() for m in ID4.finditer(b)}
            if b"ChooseRandomItem" in b:
                out.add("*random")
    for f in ("war3mapUnits.doo", "war3map.doo", "war3map.w3i"):
        b = arc.read(f)
        if b:
            out |= {b[i:i + 4].decode("latin1") for i in range(len(b) - 3)}
    return out


def std_edges(casc, uabil, itemdata):
    """Standard object id -> ids its default data names (trains, builds, sells, researches, abilities, buffs...)."""
    import fk_heroes
    edges = collections.defaultdict(set)
    tok = lambda v: set(re.findall(r"[A-Za-z0-9]{4}", str(v)))
    for table in (uabil, itemdata):
        for oid, row_ in table.items():
            for k, v in row_.items():
                if v and k.lower() in ("abillist", "heroabillist", "abilities"):
                    edges[oid] |= tok(v)
    try:
        for oid, row_ in fk_heroes.slk_rows(casc.get("War3.w3mod:Units/AbilityData.slk")).items():
            for k, v in row_.items():
                if v and k.lower().startswith(("buffid", "efctid", "unitid")):
                    edges[oid] |= tok(v)
    except Exception as e:
        print("AbilityData.slk:", e)
    keys = ("trains", "builds", "researches", "upgrade", "sellitems", "sellunits", "makeitems")
    for race in ("Human", "Orc", "NightElf", "Undead", "Neutral", "Campaign"):
        try:
            txt = casc.get("War3.w3mod:Units/%sUnitFunc.txt" % race).decode("utf-8", "replace")
        except Exception:
            continue
        for m in re.finditer(r"^\[(\w{4})\]\s*\n(.*?)(?=^\[|\Z)", txt, re.M | re.S):
            for k, v in re.findall(r"^(\w+)=(.*)$", m.group(2), re.M):
                if k.lower() in keys:
                    edges[m.group(1)] |= tok(v)
    return edges


STD = {}                                           # standard data the mission pages need (set by build)
HIDDEN_NAME = re.compile(r"(?i)cinematic|\bcine\b|\bdummy\b|\bcaster\b")


def reachable(seeds, scopes, std):
    """Closure from the seeds over object fields (campaign + map object data) and standard default data."""
    edges = collections.defaultdict(set)
    for objs in scopes:
        for kind, d in objs.objects.items():
            for oid, o in d.items():
                for fid, by_lvl in o["fields"].items():
                    for v in by_lvl.values():
                        if isinstance(v, str) and "TRIGSTR" not in v:
                            edges[oid] |= set(re.findall(r"[A-Za-z0-9]{4}", v))
                edges[oid] |= std.get(o["base"], set())
    known = set(edges) | set(std)
    seen, todo = set(), [x for x in seeds if x in known]
    while todo:
        x = todo.pop()
        if x in seen:
            continue
        seen.add(x)
        todo.extend(n for n in edges.get(x, set()) | std.get(x, set()) if n in known and n not in seen)
    return seen


# ---------------------------------------------------------------------------------------------------- the build
def build(w3n, game, style, enemy_races=()):
    camp = MpqArchive(w3n)
    camp_name, titles = mission_titles(camp)
    cS = read_wts_bytes(camp.read("war3campaign.wts"))
    creg = Registry(cS, "campaign")
    cobj = Objects(camp.read, "war3campaign")
    loose = LooseFiles(os.path.join(game, "_retail_"))
    icons = Icons(game, style, {}, archives=(camp, loose))
    import fk_heroes
    gs = fk_heroes.GameStrings(game)
    slk = lambda name: fk_heroes.slk_rows(icons.casc.get("War3.w3mod:Units/%s" % name))
    balance, itemdata, uabil = slk("UnitBalance.slk"), slk("ItemData.slk"), slk("UnitAbilities.slk")
    skins = {}
    for f in ("AbilitySkin", "ItemSkin", "DestructableSkin", "UpgradeSkin"):
        txt = icons.casc.get("War3.w3mod:Units/%s.txt" % f).decode("utf-8", "replace")
        for m in re.finditer(r"^\[(\w{4})\]\s*\n(.*?)(?=^\[|\Z)", txt, re.M | re.S):
            a = re.search(r"^Art(?::hd)?=(.+)$", m.group(2), re.M)
            if a:
                skins.setdefault(m.group(1), a.group(1).strip().split(",")[0])

    def base_name(bid):
        s = gs.section(bid).get("enUS")
        d = dict(s[1]) if s else {}
        return (d.get("Name") or d.get("Bufftip") or "").strip('"') or bid

    def icon_for(objs, archives):
        ic = Icons(game, style, {}, archives=archives)
        cache = {}

        def icon_of(kind, oid, o):
            if (kind, oid) in cache:
                return cache[(kind, oid)]
            art = None
            for fid in ("uico", "iico", "aart", "fart", "gar1"):
                v = objs.get(kind, oid, fid, None) if fid in o.get("fields", {}) else None
                if isinstance(v, str) and v:
                    art = v.split(",")[0]
                    break
            im = ic.image(art) if art else None
            if im is None and kind == "unit":           # unit without its own icon: the base unit's standard icon
                std = ic.standard_art(oid) or ic.standard_art(o.get("base"))
                im = ic.image(std) if std else None
            if im is None and o.get("base") in skins:   # abilities, items, destructables, upgrades
                im = ic.image(skins[o["base"]])
            cache[(kind, oid)] = webp(im, 64) if im is not None else None
            return cache[(kind, oid)]
        return icon_of

    def abil_lists(objs):
        def get(oid, fid):
            v = objs.get("unit", oid, fid, None)
            if v is None:
                base = objs.objects["unit"].get(oid, {}).get("base", oid)
                v = STD["research"].get(base, "") if fid == "ures" else \
                    uabil.get(base, {}).get("heroAbilList" if fid == "uhab" else "abilList", "")
            return [a for a in str(v or "").split(",") if a and a != "_"]
        return get

    def is_hero(oid, base):
        return oid[:1].isupper() or base[:1].isupper()

    def is_building(objs, oid, base):
        v = objs.get("unit", oid, "ubdg", None)
        return bool(v) if v is not None else balance.get(base, {}).get("isbldg") == "1"

    def item_class(objs, oid, base):
        v = objs.get("item", oid, "icla", None)
        return v if isinstance(v, str) and v else itemdata.get(base, {}).get("class", "")

    # ---------------------------------------------------------------------------------------------- maps first
    order = [m for m in titles] if titles else []
    maps = [m for m in order if m != "TestMap"]
    holders = set()
    mission_views, data_maps = [], []
    registry = {}
    STD["edges"] = std_edges(icons.casc, uabil, itemdata)
    STD["uabil"] = uabil
    STD["destr"] = slk("DestructableData.slk")
    unitdata = slk("UnitData.slk")
    STD["research"] = {}
    for race in ("Human", "Orc", "NightElf", "Undead", "Neutral", "Campaign"):
        try:
            txt = icons.casc.get("War3.w3mod:Units/%sUnitFunc.txt" % race).decode("utf-8", "replace")
        except Exception:
            continue
        for m in re.finditer(r"^\[(\w{4})\]\s*\n(.*?)(?=^\[|\Z)", txt, re.M | re.S):
            r = re.search(r"^Researches=(.*)$", m.group(2), re.M)
            if r:
                STD["research"][m.group(1)] = r.group(1).strip()
    seeds, map_objs, here_by_map, direct_by_map = set(), [], {}, {}
    for name in maps:
        mp = Map(camp, name + ".w3x")
        here_seeds = map_seeds(mp.arc)
        seeds |= here_seeds
        map_objs.append(Objects(mp.arc.read, "war3map"))
        here_by_map[name] = reachable(here_seeds, [cobj, map_objs[-1]], STD["edges"])
        direct_by_map[name] = here_seeds
        j = "\n".join(mp.jass.lines)
        holders |= set(re.findall(r"CreateItem\w*\('(\w{4})'", j)) | set(re.findall(r"gg_item_(\w{4})_\d+", j))
        mission_views.append(mission_page(name, titles.get(name, ("", name)), mp, camp, game, style, loose, registry,
                                          icon_for, base_name, abil_lists, is_hero, is_building, item_class, holders))
        data_maps.append(mission_views[-1].pop("data"))

    # named characters that are not heroes (Milan the Scribe): a dialogue speaker's name and 3+ abilities with text
    speakers = {n.strip().lower() for mv in mission_views for n, p_ in mv["cast"].people.items() if p_["count"]}

    def is_character(oid):
        """A dialogue speaker's name and 3+ campaign-made abilities with game text, or 2+ when the unit is flagged
        Special (Milan, Wilhelm, Gavinrad, Tharbek). A Peon with modified Harvest/Repair or a trainable Man at Arms is not."""
        nm = ent.name("unit", oid).strip().lower()
        own = [a_ for a_ in abil_lists(cobj)(oid, "uabi") if any(
                   f not in EDITOR_ONLY and f not in HOTKEYS and f not in ("anam",) and isinstance(v, str) and "TRIGSTR" in v
                   for f, by in cobj.objects["ability"].get(a_, {}).get("fields", {}).items() for v in by.values())
               and cobj.objects["ability"].get(a_, {}).get("base", a_) != a_]
        special = cobj.get("unit", oid, "uspe", None) == 1                      # "Categorization - Special"
        return nm in speakers and (len(own) >= 3 or special and len(own) >= 2)

    # ------------------------------------------------------------------------- what the player can meet at all
    used = reachable(seeds, [cobj] + map_objs, STD["edges"])
    camp_ids = {oid for objs in cobj.objects.values() for oid in objs}

    # -------------------------------------------------------------------------------------------- campaign tabs
    ent = Entities(cobj, creg, icon_for(cobj, (camp, loose)), base_name, "campaign")
    hero_list, enemy_oids, enemy_cards, enemy_struct, enemy_struct_cards = [], [], [], [], []

    def is_enemy(oid):
        """A unit of a race the player only fights (RoA: the Horde): selecting it shows its name and nothing else."""
        o = cobj.objects["unit"][oid]
        race = cobj.get("unit", oid, "urac", None) or unitdata.get(o["base"], {}).get("race", "")
        return str(race).lower() in enemy_races

    def name_only(oids, place="units", badge="Enemy unit"):
        """One card per name: just the name string(s) of these units, claimed for their tab."""
        sids = [trigstr(cobj.get("unit", o, "unam", None) or "") for o in oids]
        for sid, o in zip(sids, oids):
            creg.claim(sid, place, "unit %s unam" % o)
        o = oids[0]
        return [card(badge, ent.name("unit", o), ent.icon_of("unit", o, cobj.objects["unit"][o]), "", [],
                     ids="strings %s · unit %s" % ("+".join(str(x) for x in sids if x is not None), " ".join(oids)))]

    def fill(keep):
        tabs = {k: [] for k, _ in HOME_TABS}
        groups = collections.OrderedDict()               # structures: category (editor suffix) -> name -> [ids]
        heroes, units = collections.defaultdict(list), collections.defaultdict(list)
        for oid, o in sorted(cobj.objects["unit"].items(), key=lambda x: ent.name("unit", x[0]).lower()):
            if not ent.has_text("unit", oid) or not keep(oid):
                continue
            place = "heroes" if is_hero(oid, o["base"]) or is_character(oid) else "structures" if is_building(cobj, oid, o["base"]) else "units"
            if place == "structures":
                groups.setdefault(ent.suffix("unit", oid) or "Other", collections.OrderedDict()).setdefault(
                    ent.name("unit", oid), []).append(oid)
            elif place == "heroes":
                pn = ent.proper(oid) or ent.name("unit", oid)    # "Dal'Rend" and "Dal'rend" are one hero
                heroes[next((k for k in heroes if k.lower() == pn.lower()), pn)].append(oid)
            else:
                units[ent.name("unit", oid)].append(oid)

        def variants(oids, place, **kw):
            """Cards for units sharing a name: identical texts collapse into one card listing the other ids."""
            kept = collections.OrderedDict()
            for oid in oids:
                html_ = ent.unit_card(oid, place, abil_lists(cobj), **kw)
                key = re.sub(r'<div class="ids">.*?</div>|<span class="idc">.*?</span>|<span class="icon[^"]*"[^>]*>.*?</span>',
                             "", html_)
                kept.setdefault(key, []).append(oid)
            return [ent.unit_card(v[0], place, abil_lists(cobj), also=v[1:], **kw) for v in kept.values()]

        out_heroes = []
        for name in sorted(heroes, key=str.lower):       # heroes by proper name: Khadgar, Danath Trollbane, ...
            h = ent.hero(name, heroes[name], abil_lists(cobj))
            h["slug"] = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "hero"
            while any(x["slug"] == h["slug"] for x in hero_list):
                h["slug"] += "-2"
            hero_list.append(h)
            out_heroes.append(h)
        tabs["heroes"] = out_heroes
        for name in sorted(units, key=str.lower):
            enemy = [o for o in units[name] if keep is is_used and is_enemy(o)]
            if enemy:                                   # the player only ever reads an enemy unit's name
                tabs["units"] += name_only(enemy)
                enemy_oids.extend(enemy)
            rest = [o for o in units[name] if o not in enemy]
            if rest:
                tabs["units"] += variants(rest, "units")
        for cat in sorted(groups, key=lambda c: (c == "Other", c.lower())):
            tabs["structures"].append('<h2 class="act-head">%s</h2>' % esc(cat))
            for nm, oids in groups[cat].items():            # one card per name; copies only claim their strings
                foes = [o for o in oids if keep is is_used and is_enemy(o)]
                if foes:                                    # Horde buildings: the name is all one sees
                    tabs["structures"] += name_only(foes, "structures", "Enemy structure")
                    enemy_struct.extend(foes)
                    oids = [o for o in oids if o not in foes]   # an Alliance building of the same name stays full
                    if not oids:
                        continue
                for dup in oids[1:]:
                    ent.unit_card(dup, "structures", abil_lists(cobj))
                tabs["structures"].append(ent.unit_card(oids[0], "structures", abil_lists(cobj), also=oids[1:]))
        if keep is is_used and (enemy_oids or enemy_struct):   # their tooltips, abilities, researches: folded away
            creg.override = "unused"
            enemy_cards.extend(ent.unit_card(o, "units", abil_lists(cobj), badge="Enemy unit (only its name is seen)")
                               for o in enemy_oids)
            enemy_struct_cards.extend(ent.unit_card(o, "structures", abil_lists(cobj),
                                                    badge="Enemy structure (only its name is seen)") for o in enemy_struct)
            creg.override = None
        for oid, o in sorted(cobj.objects["item"].items(), key=lambda x: ent.name("item", x[0]).lower()):
            if not ent.has_text("item", oid) or not keep(oid):
                continue
            cls = item_class(cobj, oid, o["base"])
            if oid in holders:
                tabs["other"].append(ent.simple_card("item", oid, "other", "Text item (used by scripts)"))
            elif cls in ("PowerUp", "Miscellaneous"):
                tabs["objects"].append(ent.simple_card("item", oid, "objects", "Item · %s" % cls))
            else:
                tabs["artifacts"].append(ent.simple_card("item", oid, "artifacts", "Item · %s" % (cls or "item")))
        tabs["objects"] += object_lists(ent, cobj, "objects", keep)
        for kind, badge in (("ability", "Ability (not on a unit above)"), ("upgrade", "Upgrade"), ("buff", "Buff")):
            for oid in sorted(cobj.objects[kind], key=lambda x: ent.name(kind, x).lower()):
                if keep(oid) and ent.has_text(kind, oid) and any(
                        creg.place.get(s) is None for s, k, i, f, l in cobj.strings() if k == kind and i == oid):
                    tabs["other"].append(ent.simple_card(kind, oid, "other", badge))
        return tabs

    def is_used(oid):
        return oid in used or oid in holders
    tabs = fill(is_used)
    creg.override = "unused"                         # everything left: objects no mission ever shows
    unused = fill(lambda oid: not is_used(oid))
    unused["units"] = enemy_cards + unused["units"]
    unused["structures"] = enemy_struct_cards + unused["structures"]
    creg.override = None
    n_unused = sum(1 for oid in camp_ids if not is_used(oid))
    print("reachable campaign objects: %d of %d" % (len(camp_ids) - n_unused, len(camp_ids)))
    tabs = {k: {"used": tabs[k], "unused": unused[k]} for k in tabs}
    # heroes by mission: which maps place or create each version; heroes seen only in cinematics are not needed
    label = {n: (titles.get(n, ("", n))[0] or n) for n in maps}
    cine = {n for n in maps if label[n].lower().startswith("cinematic")}
    keep_h, cine_h = [], []
    for h in tabs["heroes"]["used"]:
        # a hero belongs to the maps that place or script it; a train/revive list alone (a town hall that lists
        # Milan in every chapter) only counts when the hero is placed nowhere
        on = {o: [n for n in maps if o in direct_by_map[n]] or [n for n in maps if o in here_by_map[n]] for o in h["oids"]}
        h["missions"] = [n for n in maps if any(n in on[o] for o in h["oids"])]
        h["version_missions"] = {o: [label[n] for n in on[o]] for o in h["oids"]}
        h["where"] = [label[n] for n in h["missions"]]
        if h["missions"] and all(n in cine for n in h["missions"]):
            h["cine"] = True
            cine_h.append(h)
        elif not h["abilities"]:                       # a name only (Antonidas, Krasus): nothing to play with
            h["noabil"] = True
            cine_h.append(h)
        else:
            keep_h.append(h)
    tabs["heroes"]["used"] = keep_h
    tabs["heroes"]["unused"] = cine_h + tabs["heroes"]["unused"]
    tabs["heroes"]["filters"] = [(n.lower(), label[n]) for n in maps if n not in cine]
    print("heroes: %d in missions, %d cinematic only" % (len(keep_h), len(cine_h)))
    # campaign info (w3f) and whatever is left
    w3f = [int(x) for x in re.findall(rb"TRIGSTR_(\d+)", camp.read("war3campaign.w3f"))]
    info = [r for r in (row(creg, "Campaign / chapter text", s, "war3campaign.w3f") for s in w3f) if r]
    for s in w3f:
        creg.claim(s, "other", "w3f")
    left = creg.left()
    for s in left:
        creg.claim(s, "other", "not referenced by campaign object data")
    other_rows = [r for r in (row(creg, "Not referenced", s, "war3campaign.wts") for s in left) if r]
    tabs["other"]["used"][:0] = [card("Campaign", camp_name, None, "", [("Campaign and chapter names", "w3f", info)]),
                         card("Unclassified", "Other campaign strings", None,
                              "Strings of war3campaign.wts that no campaign object field uses (often left over "
                              "from removed or renamed objects).", [("Strings", "", other_rows)])]
    counts = creg.counts()
    return camp_name, titles, mission_views, tabs, counts, {
        "campaign": camp_name, "source": os.path.basename(w3n), "maps": data_maps,
        "campaign_strings": {str(i): dict({"text": cS[i], "place": creg.place.get(i, ("other", ""))[0],
                                           "detail": creg.place.get(i, ("", ""))[1]}, **creg.info(i)) for i in sorted(cS)}}


def object_lists(ent, objs, place, keep=lambda oid: True):
    """Destructables as a compact list; doodads (names only visible in the editor) folded away."""
    out = []
    rows = [r for oid in sorted(objs.objects["destructable"], key=lambda x: ent.name("destructable", x).lower())
            if ent.has_text("destructable", oid) and keep(oid) for r in ent.compact_rows("destructable", oid, place)]
    if rows:
        out.append(card("Destructables", "Destructables", None, "Gates, trees, crates, bridges: their names show "
                        "when the mouse is over them.", [("Names", "", rows)]))
    for oid in objs.objects["doodad"]:                 # doodad names are seen only in the World Editor: not shown
        if ent.has_text("doodad", oid):
            ent.text_rows("doodad", oid, "editor")
    return out


def mission_page(name, title_pair, mp, camp, game, style, loose, registry, icon_for, base_name, abil_lists, is_hero,
                 is_building, item_class, holders):
    reg = Registry(mp.S, name)
    j = mp.jass
    cast = Cast(registry)
    mobj = Objects(mp.arc.read, "war3map")
    icons = Icons(game, style, merge_objects(*[read_w3u(src.read(f)) for src, f in (
        (camp, "war3campaign.w3u"), (camp, "war3campaignSkin.w3u"), (mp.arc, "war3map.w3u"), (mp.arc, "war3mapSkin.w3u"))
        if src.read(f)]), archives=(mp.arc, camp, loose))

    def speaker(snd, unit):
        nm, text, sid, item = mp.subtitle(snd)
        nm = nm or "?"
        if nm not in cast.registry:
            im = icons.lookup(unit)[0] if unit else None
            cast.register(nm, name_class(unit, "human"), webp(im) if im is not None else None)
        else:
            cast.register(nm, name_class(unit, "human"), None)
        it = mp.items.get(item[5:] if item and item.startswith("item ") else "", {})
        reg.claim(sid, "fragments")
        reg.claim(trigstr(it.get("utip")), "fragments", "speaker name")
        return nm, text, sid, item

    # ---- fragments: story order, blocks by trigger category
    played = mp.story()
    frag_html, frag_n, cur, cur_key = [], 0, [], None

    def flush():
        nonlocal frag_n
        if cur:
            frag_n += 1
            kind, ttl, cin = cur_key
            title = ttl or kind
            if re.fullmatch(r"(?i)voice ?overs?", (title or "").strip()):
                title = "Scripted Voice Lines"           # in-game lines played by triggers, not a cinematic
            # every fragment gets the framed look of a cinematic scene
            frag_html.append(block_html(cast, "Fragment %d" % frag_n, None, True, list(cur), title=title, level=3))
            cur.clear()
    for e in played:
        if e[0] == "caption" and DEBUG_RE.search(e[3] or ""):
            sid = re.search(r'"TRIGSTR_(\d+)"', j.lines[e[1] - 1])
            reg.claim(int(sid.group(1)) if sid else None, "debug", "caption")
            continue
        if e[0] == "caption":
            flush()
            frag_html.append(caption_html(e[3]))
            sid = re.search(r'"TRIGSTR_(\d+)"', j.lines[e[1] - 1])
            reg.claim(int(sid.group(1)) if sid else None, "fragments", "caption")
            continue
        key = scene_label(mp.category.get(e[2], e[2]))
        if key != cur_key:
            flush()
            cur_key = key
        nm, text, sid, item = speaker(e[3], e[4])
        cur.append((nm, text, "%s · %s · string %s" % (e[3][7:], item, sid)))
    flush()

    # ---- quests: per quest Text / Dialogues / Hints
    quest_cards, quest_trigs = [], set()
    for q in mp.quests():
        tsid, title = mp.string(q["title"])
        dsid, desc = mp.string(q["desc"])
        reg.claim(tsid, "quests", "quest title")
        reg.claim(dsid, "quests", "quest description")
        text_rows = [r for r in [row(reg, "Title", tsid), row(reg, "Description", dsid)] if r]
        for it in q["items"]:
            isid = mp.string(it["text"])[0]
            reg.claim(isid, "quests", "requirement")
            r = row(reg, "Requirement", isid)
            if r:
                text_rows.append(r)
        dia, hints = [], []
        for trig, phase, evs in mp.quest_steps(q):
            quest_trigs.add(trig)
            lines = []
            for e in evs:
                if e[0] == "line":
                    nm, t, sid, item = speaker(e[3], e[4])
                    lines.append((nm, t, "%s · %s · string %s" % (e[3][7:], item, sid)))
                elif e[0] == "ui" and (e[3] == "Secret" or DEBUG_RE.search(e[4] or "")):
                    reg.claim(e[5], "debug", "%s %s" % (e[3], trig))
                elif e[0] == "ui":
                    place = "quests"
                    reg.claim(e[5], place, e[3])
                    r = (e[3], wc3_html(e[4]), "string %s · %s" % (e[5], trig))
                    (hints if e[3] == "Hint" else text_rows).append(r)
            if lines:
                dia.append(("lines", "".join(line_html(cast, n, t, i, played=False) for n, t, i in lines),
                            phase))
        sections = [("Text", q["category"] or "", text_rows), ("Dialogues", "", dia), ("Hints", "", hints)]
        im = icons.image(q["icon"]) if q.get("icon") else None
        quest_cards.append(card("Optional quest" if "_OPT_" in q["type"] else "Main quest", title or "?",
                                webp(im, 64) if im is not None else None, "", sections, main="_OPT_" not in q["type"],
                                ids="%s · category %s" % (q["var"], q["category"])))
    # messages, hints, dialogs, factions outside quests
    general, hints_g, buttons, factions = [], [], [], []
    for trig, fn in j.actions.items():
        for no, s in j.funcs.get(fn, ([], None))[0]:
            m = re.search(r'(DialogSetMessageBJ|DialogAddButtonBJ|DialogAddQuitButton)\([^"]*"TRIGSTR_(\d+)"', s)
            if m:
                sid = int(m.group(2))
                if reg.place.get(sid) is None:
                    reg.claim(sid, "buttons")
                    r = row(reg, "Dialog title" if "Message" in m.group(1) else "Button", sid, trig)
                    if r:
                        buttons.append(r)
                continue
            m = re.search(r'(LeaderboardAddItemBJ|LeaderboardSetLabelBJ|CreateLeaderboardBJ|CreateTimerDialogBJ|'
                          r'CreateMultiboardBJ|MultiboardSetItem\w*)\([^"]*"TRIGSTR_(\d+)"', s)
            if m:
                sid = int(m.group(2))
                if reg.place.get(sid) is None:
                    reg.claim(sid, "buttons")
                    r = row(reg, {"CreateTimerDialogBJ": "Timer window", "CreateMultiboardBJ": "Board title"}.get(
                        m.group(1), "Leaderboard"), sid, trig)
                    if r:
                        buttons.append(r)
                continue
            m = re.search(r'SetPlayerName\([^"]*"TRIGSTR_(\d+)"', s)
            if m:
                sid = int(m.group(1))
                reg.claim(sid, "factions")
                r = row(reg, "Player name", sid, trig)
                if r and r not in factions:
                    factions.append(r)
                continue
            m = re.search(r'QuestMessageBJ\([^,]+,\s*bj_QUESTMESSAGE_(\w+),\s*"TRIGSTR_(\d+)"', s) or \
                re.search(r'(Display(?:Timed)?TextToForce|CustomDefeatBJ|CustomVictoryBJ)\([^"]*"TRIGSTR_(\d+)"', s)
            if m:
                sid = int(m.group(2))
                if m.group(1) == "SECRET" or DEBUG_RE.search(reg.text(sid)):
                    reg.claim(sid, "debug", "%s %s" % (m.group(1), trig))
                    continue
                if reg.place.get(sid) is None:
                    reg.claim(sid, "quests", "message")
                    label = {"HINT": "Hint", "ALWAYSHINT": "Hint"}.get(m.group(1), m.group(1).replace("_", " ").title())
                    r = row(reg, label, sid, trig)
                    if r:
                        (hints_g if label == "Hint" else general).append(r)
    for ln in j.lines:
        m = re.search(r"// Force: TRIGSTR_(\d+)", ln)
        if m:
            reg.claim(int(m.group(1)), "factions")
            r = row(reg, "Force (team)", int(m.group(1)))
            if r and r not in factions:
                factions.append(r)
    if general or hints_g:
        quest_cards.append(card("Messages", "Other messages and hints", None,
                                "Texts the scripts show outside the quests above: defeat screens, captions, hints.",
                                [("Text", "", general), ("Hints", "", hints_g)]))
    # ---- map objects
    ent = Entities(mobj, reg, icon_for(mobj, (mp.arc, camp, loose)), base_name, name)
    obj_cards, item_texts, editor_rows = [], [], []
    cobj_ = Objects(camp.read, "war3campaign")
    here = reachable(map_seeds(mp.arc), [mobj, cobj_], STD["edges"])

    def field(kind, oid, fid):
        for objs in (mobj, cobj_):
            v = objs.get(kind, oid, fid, None)
            if v is not None:
                return v
        return None

    def seen(kind, oid):
        """Can the player meet this object here? Not when no placed object / script / shop reaches it, when its name
        says cinematic or dummy, when it has Locust (unselectable), or for a destructable that is not selectable."""
        o = mobj.objects[kind][oid]
        if kind != "destructable" and oid not in here:
            return False
        names = [ent.name(kind, oid)] + [field(kind, oid, f) or "" for f in ("unsf", "bsuf")]
        if any(HIDDEN_NAME.search(reg.text(trigstr(n)) if trigstr(n) is not None else str(n)) for n in names):
            return False
        if kind == "unit":
            abil = field("unit", oid, "uabi")
            if abil is None:
                abil = STD["uabil"].get(o["base"], {}).get("abilList", "")
            if "Aloc" in str(abil).split(","):
                return False
        if kind == "destructable":
            sel = field("destructable", oid, "bgse")
            if sel is None:
                sel = STD["destr"].get(o["base"], {}).get("selectable", "0")
            return str(sel) == "1"
        return True

    hidden_objs = []
    for kind in ("unit", "item", "ability", "buff", "upgrade"):
        for oid in sorted(mobj.objects[kind]):
            if not ent.has_text(kind, oid) or kind == "item" and oid in holders:
                continue
            if not seen(kind, oid):
                hidden_objs.append((kind, oid))
    hidden_set = set(hidden_objs)
    by_name = collections.OrderedDict()                # (kind, badge, name) -> [oids], so copies show once
    for kind in ("unit", "item", "ability", "buff", "upgrade"):
        for oid in sorted(mobj.objects[kind], key=lambda x: ent.name(kind, x).lower()):
            if not ent.has_text(kind, oid) or (kind, oid) in hidden_set:
                continue
            o = mobj.objects[kind][oid]
            if kind == "item" and oid in holders:          # text items: name = editor label, tooltip/description = text
                for s, k, i, f, l in mobj.strings():
                    if k != "item" or i != oid or reg.place.get(s):
                        continue
                    if f == "unam":
                        reg.claim(s, "editor", "text item %s name" % oid)
                        r = row(reg, "Text item name", s, "item %s" % oid)
                        if r:
                            editor_rows.append(r)
                    else:
                        reg.claim(s, "fragments", "text item %s %s" % (oid, f))
                        r = row(reg, "Speaker" if f == "utip" else "Text", s, "item %s %s" % (oid, f))
                        if r:
                            item_texts.append(r)
                continue
            badge = ("Hero" if is_hero(oid, o["base"]) else "Structure" if is_building(mobj, oid, o["base"]) else "Unit") \
                if kind == "unit" else kind.title()
            by_name.setdefault((kind, badge, ent.name(kind, oid)), []).append(oid)

    def obj_card(kind, badge, oid, also=()):
        if kind == "unit":
            return ent.unit_card(oid, "objects", abil_lists(mobj), also=also).replace(
                '<span class="badge">Unit</span>', '<span class="badge">%s</span>' % badge, 1)
        c_ = ent.simple_card(kind, oid, "objects", badge)
        return c_.replace('</div></div></div>', ' · same name: %s</div></div></div>' % ", ".join(also), 1) if also else c_

    for (kind, badge, nm), oids in by_name.items():
        kept = collections.OrderedDict()               # same name and same texts -> one card
        for oid in oids:
            key = re.sub(r'<div class="ids">.*?</div>|<span class="idc">.*?</span>|<span class="icon[^"]*"[^>]*>.*?</span>',
                         "", obj_card(kind, badge, oid))
            kept.setdefault(key, []).append(oid)
        for v in kept.values():
            obj_cards.append(obj_card(kind, badge, v[0], v[1:]))
    obj_cards += object_lists(ent, mobj, "objects", lambda oid: seen("destructable", oid))
    reg.override = "unused"
    hid = [ent.simple_card(k, oid, "objects", "%s (not seen)" % k.title()) for k, oid in hidden_objs]
    hid += object_lists(ent, mobj, "objects", lambda oid: not seen("destructable", oid))
    reg.override = None
    if hid:
        obj_cards.append('<details class="appendix unused"><summary>Not seen by the player (%d): cinematic-only, '
                         'dummy or unselectable objects, decorations without a hover name, and objects nothing on '
                         'this map places or creates</summary>\n%s</details>' % (
                             len(hidden_objs) + sum(c_.count('<div class="step">') for c_ in hid[len(hidden_objs):]),
                             "\n".join(hid)))
    if item_texts:
        frag_html.append('<details class="appendix"><summary>Other texts kept in text items (%d)</summary><p>Lines '
                         'the scripts read from text items without playing them as dialogue here: floating texts, '
                         'barks, unused lines.</p>%s</details>' % (len(item_texts), card(
                             "Text items", "Texts kept in text items", None, "", [("Texts", "", item_texts)])))
    # ---- script texts not caught above: into the quest tab, under the quest whose trigger uses them
    sec_of_line = {}
    for fn, (body, sec) in j.funcs.items():
        for no, s in body:
            sec_of_line[no] = (sec, s)
    quest_of_trig = {}
    for q in mp.quests():
        for t in q["triggers"]:
            quest_of_trig[t] = mp.string(q["title"])[1] or q["var"]
    script_rows = []
    for no, (sec, s) in sorted(sec_of_line.items()):
        for x in re.findall(r"TRIGSTR_(\d+)", s):
            sid = int(x)
            if reg.place.get(sid) is not None or re.search(r"SetMap(Name|Description)", s):
                continue
            if DEBUG_RE.search(reg.text(sid)):
                reg.claim(sid, "debug", "script %s" % sec)
                continue
            call = (re.search(r"(\w+)\(", s) or re.search(r"set (\w+)", s) or [None, "script"])[1]
            reg.claim(sid, "quests", "script %s" % call)
            r = row(reg, call, sid, "%s%s" % (sec, (" · quest " + quest_of_trig[sec]) if sec in quest_of_trig else ""))
            if r:
                script_rows.append(r)
    if script_rows:
        quest_cards.append(card("Script", "Other texts set by the scripts", None,
                                "Texts passed to other script calls or kept in variables: requirement counters, "
                                "messages built at run time. The trigger name (and quest) is under Show IDs.",
                                [("Texts", "", script_rows)]))
    # ---- the rest
    other_rows = []
    sids_in_j = {int(x): ln for ln in j.lines for x in re.findall(r"TRIGSTR_(\d+)", ln)}
    for sid in reg.left():
        ln = sids_in_j.get(sid)
        why = "map name / description" if ln and re.search(r"SetMap(Name|Description)", ln) else \
            "script: %s" % re.sub(r"\s+", " ", ln.strip())[:60] if ln else \
            "map info (w3i) or not referenced" if sid in set(int(x) for x in re.findall(rb"TRIGSTR_(\d+)", mp.arc.read("war3map.w3i") or b"")) \
            else "not referenced"
        reg.claim(sid, "other", why)
        r = row(reg, why.split(":")[0].capitalize(), sid, why)
        if r:
            other_rows.append(r)
    c = reg.counts()
    story = "\n".join(frag_html)
    said = collections.Counter(re.findall(r'<div class="line" data-s="([^"]+)"', story))
    chips = "".join('<button class="chip" type="button" aria-pressed="false" data-s="%s">%s%s <small>%d</small></button>'
                    % (p["slug"], cast.icon_span(n), esc(n), said[p["slug"]])
                    for n, p in cast.people.items() if said[p["slug"]])
    if chips:
        story = ('<div class="cast-filter" role="group" aria-label="Show one speaker\'s lines">%s</div>\n' % chips) + story
    tabs = [
        ("fragments", "Storyline text", c["fragments"], story or '<p class="empty-note">No dialogue.</p>'),
        ("quests", "Quests", c["quests"], "\n".join(quest_cards) or '<p class="empty-note">No quests.</p>'),
        ("factions", "Factions", c["factions"], card("Factions", "Players and forces", None, "", [("Names", "", factions)])
         if factions else '<p class="empty-note">No faction names.</p>'),
        ("buttons", "Buttons", c["buttons"], card("Screen", "Dialogs, leaderboards and timers", None, "",
                                                  [("Texts", "", buttons)]) if buttons else
         '<p class="empty-note">No dialog buttons or boards.</p>'),
        ("objects", "Map objects", c["objects"], "\n".join(obj_cards) or '<p class="empty-note">No map objects.</p>'),
        ("other", "Other", c["other"] + c["editor"], (card("Other", "Other strings of this map", None,
                                            "Map name and description, map info and strings nothing uses.",
                                            [("Strings", "", other_rows)]) if other_rows else "") +
         ('<details class="appendix"><summary>Editor-only names (%d): text item labels, not shown in the game</summary>'
          '%s</details>' % (len(editor_rows), card("Editor", "Text item names", None, "", [("Names", "", editor_rows)]))
          if editor_rows else "") or '<p class="empty-note">Nothing left.</p>'),
    ]
    lines_total = sum(p["count"] for p in cast.people.values())
    small, big = title_pair
    return {"slug": name.lower(), "label": small or name, "title": big or name, "cast": cast, "tabs": tabs,
            "stats": "%d strings · %d lines · %d quests" % (len(mp.S), lines_total, len(mp.quests())),
            "data": {"map": name, "label": small, "title": big, "strings": {
                str(i): dict({"text": mp.S[i], "place": reg.place.get(i, ("other", ""))[0],
                              "detail": reg.place.get(i, ("", ""))[1]}, **reg.info(i)) for i in sorted(mp.S)},
                "counts": dict(c)}}


# --------------------------------------------------------------------------------------------------- the page
SITE_CSS = """
.home-tabs{margin-top:26px}
.lvl{color:var(--gold);font-weight:700}
.quest.bare .quest-head{border-bottom:0}
.title-also{margin:0 0 6px;color:var(--muted);font-size:13px;letter-spacing:.04em}
.ab-name{font:700 19px/1.2 "Alegreya SC",Georgia,serif;letter-spacing:.03em;color:var(--ink);text-transform:none}
.ab-kind{display:block;margin:3px 0 0 44px;font:600 11px/1.2 "Alegreya Sans",system-ui,sans-serif;letter-spacing:.1em;
text-transform:uppercase;color:var(--muted)}
.phase-head .icon.sm{float:left}
.phase-head::after{content:"";display:block;clear:both}
.step-label .same{color:var(--gold)}
.lvl-note{display:block;margin-top:6px;color:var(--muted);font-size:13px;font-style:italic}
.cast-filter{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0 4px;padding-bottom:18px;border-bottom:1px solid var(--line)}
.panel.focus .line:not(.on){opacity:.28}
.act-head{margin:28px 0 10px;font:700 15px/1 "Alegreya SC",Georgia,serif;letter-spacing:.1em;color:var(--gold)}
.count-note{color:var(--muted);font-size:15px;margin:14px 0 0}
.quest .phase .step .line{padding:4px 0}
.phase-head .icon.sm{display:inline-block;width:34px;height:34px;vertical-align:middle;margin:0 10px 0 0;border-radius:4px}
.hero-row{grid-template-columns:64px minmax(0,1fr) auto}
.hero-row .icon{width:64px;height:64px}
.hero-row .mission-title{font-size:26px}
.hero-classes{display:block;color:var(--hero);font-size:16px;margin-top:2px}
.hero-where{display:block;color:var(--muted);font-size:14px;margin-top:3px}
.hero-row[hidden]{display:none}
.hero-meta{color:var(--muted);font-size:15px;text-align:right;white-space:nowrap}
.hero-meta b{color:var(--ink);font-weight:600}
.hero-top{display:grid;grid-template-columns:96px minmax(0,1fr);gap:20px;align-items:center}
.hero-top .icon{width:96px;height:96px}
.versions{list-style:none;padding:0;margin:18px 0 0;color:var(--muted);font-size:15px}
.versions li{padding:3px 0}.versions code{color:var(--ink);font:13px ui-monospace,Consolas,monospace;margin-right:8px}
.copy-link{margin-left:8px}
@media (max-width:560px){.hero-row{grid-template-columns:48px minmax(0,1fr)}.hero-row .icon{width:48px;height:48px}
.hero-row .mission-title{font-size:21px}.hero-meta{grid-column:2;text-align:left;white-space:normal}
.hero-top{grid-template-columns:64px minmax(0,1fr);gap:14px}.hero-top .icon{width:64px;height:64px}}
"""

SITE_JS = """
(function(){
  var body=document.body,views=[].slice.call(document.querySelectorAll('.view')),current=null;
  function route(){
    var h=decodeURIComponent(location.hash.slice(1)),slug,tab='';
    if(h.indexOf('/')>0){slug=h}else{var parts=h.split('-');slug=parts[0]||'home';tab=parts[1]||''}
    var view=views.filter(function(v){return v.dataset.slug===slug})[0]||views[0];
    views.forEach(function(v){v.hidden=v!==view});
    var tabs=view.querySelectorAll('.tab');if(!tab&&tabs.length)tab=tabs[0].dataset.tab;
    tabs.forEach(function(t){var on=t.dataset.tab===tab;t.setAttribute('aria-selected',String(on));t.tabIndex=on?0:-1;
      document.getElementById(t.getAttribute('aria-controls')).hidden=!on});
    if(view!==current){window.scrollTo(0,0);current=view;document.querySelectorAll('.panel.focus').forEach(function(p){p.classList.remove('focus')});
      document.querySelectorAll('.chip[data-s]').forEach(function(c){c.setAttribute('aria-pressed','false')})}
  }
  document.querySelectorAll('.tab').forEach(function(t){t.addEventListener('click',function(){
    location.hash=t.dataset.view+'-'+t.dataset.tab})});
  document.querySelectorAll('.chip[data-s]').forEach(function(c){c.addEventListener('click',function(){
    var view=c.closest('.panel')||c.closest('.view'),on=c.getAttribute('aria-pressed')!=='true';
    view.querySelectorAll('.chip').forEach(function(o){o.setAttribute('aria-pressed','false')});
    view.classList.toggle('focus',on);
    view.querySelectorAll('.line').forEach(function(l){l.classList.toggle('on',on&&l.dataset.s===c.dataset.s)});
    if(on){c.setAttribute('aria-pressed','true');
      var f=[].filter.call(view.querySelectorAll('.line.on'),function(l){return l.offsetParent!==null})[0];
      if(f)f.scrollIntoView({block:'center'})}
  })});
  var toggles=document.querySelectorAll('.ids-toggle');
  toggles.forEach(function(b){b.addEventListener('click',function(){var on=b.getAttribute('aria-pressed')!=='true';
    toggles.forEach(function(o){o.setAttribute('aria-pressed',String(on))});body.classList.toggle('show-ids',on)})});
  document.querySelectorAll('.copy-link').forEach(function(b){b.addEventListener('click',function(){
    var u=location.href.split('#')[0].replace(/[^\/]*$/,'')+b.dataset.path+'/';
    (navigator.clipboard?navigator.clipboard.writeText(u):Promise.reject()).then(function(){b.textContent='Link copied'},
      function(){window.prompt('Link',u)})})});
  document.querySelectorAll('.mfilter .chip').forEach(function(b){b.addEventListener('click',function(e){
    var p=b.closest('.panel'),m=b.dataset.m;
    p.querySelectorAll('.mfilter .chip').forEach(function(o){o.setAttribute('aria-pressed',String(o===b))});
    p.querySelectorAll('.hero-list .hero-row').forEach(function(r){
      r.hidden=!!m&&(' '+r.dataset.m+' ').indexOf(' '+m+' ')<0})})});
  window.addEventListener('hashchange',route);route();
})();
"""


def tabbar(view, tabs):
    return '<div class="tabs" role="tablist">%s</div>\n%s' % (
        "".join('<button class="tab" role="tab" type="button" id="tab-%s-%s" aria-controls="panel-%s-%s" data-view="%s" '
                'data-tab="%s" aria-selected="%s">%s<small>%s</small></button>' % (
                    view, s, view, s, view, s, "true" if i == 0 else "false", esc(label), n)
                for i, (s, label, n, _) in enumerate(tabs)),
        "\n".join('<section class="panel" id="panel-%s-%s" role="tabpanel" aria-labelledby="tab-%s-%s"%s>\n%s\n</section>'
                  % (view, s, view, s, "" if i == 0 else " hidden", h) for i, (s, label, n, h) in enumerate(tabs)))


def hero_face(h, cls="icon"):
    return ('<span class="%s %s" aria-hidden="true"></span>' % (cls, icon_class(h["icon"])) if h["icon"] else
            '<span class="%s mono hero" aria-hidden="true">%s</span>' % (cls, esc(initials(h["name"]))))


def hero_row(h, labels=None):
    ms = [m.lower() for m in h.get("missions", [])]
    where = ("Cinematic only" if h.get("cine") else "No abilities" if h.get("noabil") else
             " · ".join(labels.get(m, m) for m in ms if m in labels) if labels else "")
    return ('<a class="mission-link hero-row" href="#heroes/%s" data-m="%s">%s<span><span class="mission-title">%s</span>'
            '<span class="hero-classes">%s</span>%s</span><span class="hero-meta"><b>%d</b> lines · <b>%d</b> abilities%s'
            '</span></a>' % (h["slug"], " ".join(ms), hero_face(h), esc(h["name"]), esc(" · ".join(h["classes"])),
                             '<span class="hero-where">%s</span>' % esc(where) if where else "", h["lines"],
                             h["abilities"], (" · <b>%d</b> versions" % len(h["oids"])) if len(h["oids"]) > 1 else ""))


def hero_view(h):
    vm = h.get("version_missions", {})
    versions = "".join('<li><code>%s</code>%s%s%s</li>' % (oid, esc(cls), (": " + esc(", ".join(ab))) if ab else "",
                                                          (' <span class="hero-where">— %s</span>' % esc(", ".join(vm[oid])))
                                                          if vm.get(oid) else "")
                       for oid, cls, ab in h["versions"])
    note = ("The campaign has %d versions of this hero (one per chapter or story moment). Texts they share are shown "
            "once; a text that differs is marked with the versions that use it." % len(h["oids"])
            if len(h["oids"]) > 1 else "")
    body = card("Hero", h["name"], None, "", h["sections"]).split('</div></div></div>', 1)[1][:-len("</article>")]
    return ('<section class="view" data-slug="heroes/%s" hidden>\n<nav class="crumbs"><a href="#home-heroes">&lsaquo; All '
            'heroes</a></nav>\n<header class="hero-top">%s<div><p class="eyebrow">Hero</p><h1>%s</h1><p class="meta">%s · '
            '%d lines · %d abilities</p>%s</div></header>\n<div class="bar"><div class="tools"><button class="btn ids-toggle" '
            'type="button" aria-pressed="false">Show IDs</button><button class="btn copy-link" type="button" '
            'data-path="heroes/%s">Copy link</button></div></div>\n%s%s\n<article class="quest">%s</article>\n</section>' % (
                h["slug"], hero_face(h), esc(h["name"]), esc(" · ".join(h["classes"])), h["lines"], h["abilities"],
                ('<p class="hero-where">Appears in: %s</p>' % esc(", ".join(h.get("where", [])))
                 if h.get("version_missions") else ""),
                h["slug"], ('<p class="panel-intro">%s</p>' % note) if note else "",
                ('<ul class="versions">%s</ul>' % versions) if len(h["oids"]) > 1 else "", body))


def document(camp_name, missions, home_tabs, counts, total):
    registry = missions[0]["cast"].registry if missions else {}
    heroes, unused_heroes = home_tabs["heroes"]["used"], home_tabs["heroes"]["unused"]
    filters = home_tabs["heroes"].pop("filters", [])
    labels = dict(filters)
    hero_views = [hero_view(h) for h in heroes + unused_heroes]
    shown, hidden = {}, {}
    for k, v in home_tabs.items():
        used, unused = v["used"], v["unused"]
        if k == "heroes":
            bar = ('<div class="cast-filter mfilter" role="group" aria-label="Heroes of one mission">'
                   '<button class="chip" type="button" aria-pressed="true" data-m="">All missions <small>%d</small>'
                   '</button>%s</div>' % (len(used), "".join(
                       '<button class="chip" type="button" aria-pressed="false" data-m="%s">%s <small>%d</small></button>'
                       % (m, esc(lbl), sum(1 for h in used if m in [x.lower() for x in h.get("missions", [])]))
                       for m, lbl in filters)))
            used = [bar, '<div class="missions hero-list">%s</div>' % "".join(hero_row(h, labels) for h in used)] \
                if used else []
            unused = ['<div class="missions">%s</div>' % "".join(hero_row(h) for h in unused)] if unused else []
        shown[k] = used
        n = len(unused_heroes) if k == "heroes" else sum(1 for x in unused if not x.startswith("<h2"))
        if n:
            shown[k] = used + ['<details class="appendix unused"><summary>Not used in any mission (%d): no map places, '
                               'sells, trains or scripts these%s, so the player never sees them. Translate last, if at '
                               'all.</summary>\n%s</details>' % (n, " (heroes seen only in cinematics or without abilities are here too)"
                                                                  if k == "heroes" else "", "\n".join(unused))]
    home_tabs = shown
    icon_css = "\n".join(".i-%s{background-image:url(data:image/webp;base64,%s)}" % (p["slug"], p["icon"])
                         for p in registry.values() if p["icon"]) + "\n" + "\n".join(
        ".%s{background-image:url(data:image/webp;base64,%s)}" % (c, b) for b, c in ICON_POOL.items())
    items = []
    for i, m in enumerate(missions):
        if i == 0 or (m["label"].startswith(("Cinematic", "Act")) and i):
            items.append('<li class="act-head">%s</li>' % esc(m["label"].replace("Cinematic ", "")))
        faces = "".join(m["cast"].icon_span(n) for n in sorted(
            [n for n, p in m["cast"].people.items() if p["count"]], key=lambda n: -m["cast"].people[n]["count"])[:8])
        items.append('<li><a class="mission-link" href="#%s"><span class="mission-no">%d<small>%s</small></span>'
                     '<span><span class="mission-title">%s</span><span class="mission-stats">%s</span>'
                     '<span class="mission-cast">%s</span></span><span class="arrow" aria-hidden="true">&rsaquo;</span>'
                     '</a></li>' % (m["slug"], i, esc(m["label"]), esc(m["title"]), m["stats"], faces))
    missions_html = '<ol class="missions" style="list-style:none">%s</ol>' % "".join(items)
    htabs = [("missions", "Missions", len(missions), missions_html)] + [
        (k, label, len(heroes) if k == "heroes" else sum(1 for x in home_tabs[k] if not x.startswith(("<h2", "<details"))),
         "\n".join(home_tabs[k]) or '<p class="empty-note">Nothing here.</p>')
        for k, label in HOME_TABS if k != "missions"]
    home = ('<section class="view" data-slug="home">\n<header><p class="eyebrow">Chronicles of the Second War</p>'
            '<h1>%s</h1><p class="intro">Every string of the campaign sorted by where it appears: the missions with their '
            'dialogue, quests and on-screen texts, and the campaign\'s heroes, units, structures, items and objects. '
            '%d strings in total.</p>\n<div class="bar"><div class="tools"><button class="btn ids-toggle" type="button" '
            'aria-pressed="false">Show IDs</button></div></div></header>\n<div class="home-tabs">%s</div>\n</section>' % (
                esc(camp_name), total, tabbar("home", htabs)))
    views = list(hero_views)
    for i, m in enumerate(missions):
        prev = missions[i - 1] if i else None
        nxt = missions[i + 1] if i + 1 < len(missions) else None
        chips = ""
        views.append(
            '<section class="view" data-slug="%s" hidden>\n<nav class="crumbs"><a href="#">&lsaquo; All missions</a>'
            '<span class="steps">%s%s</span></nav>\n<header><p class="eyebrow">%s</p><h1>%s</h1><p class="meta">%s</p>\n'
            '<div class="bar">%s<div class="tools"><button class="btn ids-toggle" type="button" aria-pressed="false">'
            'Show IDs</button></div></div></header>\n%s\n</section>' % (
                m["slug"], ('<a href="#%s">&lsaquo; %s</a>' % (prev["slug"], esc(prev["label"]))) if prev else "",
                ('<a href="#%s">%s &rsaquo;</a>' % (nxt["slug"], esc(nxt["label"]))) if nxt else "",
                esc(m["label"]), esc(m["title"]), m["stats"], chips, tabbar(m["slug"], m["tabs"])))
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>%s Texts</title>
<meta name="description" content="%s: every string of the campaign sorted for translators.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Alegreya:ital,wght@0,400;0,600;1,400&family=Alegreya+SC:wght@500;700&family=Alegreya+Sans:wght@500;600&display=swap" rel="stylesheet">
<style>%s
%s
%s
%s</style>
</head>
<body>
<div class="wrap">
<nav class="crumbs" style="margin-bottom:0"><a href="../">&lsaquo; All campaigns</a></nav>
%s
%s
<footer><p>Rise of the Alliance by LoreCraft Designs. Warcraft characters and icons &copy; Blizzard Entertainment.</p></footer>
</div>
<script>%s</script>
</body>
</html>
""" % (esc(camp_name), esc(camp_name), CSS.strip(), CAMPAIGN_CSS.strip(), SITE_CSS.strip(), icon_css, home,
       "\n".join(views), SITE_JS.strip())


def main():
    dl = os.path.join("F:\\", "\u0417\u0410\u0413\u0420\u0423\u0417\u041a\u0418")
    ap = argparse.ArgumentParser()
    ap.add_argument("--w3n", default=os.path.join(dl, "CSW_Rise_of_the_Alliance.w3n"))
    ap.add_argument("--game", default=r"F:\Games\Warcraft III")
    ap.add_argument("--icons", choices=("de", "hd"), default="hd")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CSW-RoA", "site",
                                                  "index.html"))
    ap.add_argument("--data", default=None)
    ap.add_argument("--enemy-races", default="orc",
                    help="races the player only fights (RoA: orc): their units show the name only")
    a = ap.parse_args()
    camp_name, titles, missions, tabs, counts, data = build(a.w3n, a.game, a.icons,
                                                               tuple(x for x in a.enemy_races.lower().split(",") if x))
    total = len(data["campaign_strings"]) + sum(len(m["strings"]) for m in data["maps"])
    doc = document(camp_name, missions, tabs, counts, total)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8", newline="\n").write(doc)
    dat = a.data or os.path.splitext(a.out)[0] + ".json"
    for h in tabs["heroes"]["used"] + tabs["heroes"]["unused"]:   # short links: <site>/roa/heroes/<slug>/ -> #heroes/<slug>
        d = os.path.join(os.path.dirname(os.path.abspath(a.out)), "heroes", h["slug"])
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "index.html"), "w", encoding="utf-8", newline="\n").write(
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>%s</title>'
            '<meta http-equiv="refresh" content="0;url=../../#heroes/%s"></head><body style="background:#0f1722">'
            '<script>location.replace("../../#heroes/%s")</script></body></html>' % (esc(h["name"]), h["slug"], h["slug"]))
    json.dump(data, open(dat, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote %s (%d KB) and %s" % (os.path.normpath(a.out), len(doc.encode()) // 1024, os.path.normpath(dat)))
    print("campaign strings:", dict(counts), "| home tabs:", {k: (len(v["used"]), len(v["unused"])) for k, v in tabs.items()})
    for m, d in zip(missions, data["maps"]):
        print("  %-14s %-26s %s" % (d["map"], d["title"][:26], d["counts"]))


if __name__ == "__main__":
    main()
