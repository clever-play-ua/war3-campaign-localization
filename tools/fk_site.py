"""Forsaken Kingdom: one-page campaign site — mission list, then per mission the dialogue in story order (scenes with
speaker icons) and a Quests tab with every text tied to each quest. Also writes a JSON with all ids (string ids,
recording labels, trigger names) as the base for a later translation editor (pick languages, edit, mark checked, export).

    python tools/fk_site.py --maps <dir with <Map>.files> --lua <dir with <Map>.lua>   (or set FK_WORK to their parent)
        [--campaign 000_The_Last_Days_of_Lordaeron] [--locale enUS] [--game "F:/Games/Warcraft III"] [--icons hd|de]
        [--fragment] [--out FK/site/<campaign>.html] [--data FK/site/<campaign>.json]

--maps: the decrypted maps (war3map.wtg for trigger categories, war3map.w3u for map-only units); --lua: the decompiled
scripts. Both live outside the repo (account-bound data).

Dialogue: conversation.json lines. Story order: HumanRE01 uses fk_clean.ORDER (checked against its script); the other maps
sort transmissions by their recording number (T010, T020, ... are written in story order) and put each cinematic where
its script starts it (ANCHORS, worked out from which trigger queues the cinematic and which transmissions it queues around
it). Lines whose trigger is marked "NOT USED" go to an appendix.
Quests: CreateQuestBJ in the Lua script; a quest's triggers are its World Editor category (war3map.wtg); texts of each
trigger in order (quest messages, quest log changes, leaderboard, timers, dialogue, also of transmission triggers it
queues).
"""
import argparse, collections, glob, json, os, re

from dialogue_page import (Icons, Cast, PHASE_RANK, campaign_document, block_html, merge_objects, name_class, phase_name,
                           quest_cards, quest_defs, quest_status, read_w3u, scan_wtg, trigger_var, ui_text, webp)
from fk_clean import ORDER, REC, blizzard_lines, ordered_lines
from fk_extract import FACE, read_wts
from fk_html import KIND, speakers

WORK = os.environ.get("FK_WORK", "")    # folder holding fk/<Map>.files (decrypted maps) and lua/<Map>.lua

# Cinematic (C/M) positions on the scale of the transmission numbers, from the scripts (see docstring).
ANCHORS = {
    # Intro; Vengeance after the cult camp (T240-T250) is destroyed; Locked Gate starts "The Rot Within" (T260+);
    # Rivendare right after T420 (voices heard); Civilians when all cogwheels are back (T470); Sacrifice after the
    # Rivendare fight (T600-T610); Outro after T670.
    "HumanRE02": {"C010": 5, "M010": 252, "M015": 255, "C020": 425, "M020": 475, "C030": 615, "M030": 680},
    "HumanRE02Interlude": {"M010": 5},
    # Intro; M020 when the cult at the base is defeated (queued before T030); M030 on entering Andorhal (after T280,
    # before T290); M040 = victory over Ras.
    "HumanRE03": {"M010": 5, "M020": 25, "M030": 285, "M040": 999},
    # Intro; M020 (Ras transformation) plays T130-T160 itself; M030 (the Baron) after T620-T640.
    "HumanRE04": {"M010": 5, "M030": 645},
}
T_SCENE = {"HumanRE04": {"T130": "M020", "T140": "M020", "T150": "M020", "T160": "M020"}}   # T lines inside a cutscene
GROUP_POS = {"HumanRE03": [(r"\bCages\b", 65)]}       # cage rescues happen while visiting the points of interest
NOT_USED = re.compile(r"\bNOT[ _]USED\b", re.I)


