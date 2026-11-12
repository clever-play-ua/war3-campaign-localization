"""Forsaken Kingdom: build loadable plain .w3x maps + UI/CampaignInfoDLC01.txt for local testing.

    python tools/fk_build.py <decrypted_dir> <lua_dir> <out_dir> [--maps HumanRE01 ...] [--tag "UA TEST"]
                             [--cache-prefix UA_TEST_] [--install "F:/Games/Warcraft III/_retail_"]

<decrypted_dir>: `<Map>.files/` folders from w3xd-toolkit; <lua_dir>: `<Map>.lua` decompiled from war3map.luac
(`python -m luac53 decompile`). For every map:
  * every war3map.wts (English + 11 `_Locales`) gets "[<tag> in <locale>] " in front of each story string (strings
    without a `//` editor comment in the English file, minus map name/author and speaker names) when --tag is given;
  * war3map.lua replaces war3map.luac (the game refuses compiled Lua outside .w3xd maps);
  * level changes point to the local maps: SetNextLevelBJ paths, the UndeadRE01 hub (udg_ZoneMapPath/udg_ZoneMapExt)
    and the UndeadRE02 hub (FK_TRANSITION_CONFIG.Save MapDirectory/MapExtension);
  * game cache names get --cache-prefix so test runs never mix with the original campaign progress.
CampaignInfoDLC01.txt (read from the game's CASC) gets the menu missions of the built maps redirected.
Maps are written as <out_dir>/CampaignUA/ForsakenKingdom/<Map>_ukUA.w3x, the menu file as <out_dir>/UI/CampaignInfoDLC01.txt.
"""
import argparse, glob, json, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mpq_build  # noqa: E402

GAME = "F:/Games/Warcraft III"
LOCAL_DIR = "CampaignUA\\ForsakenKingdom\\"
SUFFIX = "_ukUA.w3x"
ALL_MAPS = ["HumanRE01", "HumanRE02", "HumanRE02Interlude", "HumanRE03", "HumanRE04",
            "UndeadRE01", "UndeadRE01_02", "UndeadRE01_03", "UndeadRE01_04", "UndeadRE01_05", "UndeadRE01_06",
            "UndeadRE02", "UndeadRE02_01", "UndeadRE02_02", "UndeadRE02_03", "UndeadRE02_04", "UndeadRE02_06",
            "UndeadRE03Interlude", "UndeadRE03a", "UndeadRE03b"]
CANON = {m.lower(): m for m in ALL_MAPS}
HUMAN = ALL_MAPS[:5]

WTS = re.compile(r"STRING (\d+)(\r?\n)(//[^\r\n]*\r?\n)?\{\r?\n(.*?)(\r?\n\})", re.S)
BS2 = r"\\\\"   # regex for the two characters \\ as they appear inside a Lua string literal


def story_ids(files):
    base = open(os.path.join(files, "war3map.wts"), encoding="utf-8-sig").read()
    conv = json.load(open(os.path.join(files, "conversation.json"), encoding="utf-8"))["conversation"]
    speakers = {e.get("speakerNameId") for v in conv.values() for e in v}
    skip = {96, 97, 99} | speakers
    return {int(m.group(1)) for m in WTS.finditer(base) if not m.group(3) and m.group(4).strip()} - skip


def tagged_wts(files, tag):
    """{member name: bytes} for all war3map.wts of a map, story strings prefixed with the tag."""
    ids = story_ids(files)
    out = {}
    paths = [("enUS", "war3map.wts")] + [
        (os.path.basename(os.path.dirname(p)).split(".")[0], os.path.relpath(p, files).replace("/", "\\"))
        for p in glob.glob(os.path.join(files, "_Locales", "*", "war3map.wts"))]
    for loc, rel in paths:
        raw = open(os.path.join(files, rel.replace("\\", os.sep)), "rb").read()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        def f(m):
            if int(m.group(1)) not in ids or not m.group(4).strip():
                return m.group(0)
            nl = m.group(2)
            return "STRING %s%s%s{%s[%s in %s] %s%s" % (m.group(1), nl, m.group(3) or "", nl, tag, loc, m.group(4), m.group(5))
        text = WTS.sub(f, text)
        out[rel] = (("\ufeff" if bom else "") + text).encode("utf-8")
    return out


