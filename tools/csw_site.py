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
            parts.append('<div class="phase"><h4 class="phase-head%s">%s%s<span class="idc">%s</span></h4>%s</div>' % (
                " ph-text" if heading == "Text" else "", sic, head_html, esc(sub), "".join(out)))
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
    # Skin files first, then the *Func files: standard items keep their Art only in ItemFunc.txt, some abilities and
    # upgrades only in <Race>AbilityFunc / <Race>UpgradeFunc
    funcs = ["ItemFunc"] + ["%s%sFunc" % (r, k) for k in ("Ability", "Upgrade")
                            for r in ("Human", "Orc", "NightElf", "Undead", "Neutral", "Campaign", "Common", "Item")]
    for f in ["AbilitySkin", "ItemSkin", "DestructableSkin", "UpgradeSkin"] + funcs:
        try:
            txt = icons.casc.get("War3.w3mod:Units/%s.txt" % f).decode("utf-8", "replace")
        except Exception:
            continue
        for m in re.finditer(r"^\[(\w{4})\]\s*\n(.*?)(?=^\[|\Z)", txt, re.M | re.S):
            a = re.search(r"^(?:Art|Buffart)(?::hd)?=(.+)$", m.group(2), re.M)
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
    # speaker portraits: one speaker talks through several units (Derek Proudmoore: hero HDpc with BTNWC2Derek, and a
    # footman-looking nCDF); take the best icon over all maps: custom hero icon > custom icon > standard hero > standard
    best = {}
    for name in maps:
        mp_ = Map(camp, name + ".w3x")
        objs_ = merge_objects(*[read_w3u(src.read(f)) for src, f in (
            (camp, "war3campaign.w3u"), (camp, "war3campaignSkin.w3u"), (mp_.arc, "war3map.w3u"), (mp_.arc, "war3mapSkin.w3u"))
            if src.read(f)])
        ic_ = Icons(game, style, objs_, archives=(mp_.arc, camp, loose))
        for e in mp_.story() + [e for q in mp_.quests() for _, _, evs in mp_.quest_steps(q) for e in evs]:
            if e[0] != "line" or not e[4]:
                continue
            nm = mp_.subtitle(e[3])[0] or "?"
            im, note = ic_.lookup(e[4])
            if im is None:
                continue
            score = (2 if note == "custom" else 0) + (1 if e[4][0].isupper() else 0)
            if score > best.get(nm, (-1,))[0]:
                best[nm] = (score, im, e[4])
    for nm, (_, im, unit) in best.items():
        if nm not in registry:
            registry[nm] = {"slug": "s%d" % len(registry), "cls": name_class(unit, "human"), "icon": webp(im)}

    seeds, map_objs, here_by_map, direct_by_map, map_arcs = set(), [], {}, {}, []
    for name in maps:
        mp = Map(camp, name + ".w3x")
        map_arcs.append(mp.arc)
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
    # campaign objects may use icons imported into a map only: search the maps too
    ent = Entities(cobj, creg, icon_for(cobj, (camp, loose) + tuple(map_arcs)), base_name, "campaign")
    hero_list, enemy_oids, enemy_cards, enemy_struct, enemy_struct_cards, dummy_oids = [], [], [], [], [], []

    def side_section(kind, title, cards):
        return ('<section class="faction %s"><h2 class="faction-head">%s</h2>%s</section>'
                % (kind, esc(title), "\n".join(cards)))

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
        sides = {"alliance": [], "horde": [], "dwarf": []}
        for name in sorted(units, key=str.lower):
            if keep is is_used and HIDDEN_NAME.search(name):   # "Dummy", "Dummy Bomb Detonator": never seen
                dummy_oids.extend(units[name])
                continue
            enemy = [o for o in units[name] if keep is is_used and is_enemy(o)]
            if enemy:                                   # the player only ever reads an enemy unit's name
                sides["horde"] += name_only(enemy)
                enemy_oids.extend(enemy)
            rest = [o for o in units[name] if o not in enemy]
            for side in ("dwarf", "alliance"):          # dwarven units: ids made as d??? (dfu1, duf0, dIrS)
                part = [o for o in rest if (o[0] == "d") == (side == "dwarf")]
                if part:
                    sides[side] += variants(part, "units")
        if keep is is_used:
            tabs["units"] = [side_section(k, t, sides[k]) for k, t in (
                ("alliance", "Alliance units"), ("horde", "Horde units"), ("dwarf", "Dwarven units")) if sides[k]]
        else:
            tabs["units"] = sides["alliance"] + sides["horde"] + sides["dwarf"]
        dwarf_cards = []
        for cat in sorted(groups, key=lambda c: (c == "Other", c.lower())):
            head = '<h2 class="act-head">%s</h2>' % esc(cat)
            tabs["structures"].append(head)
            for nm, oids in groups[cat].items():            # one card per name; copies only claim their strings
                dw = [o for o in oids if keep is is_used and o[0] == "d"]
                if dw:                                      # dwarven buildings (dBA1, dTh1 ...): own section below
                    for dup in dw[1:]:
                        ent.unit_card(dup, "structures", abil_lists(cobj))
                    dwarf_cards.append(ent.unit_card(dw[0], "structures", abil_lists(cobj), also=dw[1:]))
                    oids = [o for o in oids if o not in dw]
                    if not oids:
                        continue
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
            if tabs["structures"][-1] == head:              # a category emptied by the dwarven split
                tabs["structures"].pop()
        if dwarf_cards:
            tabs["structures"].append(side_section("dwarf", "Dwarven structures", dwarf_cards))
        if keep is is_used and (enemy_oids or enemy_struct or dummy_oids):   # never seen: folded away
            creg.override = "unused"
            enemy_cards.extend(ent.unit_card(o, "units", abil_lists(cobj), badge="Dummy unit (never seen)")
                               for o in dummy_oids)
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
        races = [str(cobj.get("unit", o, "urac", None) or unitdata.get(cobj.objects["unit"][o]["base"], {}).get("race", ""))
                 .lower() for o in h["oids"]]
        h["faction"] = "horde" if any(r in ("orc", "undead") for r in races) else "alliance"
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


