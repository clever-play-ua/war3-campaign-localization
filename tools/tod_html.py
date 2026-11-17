"""Tides of Darkness (LoreCraft CSW campaign): one-file HTML page with the dialogue of one or more maps in story order.

    python tools/tod_html.py --maps WC2OrcAct1.w3x WC2Orc01.w3x
        [--w3n D:/Games/WarCraft/CSW_Tides_Of_Darkness.w3n] [--game "F:/Games/Warcraft III"] [--icons de|hd]
        [--fragment] [--out CSW-ToD/dialogue/<maps>.html]

How these maps play dialogue (JASS, war3map.j):
* `PlayDialogueFromSpeakerEx(force, unit, 'type' or 0, gg_snd_X, ...)`, `PlayDialogueFromSpeakerTypeEx(force, player,
  'type', loc, gg_snd_X, ...)`, barks `PlaySoundOnUnitBJ(gg_snd_X, vol, unit)`.
* Subtitles come from "localization items": `CreateItemLoc('IAL0', ...)`, then `SetDialogueTextKey(gg_snd_X,
  BlzGetItemDescription(...))` (and the speaker name from BlzGetItemTooltip). The item's tooltip `utip` (speaker) and
  description `ides` (line) are TRIGSTR in the map's war3map.wts (items in war3map.w3t / war3mapSkin.w3t).
* Captions between scenes: `DisplayTimedTextToForce(..., "TRIGSTR_n")`.

Story order: triggers are walked from the roots in STORY like the game queue does (QueuedTriggerAddBJ appends,
TriggerExecute / ConditionalTriggerExecute runs right after the current trigger, each trigger once). `if` branches on
constant flags are evaluated (values from the globals block, InitGlobals and the Initialization trigger; unknown
conditions take both branches), `return` in a taken branch ends the trigger. Lines that are never reached and whose sound
is not played anywhere else go to an appendix ("recorded but not played").
Icons: the speaker's unit type -> custom icon (`uico`) from the map, the campaign, the game's loose files (_retail_, where
the CSW V2 asset pack puts ReplaceableTextures\\CommandButtons\\BTNOrgrim.dds etc.) or CASC; generic soldiers fall back to
their base unit's icon, named characters without an icon get a monogram.
"""
import argparse, collections, os, re, struct, sys

from dialogue_page import (Icons, LooseFiles, Cast, block_html, caption_html, document, merge_objects, name_class,
                           phase_name, quest_cards, quest_defs, quest_status, read_w3u, ui_text, webp)
from fk_extract import read_wts
from w3mpq import MpqArchive, open_nested

# Roots in story order per map (trigger names). Maps not listed: Initialization, then every trigger with dialogue.
STORY = {
    # The player answers "Watch Anyway" in the skip-cutscenes dialog (PlayerWatchesDialog queues Logo Cinematic Q).
    "WC2OrcAct1": ["Logo Cinematic Q"],
    # Event-driven quests in the usual order of play; wolves can attack earlier or later.
    "WC2Orc01": ["Initialization", "Wolves Discover", "First Discover", "HumanTaunt Sound", "First Update KilledScouts",
                 "Wolves Completed", "AllQuestsCompleted Victory"],
}
SIDE = {"Nbbc": "orc", "N60K": "orc"}            # Varok, Maim: Blackrock orcs on neutral-hero base units
TITLE_FIX = {"Guldan Dream": "Gul'dan's Dream", "Human Taunt Sound": "Combat"}
# Speakers whose unit has no icon of its own. Varok is a plain Nbbc (Chaos Blademaster icon) with the Varok model in every
# map; the CSW asset pack ships BTNSaurfang.dds (game folder) that no unit references.
ICON_FIX = {"Varok Saurfang": r"ReplaceableTextures\CommandButtons\BTNSaurfang.blp"}