# ------------------------------------------------------------------------------------------------- dialogue order
def auto_anchors(lua_path):
    """Cinematic code -> position on the transmission scale, read from the script the way ANCHORS were worked out:
    CinematicStudio.CreateCinematic("A1M1C020") blocks name the Cinematic_<X>_Setup/Cleanup triggers; the trigger that
    queues Cinematic_<X>_..Q.. also queues transmissions: the last one before it (+0.5) or the first one after (-0.5);
    else what the cinematic's own triggers start next (-0.5); else the same one level up. Returns ({code: pos}, notes)."""
    L = open(lua_path, encoding="utf-8").read().splitlines()
    funcs, cur = {}, None
    for no, ln in enumerate(L):
        m = re.match(r"function (\w+)\(", ln)
        if m:
            cur = m.group(1)
            funcs[cur] = []
        elif ln == "end":
            cur = None
        elif cur:
            funcs[cur].append(ln.strip())
    by_var = collections.defaultdict(list)
    for f, body in funcs.items():
        m = re.match(r"Trig_(\w+?)_(Actions|Conditions|Func\w+)$", f)
        if m:
            by_var[m.group(1)].extend(body)
    tnum = lambda var: sorted({int(x) for s in by_var.get(var, []) for x in re.findall(r"gg_snd_\w*?_T(\d+)", s)})
    edges = lambda var: [m.group(2) for s in funcs.get("Trig_%s_Actions" % var, [])
                         for m in [re.search(r"(QueuedTriggerAddBJ|TriggerExecute|EnableTrigger)\(gg_trg_(\w+)", s)] if m]

    def reach(var, depth=2, seen=None):
        seen = seen if seen is not None else set()
        out = []
        for t in edges(var):
            if t not in seen:
                seen.add(t)
                out += tnum(t) + (reach(t, depth - 1, seen) if depth > 1 else [])
        return out

    callers = collections.defaultdict(list)                  # var -> [(caller var, index of the call)]
    for var in by_var:
        for i, t in enumerate(edges(var)):
            callers[t].append((var, i))
    starts = [(i, m.group(1)) for i, ln in enumerate(L) for m in [re.search(r'CreateCinematic\("(\w+)"', ln)] if m]
    out, notes = {}, {}
    for k, (i, name) in enumerate(starts):
        code = re.search(r"([CM]\d{3})$", name)
        if not code:
            continue
        code = code.group(1)
        end = starts[k + 1][0] if k + 1 < len(starts) else i + 1500
        bases = {re.sub(r"_(Setup|Cleanup)\w*$", "", m) for ln in L[i:end] for m in re.findall(r"gg_trg_(\w+)", ln)
                 if re.search(r"_(Setup|Cleanup)", m)}
        mine = [v for v in by_var if any(v.startswith(b) for b in bases)]
        qvars = [v for v in mine if not re.search(r"_(Setup|Cleanup)", v)]
        pos = None
        for level in (0, 1):
            sites = [(c, idx) for q in qvars for c, idx in callers.get(q, [])] if level == 0 else \
                    [(c2, idx2) for q in qvars for c, _ in callers.get(q, []) for c2, idx2 in callers.get(c, [])]
            for c, idx in sites:
                if re.match(r"Initi?ali[sz]ation|Init_", c):
                    pos, why = 1, "started at map start"
                    break
                seq = edges(c)
                before = [n for t in seq[:idx] for n in tnum(t)]
                after = [n for t in seq[idx + 1:] for n in tnum(t)]
                if before:
                    pos, why = max(before) + 0.5, "after T%03d (queued by %s)" % (max(before), c)
                elif after:
                    pos, why = min(after) - 0.5, "before T%03d (queued by %s)" % (min(after), c)
                if pos is not None:
                    break
            if pos is None and level == 0:
                nxt = [n for v in mine for n in reach(v)]
                if nxt:
                    pos, why = min(nxt) - 0.5, "before T%03d (started when the cinematic ends)" % min(nxt)
            if pos is not None:
                break
        if pos is not None:
            out[code], notes[code] = pos, why
    return out, notes


def story(mission_dir, conv_path, mapname, lua_path=None):
    """(played lines in story order, unplayed lines). Lines: fk_clean.blizzard_lines dicts (+ "scene" override)."""
    if mapname in ORDER:
        return ordered_lines(mission_dir, conv_path)[1], []
    lines = blizzard_lines(conv_path, os.path.join(mission_dir, "war3map.wts"))
    anchors = ANCHORS.get(mapname)
    if anchors is None and lua_path and os.path.exists(lua_path):
        anchors = auto_anchors(lua_path)[0]
    anchors, tscene = dict(anchors or {}), T_SCENE.get(mapname, {})
    # cinematics the script gives no clue for: between the known ones by number, the last ones towards the end
    codes = {"%s%03d" % (m.group(1), int(m.group(2))) for x in lines for m in [REC.match(x["label"])]
             if m and m.group(1) in "CM"}
    max_t = max([int(m.group(2)) for x in lines for m in [REC.match(x["label"])] if m and m.group(1) == "T"] or [0])
    known = sorted((int(c[1:]), p) for c, p in anchors.items() if c in codes)
    missing = sorted(codes - set(anchors), key=lambda c: int(c[1:]))
    estimated = set(missing)
    top = max([int(c[1:]) for c in missing] or [0])
    for c in missing:
        n = int(c[1:])
        same = [p for k, p in known if k == n]
        low = [(k, p) for k, p in known if k < n]
        high = [(k, p) for k, p in known if k > n]
        if same:
            anchors[c] = min(same) - 0.25
        elif low and high:
            (lk, lp), (hk, hp) = low[-1], high[0]
            anchors[c] = lp + (hp - lp) * (n - lk) / (hk - lk)
        elif low:
            lk, lp = low[-1]
            anchors[c] = lp + (max_t + 1 - lp) * (n - lk) / max(top - lk, 1)
        elif high:
            hk, hp = high[0]
            anchors[c] = hp * n / (hk + 1)
        else:
            anchors[c] = (max_t + 1) * n / (top + 1)
    # positions are (place, scene code, line number) so two scenes put at the same place never interleave
    played, unplayed, pos, last = [], [], [], (0.0, "", 0.0)
    for i, x in enumerate(lines):
        if NOT_USED.search(x["group"]):
            unplayed.append(x)
            continue
        m = REC.match(x["label"])
        p = None
        if m:
            series, num, sub = m.group(1), int(m.group(2)), int(m.group(3) or 0)
            code = "%s%03d" % (series, num)
            if series == "T":
                p = (float(num), "", float(sub))
                if code in tscene:
                    x = dict(x, scene=tscene[code])
            elif code in anchors:
                p = (float(anchors[code]), code, float(sub))
                if code in estimated:
                    x = dict(x, estimated=True)
        if p is None:
            for pat, gp in GROUP_POS.get(mapname, []):
                if re.search(pat, x["group"]):
                    p = (float(gp), "", 0.0)
        if p is None:                                 # keep it right after the line before it in Blizzard's list
            p = (last[0], last[1], last[2] + 0.0001 * (i + 1) / len(lines))
        last = p if m else last
        played.append(x)
        pos.append((p, i))
    order = sorted(range(len(played)), key=lambda k: pos[k])
    return [played[k] for k in order], unplayed


