"""Shared parts of the one-file dialogue pages (fk_html.py, tod_html.py): object data, speaker icons, HTML renderer.

Page layout: header (eyebrow, title, line/character count, cast chips that highlight a speaker's lines, "Show IDs"),
then parts (one per map) made of scene blocks and captions, an optional <details> appendix, footer notes.
Dark-first theme with a light variant; Google Fonts with Cyrillic (Alegreya family) so translated pages look the same.
"""
import base64, html, io, os, re, struct

from PIL import Image

from casc import Casc

FACTION = {"h": "human", "u": "undead", "n": "neutral", "o": "orc", "e": "elf"}


def read_w3u(data):
    """war3map.w3u / war3mapSkin.w3u / .w3t (path or bytes) -> {id: {"base": original id, field: value}}."""
    d = open(data, "rb").read() if isinstance(data, str) else data
    p = 0

    def take(fmt):
        nonlocal p
        v = struct.unpack_from(fmt, d, p)[0]
        p += struct.calcsize(fmt)
        return v

    ver = take("<i")
    out = {}
    for table in range(2):
        if p >= len(d):
            break
        for _ in range(take("<i")):
            orig, new = take("4s").decode("latin1"), take("4s").decode("latin1")
            obj = out.setdefault(new if table else orig, {"base": orig})
            for _ in range(take("<i") if ver >= 3 else 1):
                if ver >= 3:
                    take("<i")
                for _ in range(take("<i")):
                    fid, typ = take("4s").decode("latin1"), take("<i")
                    if typ == 0:
                        val = take("<i")
                    elif typ in (1, 2):
                        val = take("<f")
                    else:
                        e = d.index(b"\0", p)
                        val, p = d[p:e].decode("utf-8", "replace"), e + 1
                    take("4s")
                    obj[fid] = val
    return out


class LooseFiles:
    """A folder used like an archive (case-insensitive `read(name)`), e.g. the game's _retail_ folder, where asset packs
    such as the CSW V2 pack put loose files (ReplaceableTextures\\CommandButtons\\BTNOrgrim.dds) for AllowLocalFiles."""

    def __init__(self, root, subdirs=("ReplaceableTextures",)):
        self.index = {}
        for sub in subdirs:
            for dirpath, _, files in os.walk(os.path.join(root, sub)):
                for f in files:
                    full = os.path.join(dirpath, f)
                    self.index[os.path.relpath(full, root).replace("/", "\\").lower()] = full

    def read(self, name):
        path = self.index.get(name.replace("/", "\\").lower())
        return open(path, "rb").read() if path else None


def scan_wtg(data):
    """war3map.wtg (Reforged, subversion 7) -> {trigger name: category name}, found by record shape, no ECA parsing.

    Trigger record: classifier u32 (8 GUI / 32 custom text), name, description (C strings), is_comment, id (0x03......),
    enabled, is_script, initially_off, run_on_init, parent id (0x02...... or 0). Category record: 4, id (0x02......), name,
    is_comment, open, parent."""
    out, cats = {}, {}
    if not data or data[:4] != b"WTG!":
        return out
    for m in re.finditer(rb"\x04\x00\x00\x00(...\x02)([^\x00]{1,200})\x00", data, re.S):
        cats[struct.unpack("<I", m.group(1))[0]] = m.group(2).decode("utf-8", "replace")
    for m in re.finditer(rb"(?:\x08|\x20)\x00\x00\x00([^\x00]{1,200})\x00([^\x00]{0,4000})\x00"
                         rb"([\x00\x01]\x00\x00\x00)(...\x03)(.{16})(...\x02|\x00\x00\x00\x00)", data, re.S):
        parent = struct.unpack("<I", m.group(6))[0]
        out.setdefault(m.group(1).decode("utf-8", "replace"), cats.get(parent))
    return out