# ----------------------------------------------------------------------------------------------------------- JASS
class Jass:
    def __init__(self, text):
        self.lines = text.splitlines()
        self.funcs, self.trig_of_var, self.actions, self.trig_line = {}, {}, {}, {}
        name, body, section = None, [], None
        for no, ln in enumerate(self.lines, 1):
            s = ln.strip()
            m = re.match(r"// Trigger: (.+)", s)
            if m:
                section = m.group(1).strip()
                self.trig_line[section] = no
            m = re.match(r"function (\w+) takes", s)
            if m:
                name, body = m.group(1), []
                continue
            if s == "endfunction" and name:
                self.funcs[name] = (body, section)
                name = None
                continue
            if name:
                body.append((no, s))
            if section:
                m = re.match(r"set (gg_trg_\w+)\s*=\s*CreateTrigger", s)
                if m:
                    self.trig_of_var[m.group(1)] = section
                m = re.match(r"call TriggerAddAction\((gg_trg_\w+),\s*function (\w+)\)", s)
                if m:
                    self.actions[self.trig_of_var.get(m.group(1), section)] = m.group(2)
        self.env = self._constants()

    def _constants(self):
        env = {}
        for ln in self.lines:
            m = re.match(r"\s*(boolean|integer)\s+(udg_\w+)\s*=\s*(true|false|-?\d+)\s*$", ln)
            if m:
                env[m.group(2)] = m.group(3)
            if ln.strip() == "endglobals":
                break
        for fn in ("InitGlobals", self.actions.get("Initialization")):
            for _, s in self.funcs.get(fn, ([], None))[0]:
                m = re.match(r"set (udg_\w+)\s*=\s*(true|false|-?\d+)$", s)
                if m:
                    env[m.group(1)] = m.group(2)
        return env

    # conditions -------------------------------------------------------------------------------------------------
    def eval(self, expr, env):
        e = expr.strip()
        while e.startswith("(") and self._closes_at_end(e):
            e = e[1:-1].strip()
        if e.startswith("not "):
            v = self.eval(e[4:], env)
            return None if v is None else not v
        m = re.fullmatch(r"(udg_\w+)\s*(==|!=)\s*(true|false|-?\d+)", e)
        if m:
            cur = env.get(m.group(1))
            if cur is None:
                return None
            return (cur == m.group(3)) == (m.group(2) == "==")
        m = re.fullmatch(r"(Trig_\w+)\(\)", e)
        if m and m.group(1) in self.funcs:
            return self._eval_func(m.group(1), env)
        return None

    @staticmethod
    def _closes_at_end(e):
        depth = 0
        for i, ch in enumerate(e):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and i < len(e) - 1:
                return False
        return True

    def _eval_func(self, name, env):
        body = [s for _, s in self.funcs[name][0]]
        unknown, i = False, 0
        while i < len(body):
            m = re.match(r"if (.+) then$", body[i])
            if m and i + 2 < len(body) and body[i + 2] == "endif" and body[i + 1] in ("return true", "return false"):
                v = self.eval(m.group(1), env)
                if v is None:
                    unknown = True
                elif v:
                    return None if unknown else body[i + 1] == "return true"
                i += 3
                continue
            m = re.match(r"return (.+)$", body[i])
            if m:
                v = {"true": True, "false": False}.get(m.group(1).strip())
                v = self.eval(m.group(1), env) if v is None else v
                return None if unknown else v
            i += 1
        return None

    # statements -------------------------------------------------------------------------------------------------
    def tree(self, fn):
        """Function body -> nested list of ("stmt", no, text) / ("if", no, cond, then, else)."""
        body = [(no, re.sub(r"\s+//.*$", "", s) if s.startswith(("if ", "elseif ")) else s)
                for no, s in self.funcs.get(fn, ([], None))[0]]
        pos = 0

        def block(stop):
            nonlocal pos
            out = []
            while pos < len(body):
                no, s = body[pos]
                if s.split(" ")[0] in stop:
                    return out, s
                pos += 1
                m = re.match(r"if (.+) then$", s)
                if m:
                    out.append(parse_if(no, m.group(1)))
                elif not s.startswith("//"):
                    out.append(("stmt", no, s))
            return out, None

        def parse_if(no, cond):
            nonlocal pos
            then, end = block(("else", "elseif", "endif"))
            other = []
            if end == "else":
                pos += 1
                other, _ = block(("endif",))
                pos += 1
            elif end and end.startswith("elseif"):
                no2, s2 = body[pos]
                pos += 1
                other = [parse_if(no2, re.match(r"elseif (.+) then$", s2).group(1))]  # shares the endif
            else:
                pos += 1
            return ("if", no, cond, then, other)

        return block(())[0]