def scene_blocks(lines):
    """Blocks: each cinematic/cutscene recording its own block, gameplay transmissions merged."""
    out, key = [], None
    for x in lines:
        if x.get("scene"):
            key = (x["scene"][0], x["scene"])
        else:
            m = REC.match(x["label"])
            if m:
                key = ("T", None) if m.group(1) == "T" else (m.group(1), ("≈ " if x.get("estimated") else "") +
                                                             "%s%03d" % (m.group(1), int(m.group(2))))
            elif key is None:
                key = ("T", None)
        if not out or out[-1]["key"] != key:
            out.append({"key": key, "lines": []})
        out[-1]["lines"].append(x)
    return out


# ----------------------------------------------------------------------------------------------------- Lua quests
class LuaMap:
    """Decompiled war3map.lua of a Blizzard map + its strings/conversation, with the interface dialogue_page.quest_cards
    uses: quests(), quest_steps(q), string(ref), subtitle(snd)."""

    def __init__(self, lua_path, wts, conv_path, wtg_bytes, qi_map=None):
        self.lines = open(lua_path, encoding="utf-8").read().splitlines()
        self.S = wts
        self.qi_map = qi_map                         # position of the map in its chapter (table-driven journal)
        self.funcs, cur = {}, None
        for no, ln in enumerate(self.lines, 1):
            m = re.match(r"function (\w+)\(", ln)
            if m:
                cur = m.group(1)
                self.funcs[cur] = []
            elif ln == "end":
                cur = None
            elif cur:
                self.funcs[cur].append((no, ln.strip()))
        self.wtg = scan_wtg(wtg_bytes)
        self.trig_of_var = {trigger_var(n): n for n in self.wtg}
        self.actions, self.trig_line = {}, {}
        for f, body in self.funcs.items():
            for no, s in body:
                m = re.match(r"TriggerAddAction\(gg_trg_(\w+),\s*(\w+)\)", s)
                if m and m.group(2) in self.funcs:
                    t = self.trig_of_var.get(m.group(1), m.group(1))
                    self.actions[t] = m.group(2)
                    self.trig_line[t] = self.funcs[m.group(2)][0][0] if self.funcs[m.group(2)] else no
        self.sound = {}
        conv = json.load(open(conv_path, encoding="utf-8"))["conversation"]
        for es in conv.values():
            for e in es:
                lab = e.get("animationLabel") or os.path.splitext(os.path.basename(e.get("soundFile") or ""))[0]
                self.sound.setdefault(re.sub(r"\W", "_", lab), e)

    def _assignments(self):
        """udg_ string variables -> [(line, expression)] in file order (main campaign builds quest texts this way:
        udg_MQSecondChanceTitle = StringIdentity("TRIGSTR_3693"), descriptions concatenated with ..)."""
        if not hasattr(self, "_assign"):
            self._assign = collections.defaultdict(list)
            counters = {}                            # udg_X = 5 / udg_X = udg_X + 1, carried on from function to
            for body in self.funcs.values():         # function in file order (QM_Data_Undercity, ..._Villages, ...)
                for no, s in body:
                    m = re.match(r"(udg_\w+)\s*=\s*(-?\d+)$", s)
                    if m:
                        counters[m.group(1)] = int(m.group(2))
                        continue
                    m = re.match(r"(udg_\w+)\s*=\s*\1\s*\+\s*(\d+)$", s)
                    if m and m.group(1) in counters:
                        counters[m.group(1)] += int(m.group(2))
                        continue
                    m = re.match(r"(udg_\w+)\[(udg_\w+)\]\s*=\s*(.+)$", s)
                    if m and m.group(2) in counters:  # array filled through a counter (QM_ExternalizedStrings)
                        self._assign["%s[%d]" % (m.group(1), counters[m.group(2)])].append((no, m.group(3)))
                        continue
                    m = re.match(r'(udg_\w+(?:\[\d+\])?)\s*=\s*(.+)$', s)
                    if m and ('"' in m.group(2) or ".." in m.group(2) or m.group(2).startswith("udg_")):
                        self._assign[m.group(1)].append((no, m.group(2)))
            for v in self._assign.values():
                v.sort()
        return self._assign

    def eval_var(self, var, depth=0):
        """Value of a string variable after all its assignments in file order -> (text, [string ids])."""
        val, ids = "", []
        if depth > 5:
            return val, ids
        for _, expr in self._assignments().get(var, []):
            out, out_ids = "", []
            for part in re.split(r'\s*\.\.\s*(?=(?:[^"]*"[^"]*")*[^"]*$)', expr):
                part = part.strip()
                m = re.fullmatch(r'(?:StringIdentity\(|GetLocalizedString\()?"(TRIGSTR_\d+)"\)?', part)
                if part == var:
                    out, out_ids = out + val, out_ids + ids
                elif m:
                    out += self.S.get(int(m.group(1)[8:]), "")
                    out_ids.append(int(m.group(1)[8:]))
                elif re.fullmatch(r'"(?:[^"\\]|\\.)*"', part):
                    out += part[1:-1].replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
                elif re.fullmatch(r"udg_\w+(?:\[\d+\])?", part):
                    t, i = self.eval_var(part, depth + 1)
                    out, out_ids = out + t, out_ids + i
                else:
                    out += "…"
            val, ids = out, out_ids
        return val, ids

    def string(self, ref):
        if isinstance(ref, str) and ref.startswith("TRIGSTR_"):
            return int(ref[8:]), self.S.get(int(ref[8:]))
        if isinstance(ref, str) and ref.startswith("udg_"):
            text, ids = self.eval_var(ref)
            return ("+".join(str(i) for i in ids) or None), text
        return None, ref

    def subtitle(self, snd):
        e = self.sound.get(snd[len("gg_snd_"):], {})
        if e.get("trigstr"):
            return self.string(e["name"])[1] or "", self.string(e["text"])[1] or "", self.string(e["text"])[0], "no voice"
        name = self.S.get(e.get("speakerNameId"))
        text = FACE.sub("", self.S.get(e.get("dialogueId"), "")).strip()
        return name or "", text, e.get("dialogueId"), e.get("animationLabel") or ""

    def unit_of(self, snd):
        return self.sound.get(snd[len("gg_snd_"):], {}).get("speakerUnitID")

    def trigger_text(self, trig):
        var = trigger_var(trig)
        return "\n".join(s for f, body in self.funcs.items() if f.startswith("Trig_%s_" % var) for _, s in body)

    def qi_quests(self):
        """Chapter 2 quest journal: Lua table QI_QuestDataByMap = {[1] = {name = "Map 1", quests = {{key = "UC_MQ_01_..",
        questType = .., title = function() return udg_QM_ExternalizedStrings[1] end, icon = "..", requirements = {{text =
        function() return udg_QM_ExternalizedStrings[3] end}, ..}}, ..}}, ..}. Every map of the chapter carries the whole
        table; entry N = the N-th map of the chapter (hub 1, then its areas in order: UC = Undercity hub, FV = villages,
        entry 4 empty = Blackrock Encampment, ...). Progress runs inside the journal code, so no per-trigger texts."""
        text = "\n".join(self.lines)
        i = text.find("QI_QuestDataByMap = {")
        if i < 0 or not self.qi_map:
            return []
        depth, j = 0, text.index("{", i)
        for k in range(j, len(text)):
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            if depth == 0:
                break
        block = text[j:k + 1]
        starts = [(m.start(), int(m.group(1))) for m in re.finditer(r"\[(\d+)\] = \{\s*name = ", block)]
        part = next((block[s:(starts[n + 1][0] if n + 1 < len(starts) else len(block))]
                     for n, (s, idx) in enumerate(starts) if idx == self.qi_map), "")
        block = part
        out = []
        for m in re.finditer(r'key = "([^"]+)"', block):
            nxt = block.find('key = "', m.end())
            body = block[m.start(): nxt if nxt > 0 else len(block)]
            ext = lambda field: (re.search(field + r" = function\(\)\s*return (udg_\w+\[\d+\])", body) or [None, ""])[1]
            req = body[body.find("requirements = {"):] if "requirements = {" in body else ""
            out.append({"var": m.group(1), "key": m.group(1), "line": 0,
                        "type": (re.search(r"questType = (bj_QUESTTYPE_\w+)", body) or [None, "bj_QUESTTYPE_REQ_DISCOVERED"])[1],
                        "title": ext("title"), "desc": ext("description"),
                        "icon": (re.search(r'icon = "([^"]*)"', body) or [None, ""])[1].replace("\\\\", "\\"),
                        "items": [{"var": None, "text": r} for r in
                                  re.findall(r"text = function\(\)\s*return (udg_\w+\[\d+\])", req)]})
        return out

    def quests(self):
        if hasattr(self, "_quests"):
            return self._quests
        out, seen = [], set()
        defs = quest_defs(sorted((no, s) for body in self.funcs.values() for no, s in body))
        if not any(q["title"].startswith(("TRIGSTR_", "udg_")) and "[" not in q["title"] for q in defs):
            defs = self.qi_quests() or defs           # chapter 2: table-driven quests, one generic CreateQuestBJ
        for q in defs:
            if q["icon"].startswith("udg_"):
                q["icon"] = self.eval_var(q["icon"])[0].replace("\\\\", "\\")
            key = self.string(q["title"])[1] or q["title"]
            if key not in seen:                      # some quests are created again by checkpoint/reload triggers
                seen.add(key)
                texts, items = set(), []
                for it in q["items"]:                # ... and so are their requirements
                    t = self.string(it["text"])[1] or it["text"]
                    if t not in texts:
                        texts.add(t)
                        items.append(it)
                q["items"] = items
                out.append(q)
        texts = {t: self.trigger_text(t) for t in self.wtg}
        for q in out:
            if q.get("key"):
                pat = re.compile(re.escape('"%s"' % q["key"]))
            else:
                tokens = [q["var"]] + [it["var"] for it in q["items"] if it["var"]]
                pat = re.compile(r"\b(%s)\b" % "|".join(re.escape(t) for t in tokens if t))
            votes = collections.Counter(self.wtg[t] for t, text in texts.items()
                                        if self.wtg[t] and "CreateQuestBJ" not in text and pat.search(text))
            q["category"] = votes.most_common(1)[0][0] if votes else None
            q["triggers"] = sorted((t for t, c in self.wtg.items() if c == q["category"] and t in self.actions),
                                   key=lambda t: self.trig_line.get(t, 0))
        self._quests = out
        return out

    def events(self, trig, q, depth=0, seen=None):
        seen = seen if seen is not None else set()
        out = []
        for no, s in self.funcs.get(self.actions.get(trig, ""), []):
            m = re.search(r"PlayDialogueFromSpeaker(?:Type)?Ex\(.*?(gg_snd_\w+)", s)
            if m:
                out.append(("line", no, trig, m.group(1), self.unit_of(m.group(1))))
                continue
            m = re.search(r'TransmissionFromUnitWithNameBJ\([^,]+,\s*[^,]+,\s*"(TRIGSTR_\d+)",\s*(\w+),\s*"(TRIGSTR_\d+)"', s)
            if m:
                snd = m.group(2) if m.group(2).startswith("gg_snd_") and m.group(2)[7:] in self.sound else None
                if not snd:
                    snd = "gg_snd_trigstr_" + m.group(3)[8:]
                    self.sound[snd[7:]] = {"trigstr": True, "name": m.group(1), "text": m.group(3)}
                out.append(("line", no, trig, snd, self.unit_of(snd)))
                continue
            ui = ui_text(s)
            if ui:
                sid, text = self.string(ui[1])
                out.append(("ui", no, trig, ui[0], text or "", sid))
                continue
            st = quest_status(s, q, self.string)
            if st:
                out.append(("status", no, trig, st))
                continue
            m = re.search(r"(?:QueuedTriggerAddBJ|TriggerExecute)\(gg_trg_(\w+)", s)
            if m and depth < 1:
                t = self.trig_of_var.get(m.group(1), m.group(1))
                if t not in q["triggers"] and t not in seen:
                    seen.add(t)
                    out.extend(e for e in self.events(t, q, depth + 1, seen) if e[0] in ("line", "ui"))
        return out

    def quest_steps(self, q):
        out = []
        for trig in q["triggers"]:
            if re.search(r"\bDebug\b", trig):           # developers' test triggers
                continue
            evs = [e for e in self.events(trig, q) if not (e[0] == "ui" and not (e[4] or "").strip())]
            # (messages built at run time by the main campaign's quest journal have no static text)
            if any(e[0] in ("line", "ui") for e in evs):
                out.append((trig, phase_name(trig), evs))
        return out