def patch_lua(src, built, cache_prefix):
    """Returns (patched source, report dict)."""
    rep = {}

    def count(name, n):
        rep[name] = rep.get(name, 0) + n

    # SetNextLevelBJ("Campaign\\ForsakenKingdom\\X.w3xd") and any other literal full map path
    def full(m):
        canon = CANON.get(m.group(1).lower())
        if canon not in built:
            count("path_left_original:" + m.group(1), 1)
            return m.group(0)
        count("map_path", 1)
        return '"' + LOCAL_DIR.replace("\\", "\\\\") + canon + SUFFIX + '"'
    src = re.sub(r'"Campaign' + BS2 + r'forsakenkingdom' + BS2 + r'(\w+)\.w3xd"', full, src, flags=re.I)

    # hub map directory + extension (UndeadRE01: udg_ZoneMapPath/udg_ZoneMapExt; UndeadRE02: MapDirectory/MapExtension)
    local_dir_lit = '"' + LOCAL_DIR.replace("\\", "\\\\") + '"'
    src, n = re.subn(r'(udg_ZoneMapPath = |MapDirectory = )"Campaign' + BS2 + r'ForsakenKingdom' + BS2 + '"',
                     lambda m: m.group(1) + local_dir_lit, src, flags=re.I)
    count("hub_dir", n)
    src, n = re.subn(r'(udg_ZoneMapExt = |MapExtension = )"\.w3xd"', lambda m: m.group(1) + '"' + SUFFIX + '"', src)
    count("hub_ext", n)

    if cache_prefix:
        src, n = re.subn(r'((?:udg_Cache_HumanRE|udg_Cache_CampaignName) = )"HumanRE"',
                         lambda m: m.group(1) + '"' + cache_prefix + 'HumanRE"', src)
        count("cache_HumanRE", n)
        src, n = re.subn(r'"Campaigns\.w3v"', '"' + cache_prefix + 'Campaigns.w3v"', src)
        count("cache_Campaigns.w3v", n)
        src, n = re.subn(r'"(Blizzard' + BS2 + r')Zones"', lambda m: '"' + m.group(1) + cache_prefix + 'Zones"', src)
        count("zone_save_folder", n)
        src, n = re.subn(r'(udg_ZoneFolder = udg_BlizzardFolder \.\. )"Zones"',
                         lambda m: m.group(1) + '"' + cache_prefix + 'Zones"', src)
        count("zone_save_folder", n)
    return src, rep


def campaign_info(built):
    from casc import Casc
    txt = Casc(GAME).get("War3.w3mod:UI/CampaignInfoDLC01.txt").decode("utf-8")
    def f(m):
        canon = CANON.get(m.group(1).lower())
        if canon in built:
            return '"' + LOCAL_DIR.replace("\\", "/") + canon + SUFFIX + '"'
        return m.group(0)
    return re.sub(r'"Campaign/ForsakenKingdom/(\w+)\.w3xd"', f, txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decrypted")
    ap.add_argument("lua")
    ap.add_argument("out")
    ap.add_argument("--maps", nargs="*", default=HUMAN)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--cache-prefix", default="")
    ap.add_argument("--install", default=None, help="_retail_ folder to copy the result into")
    a = ap.parse_args()
    built = set(a.maps)
    mapdir = os.path.join(a.out, "CampaignUA", "ForsakenKingdom")
    os.makedirs(mapdir, exist_ok=True)
    for m in a.maps:
        files = os.path.join(a.decrypted, m + ".files")
        lua, rep = patch_lua(open(os.path.join(a.lua, m + ".lua"), encoding="utf-8").read(), built, a.cache_prefix)
        put = {"war3map.lua": lua.encode("utf-8")}
        if a.tag:
            put.update(tagged_wts(files, a.tag))
        members = mpq_build.collect(files, drop=["war3map.luac"], put=put)
        data = mpq_build.build(members, m)
        open(os.path.join(mapdir, m + SUFFIX.replace(".w3x", "") + ".w3x"), "wb").write(data)
        print("%-20s %8d bytes  %s" % (m, len(data), rep))
    ui = os.path.join(a.out, "UI")
    os.makedirs(ui, exist_ok=True)
    open(os.path.join(ui, "CampaignInfoDLC01.txt"), "w", encoding="utf-8", newline="").write(campaign_info(built))
    if a.install:
        for p in glob.glob(os.path.join(mapdir, "*.w3x")):
            dst = os.path.join(a.install, "CampaignUA", "ForsakenKingdom")
            os.makedirs(dst, exist_ok=True)
            shutil.copyfile(p, os.path.join(dst, os.path.basename(p)))
        shutil.copyfile(os.path.join(ui, "CampaignInfoDLC01.txt"), os.path.join(a.install, "UI", "CampaignInfoDLC01.txt"))
        print("installed into", a.install)


if __name__ == "__main__":
    main()