# ----------------------------------------------------------------------------------------------------------- map
class Map:
    def __init__(self, camp, name):
        self.name = os.path.splitext(name)[0]
        self.arc = open_nested(camp, name)
        self.jass = Jass(self.arc.read("war3map.j").decode("utf-8", "replace"))
        self.S = read_wts_bytes(self.arc.read("war3map.wts"))
        self.items = merge_objects(*[read_w3u(self.arc.read(f)) for f in ("war3map.w3t", "war3mapSkin.w3t")
                                     if self.arc.read(f)])
        self.sound_item = self._sound_items()
        self.unit_of_var = self._unit_vars()
        conv = self.arc.read("conversation.json")
        self.category = {}
        if conv:
            import json
            for group in json.loads(conv.decode("utf-8"))["conversation"]:
                cat, _, trig = group.rpartition(".")
                self.category[trig.strip()] = cat.strip() or trig.strip()

    def _sound_items(self):
        out, cur = {}, None
        for ln in self.jass.lines:
            m = re.search(r"CreateItem\w*\('(\w{4})'", ln)
            if m:
                cur = m.group(1)
            m = re.search(r"SetDialogue(SpeakerName|Text)Key\((gg_snd_\w+),\s*BlzGetItem(Tooltip|Description)", ln)
            if m:
                out.setdefault(m.group(2), {})[m.group(1)] = cur
        return out

    def _unit_vars(self):
        out, last = {}, None
        for ln in self.jass.lines:
            m = re.search(r"Create\w*Unit\w*\([^']*'(\w{4})'", ln)
            if m:
                last = m.group(1)
            m = re.search(r"set (udg_\w+)\s*=\s*(gg_unit_(\w{4})_\d+|GetLastCreatedUnit\(\)|bj_lastCreatedUnit)", ln)
            if m:
                out.setdefault(m.group(1), m.group(3) or last)
        return out

    def string(self, ref):
        if isinstance(ref, str) and ref.startswith("TRIGSTR_"):
            return int(ref[8:]), self.S.get(int(ref[8:]))
        return None, ref

    def subtitle(self, snd):
        keys = self.sound_item.get(snd, {})
        item = self.items.get(keys.get("Text") or keys.get("SpeakerName") or "", {})
        sid, text = self.string(item.get("ides"))
        nid, name = self.string(item.get("utip"))
        if not name and keys.get("SpeakerName"):
            nid, name = self.string(self.items.get(keys["SpeakerName"], {}).get("utip"))
        return (name or "").strip(), (text or "").strip(), sid, "item %s" % keys.get("Text")

    def unit_type(self, unit_expr, type_expr):
        m = re.fullmatch(r"'(\w{4})'", (type_expr or "").strip())
        if m:
            return m.group(1)
        u = (unit_expr or "").strip()
        m = re.fullmatch(r"gg_unit_(\w{4})_\d+", u)
        if m:
            return m.group(1)
        return self.unit_of_var.get(u)

    # walking ----------------------------------------------------------------------------------------------------
    def events_of(self, stmt_no, s, trig):
        """Events of one statement: ("line", no, trig, snd, unit) / ("caption", no, trig, text) / ("queue"|"run", target)."""
        m = re.search(r"PlayDialogueFromSpeakerEx\([^,]+,\s*([^,]+),\s*([^,]+),\s*(gg_snd_\w+)", s)
        if m:
            return [("line", stmt_no, trig, m.group(3), self.unit_type(m.group(1), m.group(2)))]
        m = re.search(r"PlayDialogueFromSpeakerTypeEx\([^,]+,\s*[^,]+,\s*('\w{4}'),\s*[^,]+(?:\([^)]*\))?,\s*(gg_snd_\w+)", s)
        if m:
            return [("line", stmt_no, trig, m.group(2), m.group(1).strip("'"))]
        m = re.search(r"PlaySound\w*BJ\((gg_snd_\w+)(?:,\s*[^,]+,\s*(.+))?\)", s)
        if m and m.group(1) in self.sound_item:
            return [("line", stmt_no, trig, m.group(1), self.unit_type(m.group(2), None))]
        m = re.search(r"Display(?:Timed)?TextToForce\([^\"]*\"(TRIGSTR_\d+)\"", s)
        if m:
            return [("caption", stmt_no, trig, self.string(m.group(1))[1] or "")]
        m = re.search(r"QueuedTriggerAddBJ\((gg_trg_\w+)", s)
        if m:
            return [("queue", self.jass.trig_of_var.get(m.group(1)))]
        m = re.search(r"(?:Conditional)?TriggerExecute\((gg_trg_\w+)", s)
        if m:
            return [("run", self.jass.trig_of_var.get(m.group(1)))]
        return []

    def run_trigger(self, trig, env):
        """Events of a trigger's actions in order, honouring constant branches and return."""
        out = []

        def walk(nodes):
            for node in nodes:
                if node[0] == "if":
                    v = self.jass.eval(node[2], env)
                    if v is True:
                        r = walk(node[3])
                    elif v is False:
                        r = walk(node[4])
                    else:
                        r = walk(node[3]) & walk(node[4])
                    if r:
                        return True
                    continue
                _, no, s = node
                if s == "return" or s.startswith("return "):
                    return True
                m = re.match(r"set (udg_\w+)\s*=\s*(true|false|-?\d+)$", s)
                if m:
                    env[m.group(1)] = m.group(2)
                out.extend(self.events_of(no, s, trig))
            return False

        walk(self.jass.tree(self.jass.actions.get(trig, "")))
        return out

    def story(self):
        roots = STORY.get(self.name)
        if roots is None:
            roots = ["Initialization"] + sorted(
                {t for t, fn in self.jass.actions.items() if t != "Initialization"
                 and any("PlayDialogue" in s for _, s in self.jass.funcs.get(fn, ([], None))[0])},
                key=lambda t: self.jass.trig_line.get(t, 0))
        env, seen, played = dict(self.jass.env), set(), []
        for root in roots:
            queue = [root]
            while queue:
                t = queue.pop(0)
                if not t or t in seen:
                    continue
                seen.add(t)
                later = []
                for ev in self.run_trigger(t, env):
                    if ev[0] == "queue":
                        queue.append(ev[1])
                    elif ev[0] == "run":
                        later.append(ev[1])
                    else:
                        played.append(ev)
                queue[0:0] = later
        return played

    # quests -----------------------------------------------------------------------------------------------------
    def quests(self):
        """Quests made with CreateQuestBJ: type, title/description strings, icon, requirements, and their triggers =
        the World Editor category (war3map.wtg) whose triggers mention the quest or its requirements most."""
        if not hasattr(self, "wtg"):
            self.wtg = wtg_categories(self.arc.read("war3map.wtg"), list(self.jass.trig_line))
        out = quest_defs(sorted((no, s) for fn, (body, _) in self.jass.funcs.items() for no, s in body))
        sec_text = collections.defaultdict(list)
        for body, sec in self.jass.funcs.values():
            sec_text[sec].extend(s for _, s in body)
        sec_text = {k: "\n".join(v) for k, v in sec_text.items()}
        for q in out:
            tokens = [q["var"]] + [it["var"] for it in q["items"] if it["var"]]
            pat = re.compile(r"\b(%s)\b" % "|".join(re.escape(t) for t in tokens if t))
            votes = collections.Counter()
            for trig, text in sec_text.items():
                if trig in self.wtg and "CreateQuestBJ" not in text and pat.search(text):
                    votes[self.wtg[trig]] += 1
            q["category"] = votes.most_common(1)[0][0] if votes else None
            q["triggers"] = sorted((t for t, c in self.wtg.items() if c == q["category"]),
                                   key=lambda t: self.jass.trig_line.get(t, 0))
        return out

    def ui_events(self, no, s, trig, q):
        """Texts the player sees, for the quest page: dialogue, quest messages, leaderboard, timers, quest log changes."""
        ev = [e for e in self.events_of(no, s, trig) if e[0] in ("line", "caption")]
        if ev:
            return [("line", e[1], e[2], e[3], e[4]) if e[0] == "line" else ("ui", e[1], e[2], "On-screen text", e[3], None)
                    for e in ev]
        ui = ui_text(s)
        if ui:
            sid, text = self.string(ui[1])
            return [("ui", no, trig, ui[0], text or "", sid)]
        st = quest_status(s, q, self.string)
        if st:
            return [("status", no, trig, st)]
        return []

    def quest_steps(self, q):
        """[(trigger, phase label, events)] for the quest's triggers in file order; texts only (both branches of
        conditions that cannot be evaluated)."""
        out = []
        for trig in q["triggers"]:
            env, evs = dict(self.jass.env), []

            def walk(nodes):
                for node in nodes:
                    if node[0] == "if":
                        v = self.jass.eval(node[2], env)
                        if v is not False:
                            walk(node[3])
                        if v is not True:
                            walk(node[4])
                        continue
                    evs.extend(self.ui_events(node[1], node[2], trig, q))

            walk(self.jass.tree(self.jass.actions.get(trig, "")))
            if any(e[0] in ("line", "ui") for e in evs):
                out.append((trig, phase_name(trig), evs))
        return out

    def all_lines(self):
        """Every dialogue play in the script (reached or not), by trigger, in file order."""
        out = []
        for trig, fn in self.jass.actions.items():
            for no, s in self.jass.funcs.get(fn, ([], None))[0]:
                out.extend(e for e in self.events_of(no, s, trig) if e[0] == "line")
        return sorted(out, key=lambda e: e[1])