# ----------------------------------------------------------------------------------------------------------- build
def heroes_section(units, game, style, locale):
    """Hero views (texts from the game's string files, see fk_heroes.py) and their JSON."""
    import fk_heroes
    gs = fk_heroes.GameStrings(game)
    icons = Icons(game, style)
    icon_of = lambda art: (lambda im: webp(im) if im is not None else None)(icons.image(art)) if art else None
    views, data = [], []
    for unit in units:
        h = fk_heroes.hero(gs, unit)
        count = sum(len(f["values"]["enUS"]) if isinstance(f["values"].get("enUS"), list) else 1
                    for s in h["sections"] for f in s["fields"] if "enUS" in f["values"] and "otkey" not in f["key"])
        art = (h["sections"][0]["icon"] if h["sections"] else None) or \
            r"ReplaceableTextures\CommandButtons\BTN%s.blp" % re.sub(r"[^A-Za-z]", "", h["name"] or unit)
        if h["sections"]:
            h["sections"][0]["icon"] = art
        views.append({"slug": "hero-" + unit.lower(), "name": h["name"] or unit, "title": h["title"] or "",
                      "icon": icon_of(art),
                      "cards": fk_heroes.hero_cards(h, gs.ability_data, icon_of, locale), "count": count,
                      "note": "These texts are not in the maps: they come from the game's CampaignUnitStrings.txt and "
                              "CampaignAbilityStrings.txt (one per language). Numbers are filled in from the game's "
                              "ability data; Show IDs gives the raw text with its <AHhs,DataA1> placeholders, which a "
                              "translation must keep."})
        data.append(h)
    return views, data