def trigger_var(name):
    """World Editor trigger name -> gg_trg_ variable suffix."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


# ------------------------------------------------------------------------------------------------ quests (JASS & Lua)
QUEST_MSG = {"DISCOVERED": "Quest discovered", "UPDATED": "Quest updated", "COMPLETED": "Quest completed",
             "FAILED": "Quest failed", "REQUIREMENT": "Requirement", "MISSIONFAILED": "Mission failed",
             "ALWAYSHINT": "Hint", "HINT": "Hint", "SECRET": "Secret", "UNITACQUIRED": "Unit acquired",
             "UNITAVAILABLE": "Unit available", "ITEMACQUIRED": "Item acquired", "WARNING": "Warning"}
ARG = r'("[^"]*"|udg_\w+(?:\[\w+\])?)'                 # a string literal or a string variable (Lua campaigns)
UI_PATTERNS = [
    (r'QuestMessageBJ\([^,]+,\s*bj_QUESTMESSAGE_(\w+),\s*' + ARG + r'\)', lambda m: QUEST_MSG.get(m.group(1), m.group(1).title())),
    (r'Leaderboard\w*AddItem\w*BJ\([^,]+,\s*[^,]+,\s*' + ARG, lambda m: "Leaderboard"),
    (r'CreateLeaderboardBJ\([^,]+,\s*' + ARG + r'\)', lambda m: "Leaderboard title"),
    (r'QuestItemSetDescriptionBJ\((udg_\w+),\s*' + ARG + r'\)', lambda m: "Requirement text"),
    (r'QuestSetDescriptionBJ\((udg_\w+),\s*' + ARG + r'\)', lambda m: "Quest description"),
    (r'CreateTimerDialogBJ\([^,]+,\s*' + ARG + r'\)', lambda m: "Timer"),
    (r'Custom(?:Defeat|Victory)BJ\([^,]+,\s*' + ARG, lambda m: "Defeat"),
    (r'Display(?:Timed)?TextToForce\([^"]*"([^"]*)"\)', lambda m: "On-screen text"),
]


def ui_text(s):
    """A statement that shows quest-related text -> (label, string ref: TRIGSTR_n, literal or udg_ variable) or None."""
    for pat, label in UI_PATTERNS:
        m = re.search(pat, s)
        if m:
            ref = m.group(m.lastindex)
            return label(m), ref[1:-1] if ref.startswith('"') else ref
    return None


def quest_status(s, q, string):
    """Quest log changes of quest q in a statement -> text or None. string(ref) -> (id, text)."""
    item_vars = {it["var"]: it for it in q["items"]}
    m = re.search(r"Quest(Set|ItemSet)(Discovered|Completed|Failed)BJ\((udg_\w+),\s*true\)", s)
    if not m or (m.group(3) != q["var"] and m.group(3) not in item_vars):
        return None
    what = m.group(2).lower()
    if m.group(1) == "ItemSet":
        return "Requirement %s: %s" % (what, " ".join((string(item_vars[m.group(3)]["text"])[1] or "").split()))
    return {"discovered": "The quest appears in the quest log.", "completed": "The quest is marked as completed.",
            "failed": "The quest is marked as failed."}[what]


def quest_defs(lines):
    """[(no, statement)] in file order -> quests made with CreateQuestBJ (+ requirements from CreateQuestItemBJ)."""
    out = []
    unq = lambda a: a[1:-1] if a.startswith('"') else a
    for i, (no, s) in enumerate(lines):
        nxt = lines[i + 1][1] if i + 1 < len(lines) else ""
        m = re.search(r'CreateQuestBJ\((bj_QUESTTYPE_\w+),\s*%s,\s*%s,\s*%s\)' % (ARG, ARG, ARG), s)
        if m:
            var = re.match(r"(?:set\s+)?(udg_\w+)\s*=\s*GetLastCreatedQuestBJ", nxt)
            out.append({"var": var.group(1) if var else None, "type": m.group(1), "title": unq(m.group(2)),
                        "desc": unq(m.group(3)), "icon": unq(m.group(4)).replace("\\\\", "\\"), "items": [], "line": no})
            continue
        m = re.search(r'CreateQuestItemBJ\((udg_\w+),\s*%s\)' % ARG, s)
        if m:
            q = next((q for q in out if q["var"] == m.group(1)), None)
            var = re.match(r"(?:set\s+)?(udg_\w+)\s*=\s*GetLastCreatedQuestItemBJ", nxt)
            if q:
                q["items"].append({"var": var.group(1) if var else None, "text": unq(m.group(2))})
    return out


PHASES = [(r"discover(?:ed)?\b", "Quest starts"), (r"complete(?:d)?\b", "Quest completed"),
          (r"fail(?:ed|ure)?\b", "Quest failed"), (r"update", "Progress")]
PHASE_RANK = {"Quest starts": 0, "Progress": 1, "During the quest": 1, "Quest completed": 2, "Quest failed": 3}


def phase_name(trig):
    """Trigger name -> phase label ("Sewers SuppliesDiscovery" is not a start, "Fuel UpdateCompleted Q" is a completion)."""
    for pat, label in PHASES:
        if re.search(pat, trig, re.I):
            return label
    return "During the quest"


def quest_cards(mp, cast, icons, wanted, cls_of):
    """Quest cards of one map. mp: quests(), quest_steps(q) -> [(trigger, phase, events)], string(ref) -> (id, text),
    subtitle(snd) -> (name, text, string id, extra id). events: ("line", no, trig, snd, unit) / ("ui", no, trig, label,
    text, id) / ("status", no, trig, text). wanted: None = all, else title substrings. cls_of(unit) -> name CSS class."""
    cards = []
    for q in mp.quests():
        tsid, title = mp.string(q["title"])
        title = title or q["title"]
        if wanted is not None and not any(w.lower() in title.lower() for w in wanted):
            continue
        dsid, desc = mp.string(q["desc"])
        phases = []
        steps_in_order = sorted(mp.quest_steps(q), key=lambda p: PHASE_RANK.get(p[1], 1))   # stable: file order inside
        for trig, label, evs in steps_in_order:
            steps = []
            for e in evs:
                if e[0] == "line":
                    name, text, sid, extra = mp.subtitle(e[3])
                    name = name or "?"
                    cast.register(name, cls_of(e[4]), None)
                    entry = (name, text, "%s · %s · string %s" % (e[3][7:], extra, sid))
                    if steps and steps[-1][0] == "lines":
                        steps[-1][1].append(entry)
                    else:
                        steps.append(("lines", [entry]))
                elif e[0] == "ui":
                    steps.append(("msg", e[3], e[4], "string %s" % e[5] if e[5] else ""))
                else:
                    steps.append(("status", e[3]))
            phases.append((label, trig, steps))
        im = icons.image(q["icon"]) if q["icon"] else None
        item_ids = [mp.string(it["text"])[0] for it in q["items"]]
        cards.append(quest_html(cast, {
            "kind": "Optional quest" if "_OPT_" in q["type"] else "Main quest", "title": title, "desc": desc or "",
            "icon": webp(im) if im is not None else None,
            "items": [mp.string(it["text"])[1] or "" for it in q["items"]],
            "ids": "%s · trigger category \"%s\" · title string %s · description %s%s" % (
                q["var"], q["category"], tsid, dsid,
                (" · requirements " + ", ".join(str(i) for i in item_ids)) if item_ids else ""),
            "phases": phases}))
    return cards


def merge_objects(*tables):
    """Later tables override earlier ones field by field (campaign data first, then map, then skin files)."""
    out = {}
    for t in tables:
        for k, v in t.items():
            out.setdefault(k, {}).update(v)
    return out


class Icons:
    """Unit type -> command-button icon (PIL image).

    Order: custom icon (object data `uico`) from the given archives (map, campaign; `_HD.w3mod\\` and .dds/.blp variants),
    then the game's CASC; otherwise the standard icon of the unit (or its base unit) from Units/UnitSkin.txt.
    style "de": CASC _DE first (classic-style art at 256px), "hd": _HD first (Reforged art)."""

    _game_cache = {}

    def __init__(self, game, style="de", objects=None, archives=()):
        mods = {"de": ("_DE.w3mod:", "_HD.w3mod:", ""), "hd": ("_HD.w3mod:", "_DE.w3mod:", "")}[style]
        self.prefixes = ["War3.w3mod:" + m for m in mods]
        if game not in Icons._game_cache:          # one CASC read per game folder, shared by all maps of a page
            casc = Casc(game)
            skin = casc.get("War3.w3mod:Units/UnitSkin.txt").decode("utf-8", "replace")
            Icons._game_cache[game] = (casc, {p.lower(): p for p in casc.root()}, {
                m.group(1): m.group(2) for m in re.finditer(r"^\[(\w{4})\]\s*\n(.*?)(?=^\[|\Z)", skin, re.M | re.S)})
        self.casc, self.files, self.skin = Icons._game_cache[game]
        self.objects = objects or {}
        self.archives = list(archives)

    def standard_art(self, unit):
        for uid in (unit, self.objects.get(unit, {}).get("base")):
            sec = self.skin.get(uid or "")
            if sec:
                for field in ("Art:hd", "Art", r"Art:\w+"):   # Leonid (Hleo) has only Art:de / Art:sd style keys
                    m = re.search(r"^%s=(.+)$" % field, sec, re.M)
                    if m and m.group(1).strip():
                        return m.group(1).strip().split(",")[0]
        return None

    def image(self, art):
        stem = os.path.splitext(art.replace("/", "\\"))[0]
        for arc in self.archives:
            for pre in ("_HD.w3mod\\", ""):
                for ext in (".dds", ".blp", os.path.splitext(art)[1]):
                    data = arc.read(pre + stem + ext) if ext else None
                    if data:
                        im = Image.open(io.BytesIO(data))
                        im.load()
                        return im
        best = None
        for pre in self.prefixes:
            path = self.files.get((pre + stem.replace("\\", "/") + ".dds").lower())
            if path:
                im = Image.open(io.BytesIO(self.casc.get(path)))
                im.load()
                if best is None or im.width > best.width:
                    best = im
        return best

    def lookup(self, unit, standard_ok=True):
        """(image or None, note). standard_ok=False: a unit whose custom icon is missing gets None, not its base icon."""
        custom = self.objects.get(unit, {}).get("uico")
        if custom:
            im = self.image(custom)
            if im is not None:
                return im, "custom"
            if not standard_ok:
                return None, "custom icon missing: " + custom
        art = self.standard_art(unit)
        im = self.image(art) if art else None
        return im, ("standard" if im is not None else "no icon")


def webp(im, size=128):
    buf = io.BytesIO()
    im.convert("RGBA").resize((size, size), Image.LANCZOS).save(buf, "WEBP", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def initials(name):
    words = [w for w in re.split(r"[\s\-]+", name) if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def name_class(unit, hero_side, side=None):
    """CSS class for a speaker name: faction of the unit (first letter of its id, or side[unit]); heroes of hero_side gold."""
    u = unit or "n"
    fac = (side or {}).get(u) or FACTION.get(u[0].lower(), "neutral")
    return "hero" if u[0].isupper() and fac == hero_side else fac


def wc3_html(text):
    """WC3 text -> HTML: escape, |cAARRGGBB..|r -> highlighted span (gold, or green/red/grey by hue), |n and newlines
    -> <br>."""
    out, depth = [], 0
    for part in re.split(r"(\|c[0-9A-Fa-f]{8}|\|r|\|n|\n)", text):
        if re.fullmatch(r"\|c[0-9A-Fa-f]{8}", part):
            r, g, b = (int(part[i:i + 2], 16) for i in (4, 6, 8))
            hue = ("g" if g > r + 40 and g > b else "r" if r > g + 60 and r > b + 60
                   else "m" if max(r, g, b) - min(r, g, b) < 30 else "")
            out.append('<span class="wc%s">' % (" " + hue if hue else ""))
            depth += 1
        elif part == "|r":
            if depth:
                out.append("</span>")
                depth -= 1
        elif part in ("|n", "\n"):
            out.append("<br>")
        else:
            out.append(html.escape(part))
    return "".join(out) + "</span>" * depth


CSS = """
:root{--bg:#0e1013;--panel:#161a20;--panel2:#1b2028;--ink:#e9e3d5;--muted:#958e80;--gold:#d9b45f;--line:#2b3038;
--human:#93b8ee;--undead:#9fd27f;--neutral:#c4bcae;--orc:#e38f6a;--elf:#c6a2ef;--hero:#e7c46e;--frame:#6b5631;
--mono-orc:#5a2a1c;--mono-human:#1f3a63;--mono-undead:#27401f;--mono-other:#3a3a40;--mono-ink:#f1e2b8;color-scheme:dark}
@media (prefers-color-scheme:light){:root:not([data-theme="dark"]){--bg:#f5efe2;--panel:#fffaf0;--panel2:#fbf3e3;--ink:#2a241a;
--muted:#6e6553;--gold:#8a6414;--line:#e0d5bf;--human:#2d5d9c;--undead:#477526;--neutral:#7a5a28;--orc:#a2462a;--elf:#6a3fa0;
--hero:#8a6414;--frame:#8a6d3b;color-scheme:light}}
:root[data-theme="light"]{--bg:#f5efe2;--panel:#fffaf0;--panel2:#fbf3e3;--ink:#2a241a;--muted:#6e6553;--gold:#8a6414;--line:#e0d5bf;
--human:#2d5d9c;--undead:#477526;--neutral:#7a5a28;--orc:#a2462a;--elf:#6a3fa0;--hero:#8a6414;--frame:#8a6d3b;color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:400 18px/1.55 "Alegreya",Georgia,serif;-webkit-text-size-adjust:100%}
.wrap{max-width:780px;margin:0 auto;padding-block:48px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px))}
button:focus-visible,summary:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
@media (prefers-reduced-motion:reduce){.line{transition:none}}
header{margin-bottom:28px}
.eyebrow{font:500 15px/1.3 "Alegreya SC",Georgia,serif;letter-spacing:.08em;color:var(--gold);margin:0 0 6px}
h1{font:700 clamp(36px,7vw,54px)/1.05 "Alegreya SC",Georgia,serif;margin:0 0 10px;letter-spacing:.01em;text-wrap:balance}
.meta{color:var(--muted);font-size:16px;margin:0}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:22px 0 0}
.chip{display:inline-flex;align-items:center;gap:7px;padding:4px 10px 4px 4px;border:1px solid var(--line);border-radius:999px;
background:var(--panel);color:var(--ink);font:500 14px/1 "Alegreya Sans",system-ui,sans-serif;cursor:pointer}
.chip .icon{width:26px;height:26px;border-radius:50%;box-shadow:none;font-size:10px}
.chip small{color:var(--muted);font-size:12px}
.chip[aria-pressed="true"]{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold) inset}
.tools{margin-left:auto;display:flex;gap:8px}
.btn{padding:6px 12px;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--muted);
font:500 13px/1 "Alegreya Sans",system-ui,sans-serif;cursor:pointer}
.btn[aria-pressed="true"]{color:var(--gold);border-color:var(--gold)}
.part{margin-top:56px}
.part:first-of-type{margin-top:8px}
.part-head{margin:0 0 4px;font:700 clamp(26px,5vw,34px)/1.15 "Alegreya SC",Georgia,serif;text-wrap:balance}
.part-head small{display:block;font:500 14px/1.3 "Alegreya SC",Georgia,serif;letter-spacing:.08em;color:var(--gold);margin-bottom:4px}
.scene{margin:34px 0 0}
.scene-head{display:flex;align-items:center;gap:10px;font:500 15px/1 "Alegreya SC",Georgia,serif;letter-spacing:.08em;
color:var(--gold);margin:0 0 10px}
.scene-head::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}
.scene-head code{font:12px/1 ui-monospace,Consolas,monospace;color:var(--muted);letter-spacing:0}
.scene.cin .lines{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px 16px}
.caption{margin:30px 0 0;text-align:center;font:italic 400 17px/1.4 "Alegreya",Georgia,serif;color:var(--muted)}
.caption::before,.caption::after{content:"\\2014";margin:0 .6em;color:var(--line)}
.line{display:grid;grid-template-columns:56px minmax(0,1fr);gap:14px;padding:11px 0;transition:opacity .15s}
.scene:not(.cin) .line{padding:8px 0}
.line + .line{border-top:1px solid var(--line)}
.scene:not(.cin) .line + .line{border-top-color:transparent}
.icon{display:block;width:56px;height:56px;border-radius:5px;background:var(--panel2) center/cover no-repeat;
box-shadow:0 0 0 1px #000,0 0 0 2px var(--frame),0 3px 8px rgba(0,0,0,.35)}
.icon.mono{display:grid;place-items:center;color:var(--mono-ink);font:700 19px/1 "Alegreya SC",Georgia,serif;letter-spacing:.02em;
background:radial-gradient(circle at 35% 30%,rgba(255,255,255,.14),transparent 60%),var(--mono-other)}
.icon.mono.orc,.icon.mono.hero{background-color:var(--mono-orc)}
.icon.mono.human{background-color:var(--mono-human)}.icon.mono.undead{background-color:var(--mono-undead)}
.name{font:600 15px/1.2 "Alegreya Sans",system-ui,sans-serif;letter-spacing:.02em;margin:2px 0 3px}
.name.hero{color:var(--hero)}.name.human{color:var(--human)}.name.undead{color:var(--undead)}
.name.neutral{color:var(--neutral)}.name.orc{color:var(--orc)}.name.elf{color:var(--elf)}
.text{margin:0}
.wc{color:var(--gold);font-weight:600}.wc.g{color:var(--undead)}.wc.r{color:var(--orc)}.wc.m{color:var(--muted)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:0 0 8px}
.tab{appearance:none;background:none;border:0;border-bottom:2px solid transparent;margin-bottom:-1px;padding:10px 14px;
color:var(--muted);font:500 17px/1 "Alegreya SC",Georgia,serif;letter-spacing:.06em;cursor:pointer}
.tab:hover{color:var(--ink)}
.tab[aria-selected="true"]{color:var(--gold);border-bottom-color:var(--gold)}
.tab:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.panel-intro{color:var(--muted);font-size:16px;margin:18px 0 0;max-width:62ch}
.panel-intro + .part{margin-top:32px}
.quest{margin:24px 0 0;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
.quest-head{display:grid;grid-template-columns:64px minmax(0,1fr);gap:16px;padding:18px 18px 16px;border-bottom:1px solid var(--line)}
.quest-head .icon{width:64px;height:64px}
.badge{display:inline-block;font:600 11px/1 "Alegreya Sans",system-ui,sans-serif;letter-spacing:.1em;text-transform:uppercase;
padding:4px 7px;border-radius:4px;border:1px solid var(--line);color:var(--muted)}
.badge.main{color:var(--gold);border-color:var(--gold)}
.quest h3{font:700 26px/1.15 "Alegreya SC",Georgia,serif;margin:8px 0 6px}
.quest-desc{margin:0 0 10px}
.req-title{margin:0;font:600 12px/1.4 "Alegreya Sans",system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.req{list-style:none;padding:0;margin:4px 0 0}
.req li::before{content:"\\2013";color:var(--gold);margin-right:.5em}
.phase{padding:4px 18px 12px}
.phase + .phase{border-top:1px solid var(--line)}
.phase-head{margin:14px 0 4px;font:500 15px/1.2 "Alegreya SC",Georgia,serif;letter-spacing:.08em;color:var(--gold)}
.step{display:grid;grid-template-columns:132px minmax(0,1fr);gap:14px;padding:9px 0}
.step + .step{border-top:1px dashed var(--line)}
.step-label{font:600 11px/1.6 "Alegreya Sans",system-ui,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding-top:2px}
.step .line{padding:6px 0;border:0}
.step .line:first-child{padding-top:0}
.msg{margin:0;font:500 16px/1.5 "Alegreya Sans",system-ui,sans-serif}
.step.status .msg{color:var(--muted);font-style:italic;font-weight:400}
.idc{display:none;font:12px/1 ui-monospace,Consolas,monospace;color:var(--muted);letter-spacing:0;margin-left:8px}
body.show-ids .idc{display:inline}
.text.empty{color:var(--muted);font-style:italic}
.ids{display:none;margin-top:4px;font:12px/1.3 ui-monospace,Consolas,monospace;color:var(--muted);overflow-wrap:anywhere}
body.show-ids .ids{display:block}
body.focus .line:not(.on){opacity:.28}
.appendix{margin-top:48px;border:1px solid var(--line);border-radius:10px;padding:4px 16px 16px;background:var(--panel)}
.appendix summary{cursor:pointer;padding:12px 0;font:500 16px/1.3 "Alegreya SC",Georgia,serif;letter-spacing:.06em;color:var(--gold)}
.appendix > p{margin:0 0 4px;color:var(--muted);font-size:16px}
.appendix .scene.cin .lines{background:var(--bg)}
footer{margin-top:48px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:15px}
footer p{margin:0 0 8px}
@media (max-width:560px){body{font-size:17px}.wrap{padding-top:32px}.line{grid-template-columns:44px minmax(0,1fr);gap:12px}
.icon{width:44px;height:44px}.icon.mono{font-size:16px}.scene.cin .lines{padding:4px 12px}.tools{margin-left:0;width:100%}
.step{grid-template-columns:minmax(0,1fr);gap:4px}.quest-head{grid-template-columns:48px minmax(0,1fr);gap:12px;padding:14px}
.quest-head .icon{width:48px;height:48px}.phase{padding:4px 14px 10px}}
"""

JS = """
(function(){
  var body=document.body,chips=document.querySelectorAll('.chip'),lines=document.querySelectorAll('.line');
  chips.forEach(function(c){c.addEventListener('click',function(){
    var on=c.getAttribute('aria-pressed')!=='true';
    chips.forEach(function(o){o.setAttribute('aria-pressed','false')});
    body.classList.toggle('focus',on);
    lines.forEach(function(l){l.classList.toggle('on',on&&l.dataset.s===c.dataset.s)});
    var calm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
    if(on){c.setAttribute('aria-pressed','true');
      var f=[].filter.call(document.querySelectorAll('.line.on'),function(l){return l.offsetParent!==null})[0];
      if(f)f.scrollIntoView({behavior:calm?'auto':'smooth',block:'center'})}
  })});
  var tabs=document.querySelectorAll('.tab');
  function show(id){
    if(![].some.call(tabs,function(t){return t.id==='tab-'+id}))id=tabs[0].id.slice(4);
    tabs.forEach(function(t){var on=t.id==='tab-'+id;t.setAttribute('aria-selected',String(on));t.tabIndex=on?0:-1;
      document.getElementById(t.getAttribute('aria-controls')).hidden=!on});
  }
  tabs.forEach(function(t){t.addEventListener('click',function(){var id=t.id.slice(4);
    if(location.hash.slice(1)!==id)location.hash=id;else show(id)})});
  window.addEventListener('hashchange',function(){show(location.hash.slice(1))});
  if(tabs.length)show(location.hash.slice(1));
  var ids=document.getElementById('ids');
  ids.addEventListener('click',function(){var on=ids.getAttribute('aria-pressed')!=='true';
    ids.setAttribute('aria-pressed',String(on));body.classList.toggle('show-ids',on)});
})();
"""


class Cast:
    """Speakers in registration order (register played lines first): slug, count of played lines, icon (webp base64)
    or None for a monogram, name class."""

    def __init__(self, registry=None):
        self.registry = registry if registry is not None else {}   # shared by the missions of one page: slug, icon
        self.people = {}

    def register(self, name, cls, icon):
        g = self.registry.get(name)
        if g is None:
            g = self.registry[name] = {"slug": "s%d" % len(self.registry), "cls": cls, "icon": icon}
        if name not in self.people:
            self.people[name] = {"slug": g["slug"], "count": 0, "cls": g["cls"], "icon": g["icon"]}
        return self.people[name]

    def icon_span(self, name):
        p = self.people[name]
        if p["icon"]:
            return '<span class="icon i-%s" aria-hidden="true"></span>' % p["slug"]
        return '<span class="icon mono %s" aria-hidden="true">%s</span>' % (p["cls"], html.escape(initials(name)))


def line_html(cast, name, text, ids, played=True):
    p = cast.people[name]
    if played:
        p["count"] += 1
    return ('<div class="line" data-s="%s">%s<div><div class="name %s">%s</div><p class="text%s">%s</p>'
            '<div class="ids">%s</div></div></div>' % (
                p["slug"], cast.icon_span(name), p["cls"], html.escape(name), "" if text else " empty",
                wc3_html(text) if text else "(no text in this language)", html.escape(ids)))


def block_html(cast, kind, code, cin, lines, played=True, title=None, level=2):
    """lines: [(name, text, ids)]. Head: kind (· title) (code)."""
    head = html.escape(kind) + (" · " + html.escape(title) if title else "") + (
        " <code>%s</code>" % html.escape(code) if code else "")
    rows = "".join(line_html(cast, n, t, i, played) for n, t, i in lines)
    return '<section class="scene%s"><h%d class="scene-head">%s</h%d><div class="lines">%s</div></section>' % (
        " cin" if cin else "", level, head, level, rows)


def caption_html(text):
    return '<p class="caption">%s</p>' % wc3_html(text)


def quest_html(cast, q):
    """One quest card. q: kind ("Main quest"/"Optional quest"), title, desc, icon (webp base64 or None), items [text],
    ids, phases [(label, ids, steps)]; step = ("lines", [(name, text, ids)]) / ("msg", label, text, ids) / ("status", text)."""
    esc = html.escape
    icon = ('<span class="icon" aria-hidden="true" style="background-image:url(data:image/webp;base64,%s)"></span>' % q["icon"]
            if q["icon"] else '<span class="icon mono" aria-hidden="true">%s</span>' % esc(initials(q["title"])))
    reqs = "".join("<li>%s</li>" % wc3_html(t) for t in q["items"])
    head = ('<div class="quest-head">%s<div><span class="badge%s">%s</span><h3>%s</h3><p class="quest-desc">%s</p>%s'
            '<div class="ids">%s</div></div></div>' % (
                icon, " main" if q["kind"].startswith("Main") else "", esc(q["kind"]), wc3_html(q["title"]),
                wc3_html(q["desc"]), ('<p class="req-title">Requirements</p><ul class="req">%s</ul>' % reqs) if reqs else "",
                esc(q["ids"])))
    phases = []
    for label, ids, steps in q["phases"]:
        rows = []
        for st in steps:
            if st[0] == "lines":
                body = "".join(line_html(cast, n, t, i, played=False) for n, t, i in st[1])
                rows.append('<div class="step"><div class="step-label">Dialogue</div><div>%s</div></div>' % body)
            elif st[0] == "msg":
                rows.append('<div class="step"><div class="step-label">%s</div><div><p class="msg">%s</p>'
                            '<div class="ids">%s</div></div></div>' % (esc(st[1]), wc3_html(st[2]), esc(st[3])))
            else:
                rows.append('<div class="step status"><div class="step-label">Quest log</div><div><p class="msg">%s</p>'
                            '</div></div>' % wc3_html(st[1]))
        phases.append('<div class="phase"><h4 class="phase-head">%s<span class="idc">%s</span></h4>%s</div>' % (
            esc(label), esc(ids), "".join(rows)))
    return '<article class="quest">%s%s</article>' % (head, "".join(phases))


CAMPAIGN_CSS = """
.view[hidden]{display:none}
.intro{color:var(--muted);font-size:17px;margin:12px 0 0;max-width:62ch}
.missions{display:grid;gap:12px;margin:28px 0 0;padding:0;list-style:none}
.mission-link{display:grid;grid-template-columns:76px minmax(0,1fr) auto;gap:16px;align-items:center;padding:16px 18px;
border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--ink);text-decoration:none}
.mission-link:hover{border-color:var(--gold)}
.mission-link:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.mission-no{font:700 30px/1 "Alegreya SC",Georgia,serif;color:var(--gold);text-align:center}
.mission-no small{display:block;font:600 10px/1.2 "Alegreya Sans",system-ui,sans-serif;letter-spacing:.12em;
text-transform:uppercase;color:var(--muted);margin-top:6px}
.mission-title{display:block;font:700 24px/1.15 "Alegreya SC",Georgia,serif;text-wrap:balance}
.mission-stats{display:block;color:var(--muted);font-size:15px;margin-top:3px}
.mission-cast{display:flex;flex-wrap:wrap;gap:4px;margin-top:9px}
.mission-cast .icon{width:26px;height:26px;border-radius:50%;box-shadow:0 0 0 1px var(--frame);font-size:10px}
.arrow{color:var(--muted);font:400 26px/1 Georgia,serif}
.hero-link{grid-template-columns:56px minmax(0,1fr) auto}
.hero-link .icon{width:56px;height:56px}
.crumbs{display:flex;justify-content:space-between;align-items:center;gap:8px 16px;flex-wrap:wrap;margin:0 0 26px;
font:500 15px/1.3 "Alegreya Sans",system-ui,sans-serif}
.crumbs a,.pager a{color:var(--gold);text-decoration:none}
.crumbs a:hover,.pager a:hover{text-decoration:underline}
.crumbs .steps{display:flex;gap:16px}
.pager{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:44px;padding-top:18px;
border-top:1px solid var(--line);font:500 16px/1.3 "Alegreya Sans",system-ui,sans-serif}
.pager span{display:block;color:var(--muted);font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.pager .next{text-align:right;margin-left:auto}
.tab small{font:600 12px/1 "Alegreya Sans",system-ui,sans-serif;color:var(--muted);margin-left:5px}
.empty-note{color:var(--muted);font-style:italic;margin:24px 0 0}
@media (max-width:560px){.mission-link{grid-template-columns:52px minmax(0,1fr);gap:12px;padding:14px}.arrow{display:none}
.mission-no{font-size:24px}.mission-title{font-size:21px}.hero-link .icon{width:48px;height:48px}}
"""

CAMPAIGN_JS = """
(function(){
  var body=document.body,views=[].slice.call(document.querySelectorAll('.view')),current=null;
  function route(){
    var h=location.hash.slice(1),tab='dialogue',slug=h;
    if(/-quests$/.test(h)){tab='quests';slug=h.slice(0,-7)}
    var view=views.filter(function(v){return v.dataset.slug===slug})[0]||views[0];
    views.forEach(function(v){v.hidden=v!==view});
    view.querySelectorAll('.tab').forEach(function(t){var on=t.dataset.tab===tab;
      t.setAttribute('aria-selected',String(on));t.tabIndex=on?0:-1;
      document.getElementById(t.getAttribute('aria-controls')).hidden=!on});
    if(view!==current){window.scrollTo(0,0);current=view;
      body.classList.remove('focus');document.querySelectorAll('.chip').forEach(function(c){c.setAttribute('aria-pressed','false')})}
  }
  document.querySelectorAll('.tab').forEach(function(t){t.addEventListener('click',function(){
    location.hash=t.dataset.view+(t.dataset.tab==='quests'?'-quests':'')})});
  document.querySelectorAll('.chip').forEach(function(c){c.addEventListener('click',function(){
    var view=c.closest('.view'),on=c.getAttribute('aria-pressed')!=='true';
    view.querySelectorAll('.chip').forEach(function(o){o.setAttribute('aria-pressed','false')});
    body.classList.toggle('focus',on);
    view.querySelectorAll('.line').forEach(function(l){l.classList.toggle('on',on&&l.dataset.s===c.dataset.s)});
    var calm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
    if(on){c.setAttribute('aria-pressed','true');
      var f=[].filter.call(view.querySelectorAll('.line.on'),function(l){return l.offsetParent!==null})[0];
      if(f)f.scrollIntoView({behavior:calm?'auto':'smooth',block:'center'})}
  })});
  var toggles=document.querySelectorAll('.ids-toggle');
  toggles.forEach(function(b){b.addEventListener('click',function(){var on=b.getAttribute('aria-pressed')!=='true';
    toggles.forEach(function(o){o.setAttribute('aria-pressed',String(on))});body.classList.toggle('show-ids',on)})});
  window.addEventListener('hashchange',route);
  route();
})();
"""


def hero_views(heroes):
    """(home list items, views) for heroes: [{"slug", "name", "title", "icon" (webp b64), "cards": [html], "count",
    "note"}]."""
    esc = html.escape
    items, views = [], []
    for h in heroes:
        face = ('<span class="icon" aria-hidden="true" style="background-image:url(data:image/webp;base64,%s)"></span>'
                % h["icon"]) if h["icon"] else '<span class="icon mono" aria-hidden="true">%s</span>' % esc(initials(h["name"]))
        items.append('<li><a class="mission-link hero-link" href="#%s">%s<span><span class="mission-title">%s</span>'
                     '<span class="mission-stats">%s · %d texts</span></span><span class="arrow" aria-hidden="true">&rsaquo;'
                     '</span></a></li>' % (h["slug"], face, esc(h["name"]), esc(h["title"]), h["count"]))
        views.append(
            '<section class="view" data-slug="%s" hidden>\n<nav class="crumbs"><a href="#">&lsaquo; All missions</a></nav>\n'
            '<header><p class="eyebrow">Hero</p><h1>%s</h1><p class="meta">%s · %d texts</p>\n<div class="bar">'
            '<div class="tools"><button class="btn ids-toggle" type="button" aria-pressed="false">Show IDs</button></div>'
            '</div></header>\n<p class="panel-intro">%s</p>\n%s\n</section>' % (
                h["slug"], esc(h["name"]), esc(h["title"]), h["count"], esc(h["note"]), "\n".join(h["cards"])))
    return items, views


def campaign_document(*, lang, title, eyebrow, heading, intro, description, missions, notes, fragment=False, heroes=()):
    """Campaign page: a home view listing the missions, one view per mission with Dialogue / Quests tabs.
    missions: [{"slug", "label" (e.g. "Mission 1"), "number", "title", "cast": Cast, "dialogue": html,
    "quests": html or "", "quest_count": int}]. Views follow location.hash: "" home, "<slug>", "<slug>-quests"."""
    esc = html.escape
    registry = missions[0]["cast"].registry if missions else {}
    icon_css = "\n".join(".i-%s{background-image:url(data:image/webp;base64,%s)}" % (p["slug"], p["icon"])
                         for p in registry.values() if p["icon"])
    items, views = [], []
    for i, m in enumerate(missions):
        cast = m["cast"]
        lines = sum(p["count"] for p in cast.people.values())
        people = [n for n, p in cast.people.items() if p["count"]]
        stats = "%d lines · %d characters · %d quests" % (lines, len(people), m["quest_count"])
        faces = "".join(cast.icon_span(n) for n in sorted(people, key=lambda n: -cast.people[n]["count"])[:8])
        items.append('<li><a class="mission-link" href="#%s"><span class="mission-no">%s<small>%s</small></span>'
                     '<span><span class="mission-title">%s</span><span class="mission-stats">%s</span>'
                     '<span class="mission-cast">%s</span></span><span class="arrow" aria-hidden="true">&rsaquo;</span></a></li>'
                     % (m["slug"], esc(str(m["number"])), esc(m["label"]), esc(m["title"]), stats, faces))
        chips = "".join('<button class="chip" type="button" aria-pressed="false" data-s="%s">%s%s <small>%d</small></button>'
                        % (p["slug"], cast.icon_span(n), esc(n), p["count"]) for n, p in cast.people.items() if p["count"])
        prev = missions[i - 1] if i else None
        nxt = missions[i + 1] if i + 1 < len(missions) else None
        link = lambda x, cls, word: ('<a class="%s" href="#%s"><span>%s</span>%s · %s</a>' % (
            cls, x["slug"], word, esc(x["label"]), esc(x["title"]))) if x else ""
        s = m["slug"]
        tabs = ('<div class="tabs" role="tablist">'
                '<button class="tab" role="tab" type="button" id="tab-%s-dialogue" aria-controls="panel-%s-dialogue" '
                'data-view="%s" data-tab="dialogue" aria-selected="true">Dialogue<small>%d</small></button>'
                '<button class="tab" role="tab" type="button" id="tab-%s-quests" aria-controls="panel-%s-quests" '
                'data-view="%s" data-tab="quests" aria-selected="false">Quests<small>%d</small></button></div>'
                % (s, s, s, lines, s, s, s, m["quest_count"]))
        views.append(
            '<section class="view" data-slug="%s" hidden>\n'
            '<nav class="crumbs" aria-label="Missions"><a href="#">&lsaquo; All missions</a><span class="steps">%s%s</span></nav>\n'
            '<header><p class="eyebrow">%s</p><h1>%s</h1><p class="meta">%s</p>\n'
            '<div class="bar">%s<div class="tools"><button class="btn ids-toggle" type="button" aria-pressed="false">'
            'Show IDs</button></div></div></header>\n%s\n'
            '<section class="panel" id="panel-%s-dialogue" role="tabpanel" aria-labelledby="tab-%s-dialogue">\n%s\n</section>\n'
            '<section class="panel" id="panel-%s-quests" role="tabpanel" aria-labelledby="tab-%s-quests" hidden>\n%s\n</section>\n'
            '<nav class="pager" aria-label="Previous and next mission">%s%s</nav>\n</section>' % (
                s, ('<a href="#%s">&lsaquo; %s</a>' % (prev["slug"], esc(prev["label"]))) if prev else "",
                ('<a href="#%s">%s &rsaquo;</a>' % (nxt["slug"], esc(nxt["label"]))) if nxt else "",
                esc(m["label"]), esc(m["title"]), stats, chips, tabs, s, s, m["dialogue"], s, s,
                m["quests"] or '<p class="empty-note">This mission has no quests.</p>',
                link(prev, "prev", "Previous"), link(nxt, "next", "Next")))
    hero_items, hero_sections = hero_views(heroes)
    views += hero_sections
    home = ('<section class="view" data-slug="">\n<header><p class="eyebrow">%s</p><h1>%s</h1><p class="intro">%s</p></header>\n'
            '<ol class="missions">%s</ol>\n%s</section>' % (
                esc(eyebrow), esc(heading), esc(intro), "".join(items),
                ('<h2 class="part-head" style="margin-top:44px">Heroes</h2><ul class="missions">%s</ul>\n' % "".join(hero_items))
                if hero_items else ""))
    head = """<title>%s</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="description" content="%s">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Alegreya:ital,wght@0,400;0,600;1,400&family=Alegreya+SC:wght@500;700&family=Alegreya+Sans:wght@500;600&display=swap" rel="stylesheet">
<style>%s
%s
%s</style>
""" % (esc(title), esc(description), CSS.strip(), CAMPAIGN_CSS.strip(), icon_css)
    body = '<div class="wrap">\n%s\n%s\n<footer>\n%s\n</footer>\n</div>\n<script>%s</script>\n' % (
        home, "\n".join(views), "\n".join("<p>%s</p>" % n for n in notes), CAMPAIGN_JS.strip())
    if fragment:
        return head + body
    return ('<!doctype html>\n<html lang="%s">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n%s</head>\n<body>\n%s'
            '</body>\n</html>\n' % (lang, head, body))


def document(*, lang, title, heading, description, eyebrow, cast, body, notes, fragment=False, tabs=None):
    """Whole page. title: browser tab / gallery name, heading: the h1. body: HTML of parts/blocks. cast: Cast.
    tabs: extra [(slug, label, html)] -> a tab bar with "Dialogue" (body) first; the open tab follows location.hash."""
    esc = html.escape
    if tabs:
        tabs = [("dialogue", "Dialogue", body)] + list(tabs)
        body = ('<div class="tabs" role="tablist">%s</div>\n%s' % (
            "".join('<button class="tab" role="tab" type="button" id="tab-%s" aria-controls="panel-%s" aria-selected="%s">'
                    '%s</button>' % (s, s, "true" if i == 0 else "false", esc(label)) for i, (s, label, _) in enumerate(tabs)),
            "\n".join('<section class="panel" id="panel-%s" role="tabpanel" aria-labelledby="tab-%s"%s>\n%s\n</section>'
                      % (s, s, "" if i == 0 else " hidden", h) for i, (s, _, h) in enumerate(tabs))))
    count = sum(p["count"] for p in cast.people.values())
    icon_css = "\n".join(".i-%s{background-image:url(data:image/webp;base64,%s)}" % (p["slug"], p["icon"])
                         for p in cast.people.values() if p["icon"])
    chips = "".join('<button class="chip" type="button" aria-pressed="false" data-s="%s">%s%s <small>%d</small></button>'
                    % (p["slug"], cast.icon_span(n), esc(n), p["count"]) for n, p in cast.people.items() if p["count"])
    head = """<title>%s</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="description" content="%s">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Alegreya:ital,wght@0,400;0,600;1,400&family=Alegreya+SC:wght@500;700&family=Alegreya+Sans:wght@500;600&display=swap" rel="stylesheet">
<style>%s
%s</style>
""" % (esc(title), esc(description), CSS.strip(), icon_css)
    body = """<div class="wrap">
<header>
<p class="eyebrow">%s</p>
<h1>%s</h1>
<p class="meta">%d lines · %d characters</p>
<div class="bar">%s<div class="tools"><button class="btn" id="ids" type="button" aria-pressed="false">Show IDs</button></div></div>
</header>
<main>
%s
</main>
<footer>
%s
</footer>
</div>
<script>%s</script>
""" % (eyebrow, esc(heading), count, sum(1 for p in cast.people.values() if p["count"]), chips, body,
       "\n".join("<p>%s</p>" % n for n in notes), JS.strip())
    if fragment:  # for the Artifact tool, which adds doctype/html/head/body itself
        return head + body
    return ('<!doctype html>\n<html lang="%s">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n%s</head>\n<body>\n%s'
            '</body>\n</html>\n' % (lang, head, body))