def wtg_categories(data, trigger_names):
    """war3map.wtg (Reforged format, subversion 7) -> {trigger name: category name}, without parsing the ECAs.

    A trigger record is: classifier u32 (8 GUI, 32 custom text), name, description (C strings), then is_comment, id,
    enabled, is_script, initially_off, run_on_init, parent id (u32 each). A category: 4, id, name, is_comment, open, parent."""
    out, names = {}, {}
    if not data or data[:4] != b"WTG!":
        return out
    for t in trigger_names:
        raw = t.encode("utf-8") + b"\0"
        for cls in (8, 32):
            i = data.find(struct.pack("<I", cls) + raw)
            if i < 0:
                continue
            p = i + 4 + len(raw)
            p = data.index(b"\0", p) + 1                       # description
            parent = struct.unpack_from("<I", data, p + 24)[0]
            if parent not in names:
                j = data.find(struct.pack("<II", 4, parent))
                names[parent] = data[j + 8:data.index(b"\0", j + 8)].decode("utf-8", "replace") if j >= 0 else None
            if names[parent]:
                out[t] = names[parent]
            break
    return out


def read_wts_bytes(data):
    import tempfile
    with tempfile.NamedTemporaryFile("wb", suffix=".wts", delete=False) as f:
        f.write(data)
    try:
        return read_wts(f.name)
    finally:
        os.unlink(f.name)