CONFIG = {
    "000_The_Last_Days_of_Lordaeron": {
        "title_key": "CAMP_HUMANRE_NAME", "eyebrow": "Forsaken Kingdom · Prequel", "heroes": ["Hctl", "Hctr", "Hjsm"],
        "side": "human",
        "intro": "Every mission of the prequel campaign in story order: the spoken lines scene by scene, and each quest "
                 "with all of its texts. Pick a mission to start."},
    "001_Forsaken_Kingdom": {
        "title_key": "CAMP_UNDEADRE_NAME", "eyebrow": "Forsaken Kingdom · Main campaign", "heroes": ["Ugrk", "Uany", "Hleo"],
        "side": "undead",
        "intro": "Every map of the main campaign: the hub of each chapter and the areas it opens, with the spoken lines "
                 "scene by scene and each quest with all of its texts. Pick a map to start."},
}
MAPNAME = re.compile(r"^\d+_((?:HumanRE|UndeadRE)\d+(?:Interlude|a|b)?(?:_\d\d)?)_")


def map_labels(mapnames):
    """Map names -> (label, number): HumanRE01 "Mission 1"; UndeadRE01 "Chapter 1" and its areas UndeadRE01_02..
    "Chapter 1 · Area" 1.1, 1.2...; UndeadRE03a/b chapter 3 and its second part; interludes "Interlude"."""
    out, mission, chapter, sub = {}, 0, None, 0
    for m in mapnames:
        if "Interlude" in m:
            out[m] = ("Interlude", "•")
            continue
        g = re.match(r"(HumanRE|UndeadRE)(\d+)([ab])?(?:_(\d\d))?$", m)
        if g.group(1) == "HumanRE":
            mission += 1
            out[m] = ("Mission %d" % mission, str(mission))
            continue
        ch = int(g.group(2))
        if g.group(4) or g.group(3) == "b":
            sub += 1
            out[m] = ("Chapter %d · Area" % ch, "%d.%d" % (ch, sub))
        else:
            chapter, sub = ch, 0
            out[m] = ("Chapter %d" % ch, str(ch))
    return out