NARR = {}                                          # css class of the narration icon


# ------------------------------------------------------------------------------------------------- voice lines
AUDIO = {"out": None, "bars": 32}                   # out: site folder; audio goes to <out>/audio/<map>/<sound>.mp3


def voice_files(mp, camp, name):
    """Dialogue sounds of one map, re-encoded small (MP3 mono 48 kbit/s) with a precomputed waveform.
    -> {sound name without gg_snd_: (url, seconds, [bar heights 0-100])}. Files already made are reused."""
    import subprocess, array
    if not AUDIO["out"]:
        return {}
    cs = dict(re.findall(r'set (gg_snd_\w+)\s*=\s*CreateSound\("([^"]+)"', "\n".join(mp.jass.lines)))
    listing = {}
    for arc in (mp.arc, camp):
        for f in (arc.read("(listfile)") or b"").decode("utf-8", "replace").split():
            listing.setdefault(f.replace("/", "\\").lower(), (arc, f))
    out_dir = os.path.join(AUDIO["out"], "audio", name.lower())
    os.makedirs(out_dir, exist_ok=True)
    res = {}
    for snd in mp.sound_item:
        path = cs.get(snd)
        if not path:
            continue
        want = path.replace("/", "\\").lower()
        hit = listing.get(want) or next((v for k, v in listing.items() if k.endswith("\\" + want)), None)
        if not hit:
            continue
        data = hit[0].read(hit[1])
        if not data:
            continue
        key = snd[7:]
        mp3 = os.path.join(out_dir, key + ".mp3")
        if not os.path.exists(mp3):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "pipe:0", "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame",
                            "-b:a", "48k", mp3], input=data, check=True)
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", mp3, "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
                             capture_output=True, check=True).stdout
        a = array.array("h", pcm[:len(pcm) // 2 * 2])
        n = AUDIO["bars"]
        step = max(1, len(a) // n)
        peaks = [max((abs(x) for x in a[i * step:(i + 1) * step]), default=0) for i in range(n)]
        top = max(peaks) or 1
        bars = [max(12, round(100 * (v / top) ** 0.7)) for v in peaks]
        res[key] = ("audio/%s/%s.mp3" % (name.lower(), key), len(a) / 8000.0, bars)
    return res


def add_voice(html_, voices):
    """Put a mini player (play/pause + time) next to the speaker name of every dialogue line with a known sound."""
    def player(m):
        v = voices.get(m.group(5))
        if not v:
            return m.group(0)
        url, sec, _bars = v
        mini = ('<span class="vo" data-src="%s" data-len="%d:%02d"><button class="vo-play" type="button" '
                'aria-label="Play line"></button><span class="vo-t">%d:%02d</span></span>'
                % (url, int(sec) // 60, round(sec) % 60, int(sec) // 60, round(sec) % 60))
        return m.group(1) + m.group(2) + mini + m.group(3) + m.group(4) + '<div class="ids">' + m.group(5) + " · "
    return re.sub(r'(<div class="name[^"]*">)(.*?)(</div>)((?:(?!<div class="line").)*?)<div class="ids">(\w+) · ',
                  player, html_, flags=re.S)


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
            sid = re.search(r'"TRIGSTR_(\d+)"', j.lines[e[1] - 1])
            reg.claim(int(sid.group(1)) if sid else None, "fragments", "caption")
            if sid:                                    # narration: a line of its own, so its translation shows too
                if "narr" not in NARR:
                    im = icons.image("ReplaceableTextures\\CommandButtons\\BTNSelectHeroOn")   # Blizzard's red "?"
                    NARR["narr"] = icon_class(webp(im, 64)) if im is not None else ""
                frag_html.append(
                    '<section class="scene cin narr"><div class="lines"><div class="line" data-s="narr">'
                    '<span class="icon %s" aria-hidden="true"></span><div><div class="name narr-name">Narration</div>'
                    '<p class="text">%s</p><div class="ids">caption · string %s</div></div></div></div></section>'
                    % (NARR["narr"], wc3_html(e[3]), sid.group(1)))
            else:
                frag_html.append(caption_html(e[3]))
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
    voices = voice_files(mp, camp, name)
    tabs = [(k, lbl, n_, add_voice(h_, voices)) for k, lbl, n_, h_ in tabs]
    return {"slug": name.lower(), "label": small or name, "title": big or name, "cast": cast, "tabs": tabs,
            "stats": "%d strings · %d lines · %d quests" % (len(mp.S), lines_total, len(mp.quests())),
            "data": {"map": name, "label": small, "title": big, "strings": {
                str(i): dict({"text": mp.S[i], "place": reg.place.get(i, ("other", ""))[0],
                              "detail": reg.place.get(i, ("", ""))[1]}, **reg.info(i)) for i in sorted(mp.S)},
                "counts": dict(c)}}


# --------------------------------------------------------------------------------------------------- the page
SITE_CSS = """
.home-tabs{margin-top:26px}
.lang-switch{position:fixed;right:16px;bottom:16px;z-index:50;display:flex;flex-wrap:wrap;align-items:center;gap:4px;
max-width:calc(100vw - 32px);padding:6px 8px;border:1px solid #6b5a2e;border-radius:8px;background:rgba(20,18,15,.96);
box-shadow:0 6px 20px rgba(0,0,0,.5)}
.ls-label{font:600 11px/1 "Alegreya Sans",system-ui,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:#a8987a;
margin-right:4px}
.ls-btn{min-width:34px;padding:5px 7px;border:1px solid #4a3f26;border-radius:5px;background:#1d1a14;color:#cdb98a;
font:700 12px/1 "Alegreya Sans",system-ui,sans-serif;cursor:pointer}
.ls-btn:hover{border-color:#c9a447}
.ls-btn[aria-pressed="true"]{border-color:#f0c65a;background:#3a2f17;color:#ffe7a3}
.ls-btn:focus-visible{outline:2px solid #f0c65a;outline-offset:2px}
.tr-line{margin:4px 0 0;padding:4px 0 2px 10px;border-left:2px solid #c9a447;color:#e9dcb6;font-size:.95em}
.tr-line .tl{display:inline-block;margin-right:6px;font:700 10px/1 "Alegreya Sans",system-ui,sans-serif;letter-spacing:.08em;
color:#c9a447;vertical-align:1px}
.tr-name{color:#c9a447;font-weight:600;margin-left:6px}
.tr-name::before{content:"· "}
.line .tr-row{grid-column:1/-1;display:grid;grid-template-columns:56px minmax(0,1fr);gap:14px;margin-top:10px;padding-top:10px;
border-top:1px dashed rgba(201,164,71,.25)}
.lang-badge{display:grid;place-items:center;width:56px;height:56px;border-radius:5px;background:#000;
box-shadow:0 0 0 1px #000,0 0 0 2px #4a3f26}
.lang-badge{grid-template-rows:auto auto;align-content:center;gap:4px;overflow:hidden;position:relative}
.lang-badge::before{content:"";position:absolute;inset:-32px 0 0 -32px;background:0 0/32px 32px repeat;
animation:lbcrest 9s linear infinite;pointer-events:none}
.lang-badge b,.lang-badge i{position:relative}
@keyframes lbcrest{to{transform:translate(32px,32px)}}
.lang-badge b,.lang-badge i{display:block;text-align:center;color:transparent;-webkit-background-clip:text;background-clip:text;
background-image:linear-gradient(90deg,#ddd,#999);background-size:200% 100%;animation:lbmove 7s linear infinite}
.lang-badge b{font:800 24px/1 "Open Sans","Segoe UI",Arial,sans-serif;letter-spacing:-.5px}
.lang-badge i{font:700 8px/1.1 "Open Sans","Noto Sans","Malgun Gothic","Yu Gothic","Segoe UI",Tahoma,sans-serif;
font-style:normal;letter-spacing:-.1px;white-space:nowrap;max-width:52px}
@keyframes lbmove{to{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){.lang-badge b,.lang-badge i,.lang-badge::before{animation:none}}
.tr-who{color:#e6bd55}
.narr .lines{border-style:dashed}
.narr-name{color:#a8987a;font-style:italic}
.narr .text{font-style:italic;color:#d8cfb8}
.tr-text{color:#efe6cf}
.ls-layout{margin-left:6px;padding:5px 9px}
@media (min-width:701px){
body.tr-cols .wrap{max-width:1240px}
body.tr-cols .line:has(.tr-row){grid-template-columns:56px minmax(0,1fr) 4px 56px minmax(0,1fr);column-gap:14px}
body.tr-cols .line .tr-row{grid-column:4/6;grid-row:1;grid-template-columns:56px minmax(0,1fr);gap:14px;margin:0;padding:0;
border-top:0;position:relative}
body.tr-cols .line .tr-row::before{content:"";position:absolute;left:-16px;top:0;bottom:0;border-left:1px dashed rgba(201,164,71,.35)}
body.tr-cols .line .tr-row .tr-who{line-height:28px;margin:2px 0 3px}
body.tr-cols .narr .line .tr-row .tr-who{line-height:inherit}
body.tr-cols .line .tr-row .tr-text{margin:0}
body.tr-cols .step:has(> .tr-step){grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:32px;row-gap:6px}
body.tr-cols .step:has(> .tr-step) > .step-label{grid-column:1/-1;grid-row:1;text-align:center;padding:0}
body.tr-cols .step:has(> .tr-step) > div:not(.step-label):not(.tr-step){grid-column:1;grid-row:2}
body.tr-cols .step > .tr-step{grid-column:2;grid-row:2;margin:0;padding:0;border-top:0;position:relative}
body.tr-cols .step > .tr-step::before{content:"";position:absolute;left:-16px;top:0;bottom:0;border-left:1px dashed rgba(201,164,71,.35)}
body.tr-cols .quest-head:has(> .tr-head){grid-template-columns:1fr;justify-items:center;text-align:center;row-gap:8px}
body.tr-cols .quest-head:has(> .tr-head) > div:not(.tr-head){display:contents}
body.tr-cols .quest-head:has(> .tr-head) .badge{order:1}
body.tr-cols .quest-head:has(> .tr-head) > .icon{order:2}
body.tr-cols .quest-head:has(> .tr-head) h3{order:3;margin:0}
body.tr-cols .quest-head:has(> .tr-head) > div > :not(.badge):not(h3){order:4}}
/* mission quest cards always read top-down: centred head, the row label above its text (no wide label column) */
:is([id$="-quests"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .quest-head{grid-template-columns:1fr;justify-items:center;text-align:center;row-gap:8px}
:is([id$="-quests"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .quest-head > div:not(.tr-head){display:contents}
:is([id$="-quests"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .quest-head .badge{order:1}
:is([id$="-quests"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .quest-head > .icon{order:2}
:is([id$="-quests"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .quest-head h3{order:3;margin:0}
:is([id$="-quests"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .quest-head > div > :not(.badge):not(h3){order:4}
:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step{grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:32px;row-gap:6px}
:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step > .step-label{grid-column:1/-1;grid-row:1;text-align:center;padding:0}
:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step:not(:has(> .tr-step)) > div:not(.step-label){grid-column:1/-1}
@media (min-width:701px){            /* with a translation: original | translation side by side at any page width */
:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step:has(> .tr-step) > div:not(.step-label):not(.tr-step){grid-column:1;grid-row:2}
:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step > .tr-step{grid-column:2;grid-row:2;margin:0;padding:0;border-top:0;position:relative}
:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step > .tr-step::before{content:"";position:absolute;left:-16px;top:0;bottom:0;
border-left:1px dashed rgba(201,164,71,.35)}}
@media (max-width:700px){:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step > div:not(.step-label):not(.tr-step),
:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step > .tr-step{grid-column:1/-1;grid-row:auto}}
/* hero list: compact cards, two per row (name, icon, rank only) */
#panel-home-heroes .missions,.unit-list{grid-template-columns:repeat(2,minmax(0,1fr))}
.unit-list .hero-row{grid-template-columns:56px minmax(0,1fr);padding:12px 14px}
.unit-list .hero-row .icon{width:56px;height:56px}
.unit-list .hero-row .mission-title{font-size:21px}
.unit-list .hero-row .hero-classes{color:var(--muted);font-size:14px}
@media (max-width:640px){.unit-list{grid-template-columns:minmax(0,1fr)}}
#panel-home-heroes .hero-row .hero-where,#panel-home-heroes .hero-row .hero-meta{display:none}
#panel-home-heroes .hero-row{grid-template-columns:56px minmax(0,1fr);padding:12px 14px}
#panel-home-heroes .hero-row .icon{width:56px;height:56px}
#panel-home-heroes .hero-row .mission-title{font-size:21px}
@media (max-width:640px){#panel-home-heroes .missions{grid-template-columns:minmax(0,1fr)}}
/* language tags in the label row: EN over the original, the language code over the translation */
.step > .tr-tag{grid-row:1;align-self:center;justify-self:center;display:none}
.step > .tr-tag{padding:5px 9px;border:1px solid #6b5a2e;border-radius:5px;background:#000;box-shadow:inset 0 1px 0 rgba(255,220,140,.08)}
.step > .tr-tag.tr-en{grid-column:1}
.step > .tr-tag:not(.tr-en){grid-column:2}
.tr-tag b{display:block;font:800 13px/1 "Open Sans","Segoe UI",Arial,sans-serif;letter-spacing:.04em;color:transparent;
-webkit-background-clip:text;background-clip:text;background-size:200% 100%;animation:lbmove 7s linear infinite}
.tr-en b{background-image:linear-gradient(90deg,#f4f6f8,#d9dee5,#bec6d1,#a4afbe,#8b98aa,#a4afbe,#bec6d1,#d9dee5,#f4f6f8)}
@media (min-width:701px){:is([id$="-quests"],[data-slug^="heroes/"],[data-slug^="units/"],[data-slug^="structures/"]) .quest .step > .tr-tag{display:block}}
@media (prefers-reduced-motion:reduce){.tr-tag b{animation:none}}
/* quest cards: the section word "Text" says nothing */
.quest .ph-text{display:none}
/* quest rows: the translation under the English, in the text column */
.step > .tr-step{grid-column:2;margin-top:6px;padding-top:6px;border-top:1px dashed rgba(201,164,71,.25)}
.quest-head > .tr-head{display:none}
.tr-head h3{color:#efe6cf}
.tr-step .tr-text{margin:0;color:#efe6cf}
/* game icons carry their own bevel frame: zoom past it so only the page frame shows */
.icon:not(.mono),.lang-badge{background-size:114% 114%;background-position:center}
@media (max-width:560px){.line .tr-row,.step > .tr-step{grid-template-columns:44px minmax(0,1fr);gap:12px}.lang-badge{width:44px;height:44px}
.lang-badge b{font-size:16px}.lang-badge i{font-size:6px}}
@media (max-width:560px){.lang-switch{right:8px;bottom:8px}}
.vo{display:inline-flex;align-items:center;gap:6px;margin-left:10px;padding:2px 8px 2px 2px;vertical-align:middle;
border:1px solid #6b5a2e;border-radius:6px;background:linear-gradient(180deg,#1d1a14,#141210)}
.vo-play{flex:0 0 auto;width:22px;height:22px;border:1px solid #c9a447;border-radius:4px;cursor:pointer;position:relative;
background:linear-gradient(180deg,#3a2f17,#211a0d)}
.vo-play:hover{border-color:#f0cf6a}
.vo-play::before{content:"";position:absolute;left:7px;top:5px;border-left:8px solid #f0c65a;border-top:5px solid transparent;
border-bottom:5px solid transparent}
.vo.playing{border-color:#c9a447}
.vo.playing .vo-play::before{left:6px;top:5px;width:3px;height:10px;border:0;background:#f0c65a;box-shadow:5px 0 0 #f0c65a}
.vo-play:focus-visible{outline:2px solid #f0c65a;outline-offset:2px}
.vo-t{font:600 12px/1 "Alegreya Sans",system-ui,sans-serif;color:#cdb98a;min-width:26px}
.tabs{flex-wrap:wrap}.tab{padding:10px 8px;letter-spacing:.03em}
@media (max-width:700px){.tabs{flex-wrap:wrap;gap:6px;border-bottom:0;margin-bottom:14px}.tab{padding:7px 12px;margin:0;border:1px solid var(--line);border-radius:999px;font-size:15px;white-space:nowrap}.tab[aria-selected="true"]{border-color:var(--gold);background:rgba(255,210,120,.08)}}
.wrap{overflow-x:clip}.cast-filter{min-width:0}
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
.faction{position:relative;overflow:hidden;margin:22px 0 0;padding:20px 18px 18px;border:1px solid var(--fc);border-radius:12px;
background:var(--fbg)}
.faction::before{content:"";position:absolute;inset:-160px 0 0 -160px;background:url(data:image/webp;base64,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) 0 0/160px 160px repeat;
animation:crest 30s linear infinite;pointer-events:none;will-change:transform}
.faction.horde::before{background-image:url(data:image/webp;base64,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)}
.faction > *{position:relative}
.faction.alliance{--fc:#3f7be8;--fbg:#0f1b33}
.faction.horde{--fc:#d8453b;--fbg:#2a1012}
.faction.dwarf{--fc:#d9a838;--fbg:#2a210b}
.faction.dwarf::before{background-image:url(data:image/webp;base64,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)}
.faction .quest{border-color:var(--fc);background:rgba(18,20,26,.94)}
.faction .quest:first-of-type{margin-top:0}
.faction-head{display:block;text-align:center;margin:0 0 16px;font:700 22px/1.2 "Alegreya SC",Georgia,serif;letter-spacing:.14em;text-transform:uppercase;color:#fff;text-shadow:0 2px 8px #000,0 0 2px #000}
.faction .hero-row{border-color:var(--fc);background:rgba(10,12,18,.93)}
.faction .hero-row .icon{box-shadow:0 0 0 1px #000,0 0 0 2px var(--fc)}
.faction[hidden]{display:none}
@keyframes crest{to{transform:translate(160px,160px)}}
@media (prefers-reduced-motion:reduce){.faction::before{animation:none}}
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
      r.hidden=!!m&&(' '+r.dataset.m+' ').indexOf(' '+m+' ')<0});
    p.querySelectorAll('.faction').forEach(function(f){f.hidden=!f.querySelector('.hero-row:not([hidden])')})})});
  var au=new Audio(),cur=null;au.preload='none';
  function fmt(t){t=Math.max(0,Math.round(t));return Math.floor(t/60)+':'+('0'+t%60).slice(-2)}
  function paint(){if(!cur)return;var d=au.duration||1,p=au.currentTime/d,bars=cur.querySelectorAll('.vo-wave i'),k=Math.round(p*bars.length);
    for(var i=0;i<bars.length;i++)bars[i].classList.toggle('on',i<k);cur.querySelector('.vo-t').textContent=fmt(au.currentTime)}
  function stop(){if(cur){cur.classList.remove('playing');cur.querySelector('.vo-play').setAttribute('aria-label','Play line');if(au.ended||cur!==null&&au.paused&&au.currentTime===0)cur.querySelector('.vo-t').textContent=cur.dataset.len}}
  au.addEventListener('timeupdate',paint);
  au.addEventListener('ended',function(){paint();stop();if(cur)cur.querySelector('.vo-t').textContent=fmt(au.duration);cur=null});
  function start(v,at){if(cur!==v){stop();if(cur)cur.querySelector('.vo-t').textContent=cur.dataset.len;if(cur)cur.querySelectorAll('.vo-wave i').forEach(function(b){b.classList.remove('on')});
      cur=v;au.src=v.dataset.src}
    if(at!=null){var go=function(){au.currentTime=at*(au.duration||0)};if(au.readyState>0)go();else au.addEventListener('loadedmetadata',go,{once:true})}
    au.play();v.classList.add('playing');v.querySelector('.vo-play').setAttribute('aria-label','Pause line')}
  document.addEventListener('click',function(e){var b=e.target.closest('.vo-play'),w=e.target.closest('.vo-wave');
    if(b){var v=b.closest('.vo');if(cur===v&&!au.paused){au.pause();stop()}else start(v)}
    else if(w){var r=w.getBoundingClientRect();start(w.closest('.vo'),(e.clientX-r.left)/r.width)}});
  var lsBtns=document.querySelectorAll('.ls-btn'),lsCache={},lsOn=null;
  function wc(t){var e=document.createElement('div');e.textContent=t;var h=e.innerHTML.replace(/\\|n|\\r?\\n/g,'<br>');
    h=h.replace(/\|c[0-9a-fA-F]{2}([0-9a-fA-F]{6})(.*?)(\|r|$)/g,function(m,c,x){
      var r=parseInt(c.slice(0,2),16),g=parseInt(c.slice(2,4),16),b=parseInt(c.slice(4,6),16),mx=Math.max(r,g,b),mn=Math.min(r,g,b),k='';
      if(mx-mn<40)k=' m';else if(g>r&&g>=b)k=' g';else if(r>g*1.6&&r>b)k=' r';   /* the page's own colour classes, as in English */
      return '<span class="wc'+k+'">'+x+'</span>'});
    return h.replace(/\|r/g,'')}
  function lsClear(){document.querySelectorAll('.tr-line,.tr-name,.tr-row,.tr-step,.tr-head,.tr-tag').forEach(function(n){n.remove()})}
  function esc(t){var e=document.createElement('div');e.textContent=t;return e.innerHTML}
  function lsApply(code,d){lsClear();
    document.querySelectorAll('.ids').forEach(function(ids){var m=/strings? (\d+)/.exec(ids.textContent);if(!m)return;
      var v=ids.closest('.view'),slug=v&&v.dataset.slug||'',part=d[slug]||d.campaign||{};if(!d[slug])part=d.campaign||{};
      var t=part[m[1]];if(!t)return;var line=ids.closest('.line');
      if(line){var nb=line.querySelector('.name'),en=nb?(nb.childNodes[0]&&nb.childNodes[0].nodeType===3?nb.childNodes[0].textContent:nb.textContent).trim():'';
        var who=line.dataset.s==='narr'?' ':(d.names&&d.names[en])||en,row=document.createElement('div');row.className='tr-row';row.lang=code.toLowerCase();
        row.innerHTML='<span class="lang-badge lb-'+code+'" title="'+code+'"><b>'+code+'</b><i></i></span><div><div class="name tr-who">'+esc(who)+
          '</div><p class="text tr-text">'+wc(t)+'</p></div>';line.appendChild(row);return}
      var step=ids.closest('.step');
      if(step&&step.querySelector(':scope > div > .msg')){var tr=document.createElement('div');tr.className='tr-step';tr.lang=code.toLowerCase();
        tr.innerHTML='<p class="msg tr-text">'+wc(t)+'</p>';
        step.appendChild(tr);
        step.insertAdjacentHTML('beforeend','<span class="tr-tag tr-en"><b>EN</b></span><span class="tr-tag lb-'+code+'"><b>'+code+'</b></span>');return}
      var box=ids.parentNode,txt=box.querySelector('.msg,.text')||box.querySelector('h3');if(!txt)return;
      var p=document.createElement('p');p.className='tr-line';p.lang=code.toLowerCase();
      p.innerHTML='<span class="tl">'+code+'</span>'+wc(t);txt.after(p)});
    document.querySelectorAll('.quest').forEach(function(q){var st=[].filter.call(q.querySelectorAll('.step'),function(x){
      var l=x.querySelector('.step-label');return l&&l.textContent.trim().toLowerCase()==='title'})[0],tt=st&&st.querySelector('.tr-step .tr-text');
      var head=q.querySelector('.quest-head');if(!tt||!head)return;var hd=document.createElement('div');hd.className='tr-head';
      hd.innerHTML='<h3>'+tt.innerHTML+'</h3>';head.appendChild(hd)});
    document.querySelectorAll('.chip').forEach(function(n){var nm=(n.childNodes[0]&&n.childNodes[0].nodeType===3?
      n.childNodes[0].textContent:n.textContent).trim();if(n.classList.contains('chip'))nm=nm.replace(/\s*\d+$/,'');
      var t=d.names&&d.names[nm];if(!t||t===nm)return;var s=document.createElement('span');s.className='tr-name';s.textContent=t;
      var vo=n.querySelector('.vo,small');if(vo)n.insertBefore(s,vo);else n.appendChild(s)})}
  function lsSet(code){lsOn=code;lsBtns.forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.lang===code))});
    try{localStorage.setItem('roa-lang',code||'')}catch(e){}
    if(!code){lsClear();return}
    if(lsCache[code]){lsApply(code,lsCache[code]);return}
    fetch('l10n/'+code+'.json').then(function(r){return r.json()}).then(function(d){lsCache[code]=d;if(lsOn===code)lsApply(code,d)})}
  lsBtns=[].filter.call(lsBtns,function(b){return b.dataset.lang});
  lsBtns.forEach(function(b){b.addEventListener('click',function(){lsSet(lsOn===b.dataset.lang?null:b.dataset.lang)})});
  var lay=document.querySelector('.ls-layout');
  function setCols(on){body.classList.toggle('tr-cols',on);if(lay)lay.setAttribute('aria-pressed',String(on));
    try{localStorage.setItem('roa-cols',on?'1':'')}catch(e){}}
  if(lay)lay.addEventListener('click',function(){setCols(!body.classList.contains('tr-cols'))});
  try{if(localStorage.getItem('roa-cols'))setCols(true)}catch(e){}
  try{var saved=localStorage.getItem('roa-lang');if(saved&&document.querySelector('.ls-btn[data-lang="'+saved+'"]'))lsSet(saved)}catch(e){}
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
            sides = [("alliance", "Alliance heroes"), ("horde", "Horde heroes")]
            used = [bar] + ['<section class="faction %s"><h2 class="faction-head">%s</h2><div class="missions hero-list">%s'
                            '</div></section>' % (k, esc(t), "".join(hero_row(h, labels) for h in used
                                                                  if h.get("faction", "alliance") == k))
                            for k, t in sides if any(h.get("faction", "alliance") == k for h in used)] if used else []
            unused = ['<div class="missions">%s</div>' % "".join(hero_row(h) for h in unused)] if unused else []
        shown[k] = used
        n = len(unused_heroes) if k == "heroes" else sum(1 for x in unused if not x.startswith("<h2"))
        if n:
            shown[k] = used + ['<details class="appendix unused"><summary>Not used in any mission (%d): no map places, '
                               'sells, trains or scripts these%s, so the player never sees them. Translate last, if at '
                               'all.</summary>\n%s</details>' % (n, " (heroes seen only in cinematics or without abilities are here too)"
                                                                  if k == "heroes" else "", "\n".join(unused))]
    home_tabs = shown
    # units and structures: compact two-per-row links, each card on its own page (#units/<slug>, #structures/<slug>),
    # laid out like the quests (label above, original | translation)
    unit_views, used_slugs = [], set()
    card_counts = {k: "".join(x for x in v if not x.startswith("<details")).count('<article class="quest')
                   for k, v in home_tabs.items() if k != "heroes"}

    def card_list(html_, kind, back):
        def one(m):
            art = m.group(0)
            title = re.sub(r"<[^>]+>", "", re.search(r"<h3>(.*?)</h3>", art, re.S).group(1)).strip()
            bm = re.search(r'<span class="badge[^"]*">(.*?)</span>', art)
            badge = re.sub(r"<[^>]+>", "", bm.group(1)) if bm else ""
            im = re.search(r'<span class="icon[^"]*"[^>]*>.*?</span>', art)
            slug = re.sub(r"[^a-z0-9]+", "-", html.unescape(title).lower()).strip("-") or kind
            base, k = slug, 2
            while (kind, slug) in used_slugs:
                slug, k = "%s-%d" % (base, k), k + 1
            used_slugs.add((kind, slug))
            # page copy: the name moves from the head into a first "Name" row (original | translation, like a quest)
            hm = re.search(r'(<div class="quest-head">.*?)<div class="ids">([^<]*)</div>(</div></div>)', art, re.S)
            if hm and re.search(r"strings? \d", hm.group(2)):
                row_ = ('<div class="phase"><div class="step"><div class="step-label">Name</div><div><p class="msg">%s</p>'
                        '<div class="ids">%s</div></div></div></div>' % (title, hm.group(2)))
                art = art[:hm.start()] + hm.group(1) + '<div class="ids"></div>' + hm.group(3) + row_ + art[hm.end():]
            unit_views.append('<section class="view" data-slug="%s/%s" hidden>\n<nav class="crumbs"><a href="#home-%s">'
                              '&lsaquo; %s</a></nav>\n%s\n</section>' % (kind, slug, kind, back, art))
            return ('<a class="mission-link hero-row" href="#%s/%s">%s<span><span class="mission-title">%s</span>'
                    '<span class="hero-classes">%s</span></span></a>' % (kind, slug, im.group(0) if im else "", title,
                                                                         esc(html.unescape(badge))))

        def run(m):                                       # consecutive cards -> one grid of links
            return '<div class="missions unit-list">%s</div>' % re.sub(
                r'<article class="quest[^"]*">.*?</article>\s*', one, m.group(0), flags=re.S)
        return re.sub(r'(?:<article class="quest[^"]*">.*?</article>\s*)+', run, html_, flags=re.S)

    for k, back in (("units", "All units"), ("structures", "All structures")):
        keep = [x for x in home_tabs[k] if not x.startswith("<details")]
        rest = [x for x in home_tabs[k] if x.startswith("<details")]
        home_tabs[k] = [card_list("\n".join(keep), k, back)] + rest
    import colorsys
    speaker_css = []
    for i, p_ in enumerate(sorted(registry.values(), key=lambda x: int(x["slug"][1:]))):
        deg = (i * 137.508) % 360                         # golden angle: neighbours never get close hues
        if 25 <= deg <= 60:                               # skip the page's own gold, names must stand apart from it
            deg = (deg + 45) % 360
        hue = deg / 360.0
        light = (0.72, 0.64, 0.80)[i % 3]
        r_, g_, b_ = colorsys.hls_to_rgb(hue, light, 0.62)
        col = "#%02x%02x%02x" % (int(r_ * 255), int(g_ * 255), int(b_ * 255))
        speaker_css.append('.line[data-s="%s"] .name,.line[data-s="%s"] .tr-who,.chip[data-s="%s"]{color:%s}' % (
            p_["slug"], p_["slug"], p_["slug"], col))
    icon_css = "\n".join(speaker_css) + "\n" + "\n".join(".i-%s{background-image:url(data:image/webp;base64,%s)}" % (p["slug"], p["icon"])
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
        (k, label, len(heroes) if k == "heroes" else card_counts.get(k, 0),
         "\n".join(home_tabs[k]) or '<p class="empty-note">Nothing here.</p>')
        for k, label in HOME_TABS if k != "missions"]
    home = ('<section class="view" data-slug="home">\n<header><p class="eyebrow">Chronicles of the Second War</p>'
            '<h1>%s</h1><p class="intro">Every string of the campaign sorted by where it appears: the missions with their '
            'dialogue, quests and on-screen texts, and the campaign\'s heroes, units, structures, items and objects. '
            '%d strings in total.</p>\n<div class="bar"><div class="tools"><button class="btn ids-toggle" type="button" '
            'aria-pressed="false">Show IDs</button></div></div></header>\n<div class="home-tabs">%s</div>\n</section>' % (
                esc(camp_name), total, tabbar("home", htabs)))
    views = list(hero_views) + unit_views
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


# ------------------------------------------------------------------------------------------ extra languages
LANG_NAMES = {"UK": "Українська", "IT": "Italiano", "PL": "Polski", "DE": "Deutsch", "FR": "Français", "ES": "Español",
              "PT": "Português", "CZ": "Čeština", "KR": "한국어", "RU": "Русский", "CN": "中文"}


BADGE_CODE = {"UK": "uk", "IT": "it", "PL": "pl", "DE": "de", "FR": "fr", "ES": "es", "PT": "pt", "CZ": "cs", "KR": "ko",
              "RU": "ru", "CN": "zh"}


import base64


def lang_badge_css(codes):
    """.lb-<CODE>: the language badge gradient of archive/lang_indication/make_badges.py on the code letters."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "archive", "lang_indication"))
    try:
        import make_badges
    except ImportError:
        return ""
    out = []
    for c in codes:
        pal = make_badges.PALETTE.get(BADGE_CODE.get(c, c.lower()))
        if pal:
            stops = pal[2] + pal[2][-2::-1]          # there and back: the moving gradient loops without a seam
            name = pal[1]
            size = max(7.0, min(11.0, 50.0 / (len(name) * (0.95 if re.search(r"[\u1100-\uffff]", name) else 0.66))))
            # moving pattern behind the letters (like the crests of the faction sections): the code, small, in its colour
            tile = ("<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'><g fill='%s' opacity='.28' "
                    "font-family='Arial' font-weight='700' font-size='8' transform='rotate(-14 16 16)'>"
                    "<text x='1' y='11'>%s</text><text x='17' y='27'>%s</text></g></svg>" % (pal[2][2], c, c))
            uri = "data:image/svg+xml;base64," + base64.b64encode(tile.encode()).decode()
            out.append(".lb-%s b,.lb-%s i{background-image:linear-gradient(90deg,%s)}.lb-%s i::before{content:%s}"
                       ".lb-%s i{font-size:%.1fpx}.lb-%s::before{background-image:url(%s)}" % (
                           c, c, ",".join(stops), c, json.dumps(name, ensure_ascii=False), c, size, c, uri))
    return "".join(out)


def write_translations(camp_dir, site_dir, data):
    """site/l10n/<LANG>.json for every <Camp>/<LANG>/strings: {"names": {english speaker: tr}, "<map lower>": {sid: tr},
    "campaign": {sid: tr}}, matched by the hash of the English text (what tools/l10n.py builds with). -> [(code, n)]"""
    import glob, hashlib
    h = lambda t: hashlib.sha256(t.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:10]
    out_dir = os.path.join(site_dir, "l10n")
    os.makedirs(out_dir, exist_ok=True)
    langs = []
    for lang_dir in sorted(glob.glob(os.path.join(camp_dir, "*", "strings"))):
        code = os.path.basename(os.path.dirname(lang_dir))
        if code in ("site", "source") or not code.isupper():
            continue
        memo = {}
        for f in glob.glob(os.path.join(lang_dir, "**", "*.json"), recursive=True):
            try:
                entries = json.load(open(f, encoding="utf-8"))
            except ValueError:
                continue
            for e in entries.values():
                if isinstance(e, dict) and e.get("tr") and e.get("hash"):
                    memo.setdefault(e["hash"], e["tr"])
        res, names, n = {"campaign": {}}, {}, 0
        for sid, v in data["campaign_strings"].items():
            t = memo.get(h(v["text"]))
            if t:
                res["campaign"][sid] = t
                n += 1
        for m in data["maps"]:
            part = res.setdefault(m["map"].lower(), {})
            for sid, v in m["strings"].items():
                t = memo.get(h(v["text"]))
                if t:
                    part[sid] = t
                    n += 1
                    if v["detail"] == "speaker name":
                        names.setdefault(v["text"].strip(), t.strip())
        res["names"] = names
        if n:
            json.dump(res, open(os.path.join(out_dir, code + ".json"), "w", encoding="utf-8"), ensure_ascii=False,
                      separators=(",", ":"))
            langs.append((code, n))
    return langs


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
    AUDIO["out"] = os.path.dirname(os.path.abspath(a.out))
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
    langs = write_translations(os.path.dirname(os.path.dirname(os.path.abspath(a.out))),
                               os.path.dirname(os.path.abspath(a.out)), data)
    print("translations:", langs)
    page = open(a.out, encoding="utf-8").read()
    switch = ('<div class="lang-switch" role="group" aria-label="Show a translation next to the English">'
              '<span class="ls-label">Translation</span>%s<button type="button" class="ls-btn ls-layout" aria-pressed="false" '
              'title="Rows or two columns (English left, translation right)">⇆ 2 col</button></div>' % "".join(
                  '<button type="button" class="ls-btn" data-lang="%s" aria-pressed="false" title="%s · %d strings">%s'
                  '</button>' % (c, LANG_NAMES.get(c, c), n, c) for c, n in langs))
    badge_css = lang_badge_css([c for c, _ in langs])
    page = page.replace("</head>", "<style>%s</style>\n</head>" % badge_css, 1)
    open(a.out, "w", encoding="utf-8", newline="\n").write(page.replace("<script>", switch + "\n<script>", 1))
    print("wrote %s (%d KB) and %s" % (os.path.normpath(a.out), len(doc.encode()) // 1024, os.path.normpath(dat)))
    print("campaign strings:", dict(counts), "| home tabs:", {k: (len(v["used"]), len(v["unused"])) for k, v in tabs.items()})
    for m, d in zip(missions, data["maps"]):
        print("  %-14s %-26s %s" % (d["map"], d["title"][:26], d["counts"]))


if __name__ == "__main__":
    main()