def scene_label(category):
    """Trigger category -> (kind, title, cinematic?)."""
    m = re.match(r"(Main|Optional) Quest\s*-\s*(.+)", category)
    if m:
        return ("Main quest" if m.group(1) == "Main" else "Optional quest"), m.group(2).strip(), False
    cin = "cinematic" in category.lower()
    words = re.sub(r"(?i)cinematic|\bQ\b|\d+$", " ", category)
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", words)
    title = " ".join(words.split()) or None
    title = TITLE_FIX.get(title, title)
    return ("Cinematic" if cin else "Gameplay"), title, cin


def mission_titles(camp):
    S = read_wts_bytes(camp.read("war3campaign.wts"))
    parts = [p.decode("utf-8", "replace") for p in camp.read("war3campaign.w3f").split(b"\0")]
    out = {}
    for i, p in enumerate(parts):
        if p.lower().endswith(".w3x") and i >= 2 and parts[i - 2].startswith("TRIGSTR_") and p not in out:
            out[os.path.splitext(p)[0]] = tuple(S.get(int(x[8:]), "") for x in parts[i - 2:i])
    camp_name = S.get(1, "Tides of Darkness")
    return camp_name, out


def build(w3n, maps, game, style="de", fragment=False, quests=None):
    camp = MpqArchive(w3n)
    camp_name, titles = mission_titles(camp)
    campaign_objects = [read_w3u(camp.read(f)) for f in ("war3campaign.w3u", "war3campaignSkin.w3u") if camp.read(f)]
    loose = LooseFiles(os.path.join(game, "_retail_"))
    report, parts, appendix, all_events = [], [], [], []
    loaded = [Map(camp, m) for m in maps]

    # speakers: name -> unit (majority over played lines), icons
    for mp in loaded:
        played = mp.story()
        played_sounds = {e[3] for e in played if e[0] == "line"}
        played_nos = {(e[2], e[1]) for e in played if e[0] == "line"}
        unplayed = [e for e in mp.all_lines() if (e[2], e[1]) not in played_nos and e[3] not in played_sounds]
        all_events.append((mp, played, unplayed))
    units = collections.defaultdict(collections.Counter)
    for mp, played, unplayed in all_events:
        for e in played + unplayed:
            if e[0] == "line":
                name = mp.subtitle(e[3])[0]
                if e[4]:
                    units[name][e[4]] += 1
    cast, icons_by_map = Cast(), {}
    for mp, played, unplayed in all_events:
        icons = icons_by_map[mp.name] = Icons(game, style, merge_objects(*campaign_objects, *[
            read_w3u(mp.arc.read(f)) for f in ("war3map.w3u", "war3mapSkin.w3u") if mp.arc.read(f)]),
            archives=(mp.arc, camp, loose))
        for e in played + unplayed:
            if e[0] != "line":
                continue
            name = mp.subtitle(e[3])[0] or "?"
            if name in cast.people:
                continue
            unit = units[name].most_common(1)[0][0] if units[name] else None
            generic = not re.search(r"[\s'’]", name)
            im, how = icons.lookup(unit, standard_ok=generic) if unit else (None, "no unit")
            if name in ICON_FIX:
                im, how = icons.image(ICON_FIX[name]), "ICON_FIX " + ICON_FIX[name]
            report.append("%-20s %-5s %s" % (name, unit, how))
            cast.register(name, name_class(unit, "orc", SIDE), webp(im) if im is not None else None)

    def blocks(mp, events, played):
        html_out, cur, cur_key = [], [], None

        def flush():
            if cur:
                kind, title, cin = cur_key
                html_out.append(block_html(cast, kind, None, cin, list(cur), played, title=title, level=3))
                cur.clear()

        for e in events:
            if e[0] == "caption":
                flush()
                html_out.append(caption_html(e[3]))
                continue
            key = scene_label(mp.category.get(e[2], e[2]))
            if key != cur_key:
                flush()
                cur_key = key
            name, text, sid, item = mp.subtitle(e[3])
            cur.append((name or "?", text, "%s · %s · string %s" % (e[3][7:], item, sid)))
        flush()
        return "\n".join(html_out)

    for mp, played, unplayed in all_events:
        small, big = titles.get(mp.name, ("", mp.name))
        parts.append('<section class="part"><h2 class="part-head"><small>%s</small>%s</h2>\n%s\n</section>' % (
            small, big, blocks(mp, played, True)))
        if unplayed:
            appendix.append('<h3 class="part-head" style="font-size:22px;margin-top:20px"><small>%s</small>%s</h3>\n%s' % (
                small, big, blocks(mp, [e for e in unplayed], False)))
    body = "\n".join(parts)
    n_unplayed = sum(len(u) for _, _, u in all_events)
    if appendix:
        body += ('\n<details class="appendix"><summary>Recorded but not played (%d lines)</summary>'
                 '<p>These lines have voice files and subtitles in the maps, but the mission scripts never play them. '
                 'Most belong to a longer version of a scene that the game replaces with a shorter one.</p>\n%s\n</details>'
                 % (n_unplayed, "\n".join(appendix)))
    quest_parts = []
    for mp, _, _ in all_events:
        cards = quest_cards(mp, cast, icons_by_map[mp.name], quests, lambda u: name_class(u, "orc", SIDE))
        if cards:
            small, big = titles.get(mp.name, ("", mp.name))
            quest_parts.append('<section class="part"><h2 class="part-head"><small>%s</small>%s</h2>\n%s\n</section>' % (
                small, big, "\n".join(cards)))
    tabs = None
    if quest_parts:
        tabs = [("quests", "Quests", '<p class="panel-intro">Everything the player reads about each quest, in the order '
                 'it appears: the quest log entry, on-screen messages, leaderboard rows and the lines spoken when the '
                 'quest starts and ends.</p>\n' + "\n".join(quest_parts))]
    first = titles.get(loaded[0].name, ("", loaded[0].name))
    eyebrow = " · ".join(titles.get(m.name, ("", m.name))[0] for m in loaded)
    doc = document(
        lang="en-US", title="%s Dialogue" % first[1], heading=camp_name, eyebrow=eyebrow, cast=cast, body=body,
        fragment=fragment, tabs=tabs,
        description="%s: %s. Every line of dialogue in story order." % (camp_name, ", ".join(
            "%s (%s)" % (titles.get(m.name, ("", m.name))[1], titles.get(m.name, ("", ""))[0]) for m in loaded)),
        notes=["Order follows the map scripts. Quest lines that depend on what the player does (wolves, the human "
               "scouts) can come earlier or later.",
               "Text from %s; icons from the campaign and the CSW asset pack. Warcraft III: Reforged campaign by LoreCraft "
               "Designs. Characters © Blizzard Entertainment." % os.path.basename(w3n)])
    return doc, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--w3n", default=r"D:\Games\WarCraft\CSW_Tides_Of_Darkness.w3n")
    ap.add_argument("--maps", nargs="+", required=True)
    ap.add_argument("--game", default=r"F:\Games\Warcraft III")
    ap.add_argument("--icons", choices=("de", "hd"), default="hd")
    ap.add_argument("--quests", nargs="*", default=None,
                    help="quest titles for the Quests tab (substring match); default all quests, empty = no tab")
    ap.add_argument("--fragment", action="store_true", help="no doctype/html/head/body (for publishing as an Artifact)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    doc, report = build(a.w3n, a.maps, a.game, a.icons, a.fragment, a.quests)
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CSW-ToD", "dialogue",
                                "_".join(os.path.splitext(m)[0] for m in a.maps) + ".html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w", encoding="utf-8", newline="\n").write(doc)
    print("wrote %s (%d KB)" % (os.path.normpath(out), len(doc.encode()) // 1024))
    print("speakers:\n  " + "\n  ".join(report))


if __name__ == "__main__":
    main()