def build(campaign, locale, maps_dir, lua_dir, game, style, fragment, heroes=None):
    cfg = CONFIG.get(campaign, CONFIG["000_The_Last_Days_of_Lordaeron"])
    heroes = cfg["heroes"] if heroes is None else heroes
    side = cfg["side"]
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "FK")
    folders = sorted(glob.glob(os.path.join(root, "enUS", campaign, "*", "conversation.json")))
    registry, missions, data, report, quest_pairs = {}, [], [], [], []
    camp_title = re.search(r'%s\s+"([^"]*)"' % cfg["title_key"],
                           open(os.path.join(root, locale, "GLOBAL", "GlobalStrings_FK.fdf.txt"), encoding="utf-8").read())
    camp_title = camp_title.group(1) if camp_title else campaign.split("_", 1)[1].replace("_", " ")
    labels = map_labels([MAPNAME.match(os.path.basename(os.path.dirname(c))).group(1) for c in folders])
    for conv in folders:
        folder = os.path.basename(os.path.dirname(conv))
        mapname = MAPNAME.match(folder).group(1)
        mdir = os.path.join(root, locale, campaign, folder)
        en_dir = os.path.join(root, "enUS", campaign, folder)
        title = json.load(open(os.path.join(mdir, "dialogue.json"), encoding="utf-8"))["title"]
        label, number = labels[mapname]
        lua = os.path.join(lua_dir, mapname + ".lua")
        played, unplayed = story(mdir, conv, mapname, lua)
        en_played = played if locale == "enUS" else story(en_dir, conv, mapname, lua)[0]
        names, unit_of, fixed = speakers(played, en_played)
        files = os.path.join(maps_dir, mapname + ".files")
        objects = merge_objects(*[read_w3u(os.path.join(files, f)) for f in ("war3map.w3u", "war3mapSkin.w3u")
                                  if os.path.exists(os.path.join(files, f))])
        icons = Icons(game, style, objects)
        cast = Cast(registry)                        # slugs and icons shared by all missions of the page
        for n in list(names) + [x["speaker"] or "?" for x in unplayed]:
            icon = None
            if n not in registry:
                u = unit_of.get(n) or next((x["unit"] for x in unplayed if x["speaker"] == n), None)
                im, how = icons.lookup(u) if u else (None, "no unit")
                if im is None:                       # no skin data for the unit (Leonid, Hleo): BTN<Name>
                    im = icons.image(r"ReplaceableTextures\CommandButtons\BTN%s.blp" % re.sub(r"[^A-Za-z]", "", n))
                    how = "by name" if im is not None else how
                icon = webp(im) if im is not None else None
                report.append("%-18s %-20s %-5s %s" % (mapname, n, u, how))
            cast.register(n, name_class(unit_of.get(n), side), icon)

        ids = lambda x: "%s · string %s · name %s" % (x["label"], x["string_id"], x["speaker_string_id"])
        blocks, scenes_json = [], []
        for sc in scene_blocks([dict(x, name=n) for x, n in zip(played, names)]):
            series, code = sc["key"]
            blocks.append(block_html(cast, KIND[series], code, series in ("C", "M"),
                                     [(x["name"], x["text"], ids(x)) for x in sc["lines"]]))
            scenes_json.append({"kind": KIND[series], "code": code and code.replace("≈ ", ""),
                                "placement": "estimated" if code and code.startswith("≈") else "script", "lines": [
                {"label": x["label"], "string_id": x["string_id"], "speaker_string_id": x["speaker_string_id"],
                 "unit": x["unit"], "speaker": x["name"], "text": x["text"]} for x in sc["lines"]]})
        dialogue = "\n".join(blocks)
        if unplayed:
            dialogue += ('\n<details class="appendix"><summary>Recorded but not played (%d lines)</summary><p>These '
                         'transmissions are marked "NOT USED" in the map script.</p>\n%s\n</details>' % (
                             len(unplayed), "\n".join(block_html(cast, "Gameplay", None, False, [
                                 (x["speaker"] or "?", x["text"], ids(x)) for x in unplayed], played=False))))

        pairs = []                                   # (title text, has texts, card html, json) per quest of this map
        wtg = os.path.join(files, "war3map.wtg")
        if os.path.exists(lua) and os.path.exists(wtg):
            family = [MAPNAME.match(os.path.basename(os.path.dirname(c))).group(1) for c in folders]
            family = [x for x in family if x.split("_")[0] == mapname.split("_")[0]]
            mp = LuaMap(lua, read_wts(os.path.join(mdir, "war3map.wts")), conv, open(wtg, "rb").read(),
                        qi_map=family.index(mapname) + 1)
            cards = quest_cards(mp, cast, icons, None, lambda u: name_class(u, side))
            for q, card in zip(mp.quests(), cards):
                steps = sorted(mp.quest_steps(q), key=lambda p: PHASE_RANK.get(p[1], 1))
                pairs.append((mp.string(q["title"])[1] or q["title"], bool(steps), card, {
                    "var": q["var"], "type": q["type"], "category": q["category"], "icon": q["icon"],
                    "title_id": mp.string(q["title"])[0], "description_id": mp.string(q["desc"])[0],
                    "requirements": [{"var": it["var"], "string_id": mp.string(it["text"])[0]} for it in q["items"]],
                    "phases": [{"trigger": t, "phase": ph, "steps": [
                        {"type": "line", "sound": e[3], "label": mp.subtitle(e[3])[3], "string_id": mp.subtitle(e[3])[2]}
                        if e[0] == "line" else {"type": "ui", "kind": e[3], "string_id": e[5]} if e[0] == "ui"
                        else {"type": "status", "text": e[3]} for e in evs]} for t, ph, evs in steps]}))
        else:
            report.append("%s: no %s or %s, Quests tab empty" % (mapname, lua, wtg))
        quest_pairs.append(pairs)
        missions.append({"slug": mapname.lower(), "label": label, "number": number, "title": title, "cast": cast,
                         "dialogue": dialogue, "quests": "", "quest_count": 0})
        data.append({"map": mapname, "folder": folder, "label": label, "title": title,
                     "wts": "FK/<locale>/%s/%s/war3map.wts" % (campaign, folder), "scenes": scenes_json,
                     "unplayed": [{"label": x["label"], "string_id": x["string_id"], "speaker_string_id": x["speaker_string_id"],
                                   "group": x["group"]} for x in unplayed],
                     "quests": [], "speaker_fixes": fixed})
    # A chapter's maps all create the same quests and carry the same quest triggers (one quest log for the hub and its
    # areas): show each quest once, in the first map where something happens with it, else the first that creates it.
    home = {}
    for i, pairs in enumerate(quest_pairs):
        for t, has, _, _ in pairs:
            if has and t not in home:
                home[t] = i
    for i, pairs in enumerate(quest_pairs):
        for t, has, _, _ in pairs:
            home.setdefault(t, i)
    for i, (m, d, pairs) in enumerate(zip(missions, data, quest_pairs)):
        keep, shown_once = [], set()
        for t, has, card, js in pairs:
            if home.get(t) == i and t not in shown_once:
                shown_once.add(t)
                js["also_created_in"] = [x["map"] for x, p in zip(data, quest_pairs)
                                         if x is not d and any(tt == t for tt, _, _, _ in p)]
                keep.append((card, js))
        m["quests"] = "\n".join(c for c, _ in keep)
        m["quest_count"] = len(keep)
        d["quests"] = [j for _, j in keep]
    hero_views, hero_data = heroes_section(heroes, game, style, locale) if heroes else ([], [])
    doc = campaign_document(
        lang=locale[:2] + "-" + locale[2:], title="%s Script" % camp_title, eyebrow=cfg["eyebrow"],
        heading=camp_title, fragment=fragment, heroes=hero_views, intro=cfg["intro"],
        description="Warcraft III: Reforged, %s: dialogue, quest and hero texts of every map." % cfg["eyebrow"],
        missions=missions,
        notes=["Order follows the mission scripts. Lines from optional encounters and side events can play in a "
               "different order depending on the player's route. A cinematic marked ≈ has no clear place in the "
               "script; it is put between its neighbours by number.",
               "Warcraft III: Reforged — Forsaken Kingdom. Dialogue, quest texts, character names and icons "
               "© Blizzard Entertainment."])
    return doc, {"campaign": campaign, "title": camp_title, "locale": locale,
                 "note": "Text ids refer to war3map.wts of each mission folder; every locale folder uses the same ids. "
                         "Heroes: sections of the game's string files (see tools/fk_heroes.py), all languages.",
                 "missions": data, "heroes": hero_data}, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default="000_The_Last_Days_of_Lordaeron")
    ap.add_argument("--locale", default="enUS")
    ap.add_argument("--maps", default=os.path.join(WORK, "fk") if WORK else None, required=not WORK,
                    help="folder with <Map>.files (decrypted maps); default $FK_WORK/fk")
    ap.add_argument("--lua", default=os.path.join(WORK, "lua") if WORK else None, required=not WORK,
                    help="folder with <Map>.lua (decompiled scripts); default $FK_WORK/lua")
    ap.add_argument("--game", default=r"F:\Games\Warcraft III")
    ap.add_argument("--icons", choices=("de", "hd"), default="hd")
    ap.add_argument("--heroes", nargs="*", default=None,
                    help="hero unit ids for the Heroes section; default: the campaign's playable heroes (CONFIG)")
    ap.add_argument("--fragment", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--data", default=None)
    a = ap.parse_args()
    doc, data, report = build(a.campaign, a.locale, a.maps, a.lua, a.game, a.icons, a.fragment, a.heroes)
    site = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "FK", "site")
    out = a.out or os.path.join(site, "%s_%s.html" % (a.campaign, a.locale))
    dat = a.data or os.path.join(site, "%s_%s.json" % (a.campaign, a.locale))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w", encoding="utf-8", newline="\n").write(doc)
    if not a.fragment:
        json.dump(data, open(dat, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("wrote %s" % os.path.normpath(dat))
    print("wrote %s (%d KB)" % (os.path.normpath(out), len(doc.encode()) // 1024))
    for m in data["missions"]:
        print("  %-20s %3d scenes %4d lines %2d unplayed %2d quests" % (
            m["map"], len(m["scenes"]), sum(len(s["lines"]) for s in m["scenes"]), len(m["unplayed"]), len(m["quests"])))
    print("speakers:\n  " + "\n  ".join(report))


if __name__ == "__main__":
    main()
