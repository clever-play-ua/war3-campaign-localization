# Project knowledge base

Everything learned while localizing the LoreCraft Designs Warcraft III campaigns (Rise of the Alliance, Tides of
Darkness), written down so nothing depends on anyone's memory. Dates are 2026. Sections: 1 how to work fast,
2 file formats, 3 patching and building campaigns, 4 translation matching, 5 per-language facts, 6 known bugs and
traps, 7 side projects (CASC, StarCraft, BFME), 8 open work.

---------------------------------------------------------------------------------------------------------------

## 1. Fast path: build or rebuild a custom campaign

`python tools/l10n.py <command> <Campaign> [LANG]` (see FORMAT.md). Typical life of a new release:

1. Put the new English `.w3n` path into `<Campaign>/campaign.json` (`source_w3n`, `version`), run `extract`.
2. `index` keeps existing ids (hash match, then >= 90 % near match with the same `<AAAA,DataA1>` placeholders).
3. `check` lists new/stale/broken strings per language; translators edit `<LANG>/strings/...json` (`en` shown, fill `tr`).
4. `build <Campaign> <LANG>` writes `<LANG>/wts/<ver>/...wts` and the localized `.w3n` into `output_dir`.
   No World Editor resave is needed (see 3.2). Loading screens and other replaced files come from `lang.json`.
5. To bring an older translation to a new release: `import-wts <Campaign> <LANG> <translated wts dir> --ref-en <English wts
   dir of that old release>` (hash memory + near match; near matches are marked stale), or `import-db` for flat
   id->text databases. `pairs` stores exact English/translation pairs of an old release for later matching.

To add another campaign: copy a `campaign.json`, list `tasks` (task id <-> map file), set `title_block` and
`description_block` (the TRIGSTR numbers referenced by `war3campaign.w3f`: find them with `re.findall(rb"TRIGSTR_(\d+)", w3f)`).

Campaign files: ToD 2.01 EN `C:\Users\UserM\Desktop\CSW_Tides_Of_Darkness_2_01.w3n` (22 maps + test map), RoA 1.03 EN
`F:\ЗАГРУЗКИ\CSW_Rise_of_the_Alliance_1_03.w3n` (5 maps), ToD 2.0 EN `D:\Games\WarCraft\CSW_Tides_Of_Darkness.w3n`
(the English the Russian 2.0 translation was made from), translated releases: PL/RU ToD on the Desktop, RoA FR/KR/CZ old
builds in `F:\ЗАГРУЗКИ`.

## 2. File formats

### 2.1 `.wts` (string table)
```
STRING 3848
// Items: IL01 (WC2Human01Terenas01), Description (Description)
{
Guards, to me!….
}
```
* `\r\n` line endings are mandatory. A file with bare `\n` makes the game show **no** strings at all (not a partial
  failure). Rule used everywhere: read with newline normalization, process with `\n`, convert to `\r\n` only at the final
  write; verify with the first bytes `STRING 1\r\n{\r\n`. A UTF-8 BOM at the start is normal and must be preserved.
* `STRING` numbers are renumbered by World Editor on every save. Never trust a number across versions; match by text.
* The optional `//` comment names the object and field (`Units: hrtt (Elven Ranger), Ubertip (Tooltip - Extended)`,
  `Items: ...`, `Abilities: ...`, `Doodads:`, `Destructibles:`). Present in map wts, absent in the campaign-wide wts
  (`war3campaign.wts`, 3975-6628 blocks of standard unit/ability tooltips). Many strings have no comment at all.
* Tags inside text: `|cffRRGGBB ... |r` colours, `|n` newline (Korean official strings use a real newline instead),
  ability variables `<AUaW,DataA1>`, `<ADpE,DataD1,%>` (4-char ability code, field). A translation must keep the variables
  identical. Rank texts (`DataA1` vs `DataA2`) are the same sentence with a digit changed.
* Typical translator typos found: missing space after a comma, doubled `|r`, `MISE À JOURr` (missing `|`), trailing
  spaces, `Chaptitre`.
* Identical English text appears in several blocks (e.g. "High Elven Manor" x9). Every block must be filled, not only the first.
  Rare context-dependent duplicates (same English, different Polish in two blocks, no editor comment) are told apart by
  STRING number only (`variants` in the language entry).

### 2.2 `.w3n` campaign and `.w3x` map
Both are MPQ v0 archives. A campaign contains: `war3campaign.w3f` (campaign metadata + TRIGSTR references to
`war3campaign.wts`), `war3campaign.wts`, the maps as **embedded raw `.w3x` files**, campaign-level assets
(`_HD.w3mod\...`, `UI\Glues\...`, `replaceabletextures\...`), `(listfile)`, `(attributes)`.
Each map contains `war3map.wts`, `war3map.j` (JASS script; ToD oil-harvest messages are hard-coded strings in it),
`war3map.w3*` object data, models, textures.

### 2.3 Locales
Reforged resolves files for the client language from `_Locales\<loc>.w3mod\...` first (e.g. `_Locales\koKR.w3mod\war3map.wts`,
`_HD.w3mod\_Locales\zhCN.w3mod\UI\...`). So a campaign can carry several languages at once:
* Korean RoA (old): base `war3map.wts` English, `_Locales\koKR.w3mod\war3map.wts` Korean (same STRING numbers), Korean loading
  screens under `_HD.w3mod\_Locales\koKR.w3mod\UI\Glues\LoadingScreen\...`.
* French RoA, Russian/Polish ToD: translation written straight into the base wts (no locale split).
* RoA 1.03 and ToD ship a zhCN locale for loading screens; the Russian ToD file also contains `_Locales\zhCN.w3mod\war3campaign.wts`
  (175 KB), logos, banner and menu video: a Chinese localization exists inside it and has not been extracted yet.
Our builds replace the base files, which works for every client language unless a locale override of that language exists.

### 2.4 Loading screens (important, non-obvious)
* A map's loading screen is a tiny model `_HD.w3mod\UI\Glues\LoadingScreen\WC2Human\WC2Human01LS.mdx` that references a texture
  `UI\Glues\LoadingScreen\WC2Human\WC2Human01.dds` (no `_HD.w3mod` in the reference). The texture is **not** inside the map; the
  replacement goes at **campaign level** under `_HD.w3mod\UI\Glues\LoadingScreen\WC2Human\WC2HUMAN01.dds` (RoA: `WC2HUMAN01..04`,
  `WC2HUMANACT1`) or `UI\Glues\LoadingScreen\WC2ORC\WC2ORC01.dds`, `WC2ORCACT01`, `WC2ORCFINAL` (ToD, 22 files).
* Must be block-compressed DDS (DXT1 with mipmaps, 1 383 240 bytes, or DXT1 without mipmaps 1 036 928, or DXT5 2 073 728, all
  1920x1080). Pillow can read these but only writes uncompressed RGBA, which breaks in game: always supply real DXT files
  and check the header (`data[:4] == b"DDS "`, fourCC at offset 84).
* Other localizable textures: `replaceabletextures\cameramasks\CreditsLogo.dds`, `Lorecraftlogo.dds` (changed in the Polish ToD).

## 3. Patching and building campaigns without the Editor

### 3.1 Why StormLib injection "needed a resave" (solved 2026-11-06)
Not a checksum problem. The game opens embedded maps **in place** as nested MPQs, which only works if the map is stored raw
(block flags `0x80000000`). `SFileAddFileEx` zlib-compresses everything (`0x80000200`), so the map would not start until the
Editor rewrote it. Everything else (the per-file CRC32 in `(attributes)`) StormLib keeps correct.

### 3.2 `tools/w3mpq.py` writes archives exactly like World Editor
Verified byte-for-byte against a real Campaign Editor resave (SHA256 identical) and against a no-op rebuild of the original.
What the Editor save does:
* rewrites the whole archive: 32-byte v0 header, all block data back to back in block-table order, hash table, block table;
* embedded `.w3x/.w3m` stored raw; every other replaced file in 4096-byte sectors, each zlib **level 6**, a `0x02` mask byte
  per sector, stored raw when compression does not shrink it; encrypted/offset-keyed blocks are re-keyed when they move;
* hash table unchanged, `(listfile)` order kept, `(attributes)` = CRC32 of each block's uncompressed data (flags `0x1` only);
* `war3campaign.w3f` integer at offset 4 (save counter) is incremented.
Limits: only replaces files that already exist (cannot add). The Polish ToD adds 19 files (quest-dialog font, fixed models);
they are stored under `CSW-ToD/PL/assets/added/` and are not injected yet. Adding needs hash-table insertion + new blocks
(+ `(listfile)`/`(attributes)` entries).
Nested maps: `open_nested(campaign, "WC2Human01.w3x")` reads a map in place; to change a map, extract its bytes, rebuild it,
put the new bytes into the campaign replacement dict.

### 3.3 StormLib / storm.dll traps (legacy tooling, kept for reference)
* StormLib functions return C++ `bool` (1 byte). Declaring `wintypes.BOOL` reads register garbage, so failures looked like
  success (an `SFileExtractFile` "succeeded" without writing a file). Use `ctypes.c_bool`.
* `SFileFindFirstFile`/`FindNextFile` with a hand-written struct hung even on a 32-file archive; `SFileGetFileInfo(FileIndex)`
  failed with the enum value assumed. Read `(listfile)` instead, or parse the MPQ in Python (what `w3mpq.py` does).
* `SFileCompactArchive` after repeated injections avoids archive bloat; still not Editor-identical.
* Always rebuild from the clean English release, never inject on top of an already modified campaign.
* `SFileVerifyFile(..., 0x02)` (CRC) is a good independent check: result `0x10` = has CRC and OK.

### 3.4 Quick recipe used for every RoA language
extract English -> replace each map's `war3map.wts` (rebuild map) -> replace `war3campaign.wts` (+title/description) -> replace the
five loading-screen DDS at campaign level -> write campaign. 3-16 seconds, output ~1.1 GB (RoA) / 1.7 GB (ToD).
Campaign display name rule: `Rise of the Alliance <campaign ver> <LANG> <translation ver>` and file name
`CSW_Rise_of_the_Alliance_1_03_<LANG>_<ver>.w3n`; the translator credit line is appended to the description string.

## 4. Matching translations (the core method)

* hash = first 10 hex of `sha256(utf-8 text)` on `\n` text. Match by hash first.
* Near match: `difflib.SequenceMatcher` ratio >= 0.90 **and** identical placeholder multiset (`<AAAA,DataA1>`). Prefilter by
  length (+/-20 %) and `real_quick_ratio`/`quick_ratio` or it takes minutes. Near matches are stored with the old hash so they
  are flagged stale.
* Whitespace-insensitive match for carrying ids (trailing `  \n` differences are common in hint strings).
* Do not `strip()` English before hashing: short UI fragments (" Gold") differ only by spaces.
* Speaker names are their own STRING blocks (cinematic speaker labels). Take them from a name table (unit id -> name).
* Fill all blocks with the same hash (a bug in the first RoA scripts translated only the first of 7-9 identical blocks).
* Build resolution: same task first, then any task of the campaign (cross-map memory).
* **Row-shift corruption** (found in ES/PT `GLOBAL`, hero abilities of Uther, Turalyon, Derek Proudmoore, Wilhelm): spreadsheet
  import had shifted translations by several rows; symptom in game: literal `<AUaW,DataA1>` shown. Detect/repair by the
  placeholder signature `tuple(sorted(PH_RE.findall(text)))`: a translation belongs to the English key with the same
  signature. Ambiguous cases were left alone, never guessed. The French `database/frFR` from July had the same corruption.
* Rank translated but sibling rank not: the two English strings differ by one consistent digit substitution; apply the same to
  the translation (`digit_remap`).
* When two sources disagree, the real translated campaign beats the spreadsheet (spreadsheets lag behind editor fixes).
* Old releases: pair the English of that release with the translation by STRING number (numbering inside one release is
  identical), build a hash memory, then apply to the new release. Verify with placeholder equality per pair.
* `import-wts` from a campaign of another release without its English reference only works when numbering still aligns
  (checked by placeholder shape at the same number); prefer `--ref-en`.

## 5. Per-language facts

| Lang | Campaign file / version | Notes |
|---|---|---|
| PL | RoA 0.2 | credit Arkadiusz Grzanka - LitheDude (Discord lithelabs). Uther the Lightbringer = **Uther Światłodzierżca** everywhere (also bare "Uther"). Official quest UI terms in 5.1. |
| ES (esMX) | RoA 0.2 | credit SoyMante (Discord mante1880). |
| PT (ptBR) | RoA 0.2 | credit Hunk (Discord hunk6427). "Changelog 1.03" = "Registro de alterações 1.03". |
| CZ | RoA 0.1 | credit Anton Dragonpride (Discord adragonpride). Only the story was translated (GLOBAL 17 of 573). Loading screens came from the old 1.01 CZ campaign. |
| DE | RoA 0.1 | machine translation by Claude, "(GERMAN AI TRANSLATION V.0.1)". Terms from the German game install via CASC (5.2). |
| FR | RoA 1.01 | credit Thomas FARINA "Whirlwind" (Discord whirlwind0174). Developer's known issues: dialogue volume 10 % twice (audio, not text), typos from Sheets, "chapititre" in the menu (fixed to "Chapitre"), Danath chapter 4 line lacks "capitaine" (text edited to match audio), Khadgar "Yes ?" at chapter 3 start (block 761, translated here). |
| KR (koKR) | RoA 1.0 | credit 메저드 (Discord medjed_8181). Translation lives in the koKR locale of the old campaign. |
| UK (ukUA) | RoA 0.4 | credit "(Переклад українською CLEVER PLAY UA)". Title/file use **UK**, code ukUA. |
| RU | ToD 2.0 | from `CSW_Tides_Of_Darkness_RU_2_0.w3n`; exact pairs in `CSW-ToD/RU/pairs/2.00`. |
| PL | ToD 2.01 | complete translation + translated textures; translator name/version not recorded. |

Credit/title strings differ per language by design (`Traducción al español: ...`). Names with Discord links go on GitHub as
`Name (Discord: nick)` plus `https://discord.com/users/<id>`.

### 5.1 Official Polish quest UI (verified against a real Blizzard PL map)
MAIN QUEST = ZADANIE GŁÓWNE; ... COMPLETED = ZADANIE GŁÓWNE ZAKOŃCZONE SUKCESEM; MAIN QUEST FAILED = MISJA ZAKOŃCZONA
NIEPOWODZENIEM; MAIN QUEST UPDATE = ZADANIE GŁÓWNE – AKTUALIZACJA; OPTIONAL QUEST = ZADANIE DODATKOWE (+ ZAKOŃCZONE SUKCESEM);
HINT = WSKAZÓWKA; per-item markers "(wykonane)"/"(nieudane)". "Landing" = Lądowanie. ("SECRET DISCOVERED" = ODKRYTO SEKRET.)
German: HAUPTQUEST, OPTIONALE QUEST, TIPP, HINWEIS, ... ABGESCHLOSSEN/FEHLGESCHLAGEN/AKTUALISIERT; Prachtmeer (Proudmoore),
Bronzebart, Schwarzfelsklan, Silberne Hand, Uther der Lichtbringer; archaic Ihr/Euch.

### 5.2 Getting official terminology from a game install (CASC)
Reforged stores official campaigns as `Campaign/Reforged/ROC/*.w3x` and `TFT/*.w3x` in CASC; each map has the English
`war3map.wts` plus `_Locales\<loc>.w3mod\war3map.wts` for 13 languages with identical STRING numbers, so EN/xx pairs are exact.
Unit/ability strings: `War3.w3mod:_Locales/deDE.w3mod:Units/*.txt`. Reading CASC (no external tool; used for German):
* `Data/config/<2>/<2>/<hash>` build config -> `root`, `encoding` (ckey ekey), key from `.build.info` (column "Build Key").
* `Data/data/*.idx` (use the newest per bucket): header size, 18-byte entries = 9-byte key, 5-byte big-endian (archive<<30|offset),
  4-byte little-endian size. Blocks live in `data.NNN` with a 30-byte header before the BLTE blob.
* BLTE: `BLTE` + header size + chunk table; chunk modes `N` raw, `Z` zlib, `F` nested.
* `encoding` file (`EN` magic) maps content keys to encoded keys. Root for WC3 is a plaintext manifest
  `War3.w3mod:path|ckey_hex||` (21 MB). WC3 install: `F:\Games\Warcraft III` (build 3.0.1.24371, German text installed).

## 6. Traps and lessons

* Windows console is cp1251: printing Hangul/accents raises `UnicodeEncodeError`. Run `python -X utf8`, or write to a file.
* Backslash handling in quoting layers (shell heredocs, JSON written through tools) halves or eats `\`; write path-heavy JSON/
  Python through a file tool and prefer forward slashes in JSON.
* Don't generate DDS with Pillow (2.4). Verify DXT by header.
* Never inject into a campaign someone is testing/has open; build to another name.
* Archive rebuild writes a `.tmp` then replaces: if the target is open in the game the replace fails (`PermissionError`).
* Czech localization only covers the story; GLOBAL tooltips of CZ/DE remain English by design. Standard units/abilities can be
  taken from the official localization for languages that have one (DE, FR, ES, PL, PT, KO, RU...).
* Do not trust the first translation DB row numbers (`STRING NUMBER` column) except as a last resort after hash/fuzzy fail.
* Existing per-language GitHub repos (`CSW-RoA-XX`) are published snapshots; their scripts now call `tools/l10n.py`.

## 7. Side projects

### 7.1 StarCraft: Remastered CASC in-place patching (experiment, 2026-09)
Goal: prove read -> decode BLTE -> edit -> re-encode -> write back at the same offset, without samase. CASC read path does not
re-verify content hashes; BLTE per-chunk checksums and the 30-byte block header checks are verified, so keep the new BLTE
blob within the old block size and leave the header, `.idx`, encoding and root untouched (new blob padded in place; BLTE
self-terminates by chunk accounting). Test: `locales\enUS\Assets\rez\RC3.json` copyright "1998" -> "3333" and `webui/dist/
CreditsPanel/CreditsPanel.js` credits. Battle.net Agent "Scan and Repair" would revert patches; ProductAgentService can rewrite
`.idx` versions (the user must stop services manually). **Root cause of "nothing shows"**: the patched copy
(`F:\ЗАГРУЗКИ\starcraft_remastered_razor\...`) was never the install Battle.net launches (`F:\Games\StarCraft`, found in
`C:\ProgramData\Battle.net\Setup\s1_2\Logs\battle.net-launcher-*.log`). Encoding format is documented on wowdev.wiki `/TACT`
(not `/Encoding`); the SC:R root is also a plaintext `path|ckey` list. Locale lookup must skip locales not installed.
Scripts (not in this repo): `casc_common.py`, `blte.py`, `encoding_file.py`, `casc_patch.py` in `sc_rem_test_inject`.

### 7.2 BFME1 Ukrainian localization (separate repo)
Community patch 2.22 at `F:\BFME1`. CSF/font/subtitle files can only come from `lang\english.big`; patch archives
`_patchNNN.big` can override `data\...` paths only. `.big` headers: base archives `BIG4`, patches `BIGF`; a wrong header makes the
engine ignore the archive silently. The subtitle ini path is case-sensitive: `Data\English\Movies\<Name>.ini`; otherwise
`INI::load` crashes once `HasSubtitles = Yes`. A full base-game reinstall resets `lang\english.big` to stock. Font:
`albertusmt_ua.otf` under the registered name "Albertus MT". Release tip: verify the packaged paths actually exist
(`locales/1.06/lang/FR/englishaudio.big`).

### 7.3 Retired web tool (history)
The first system (June-July 2026) was a static GitHub Pages tool (index/update/build html + vanilla JS) with JSON databases
`{meta, strings: {id: {hash, string_number, type, unit, source, wts_source}}}`, `manifest.json`, timestamps in file names.
It was replaced by `tools/l10n.py`; its data is in `archive/`.

## 8. Open work

* Inject files that a translation adds (Polish ToD quest dialog font and fixed models) in `w3mpq.py`.
* Import ToD global strings for non-Polish languages (old CSV/JSON in `archive/tod-prototype`).
* Extract the Chinese localization found inside the Russian ToD file.
* Translator names/versions for PL ToD and the other ToD languages; RU ToD campaign build (RU-only changed files: `war3campaign.w3f`,
  2.0 skin files).
* ES/PT/UK/FR: a few tooltips whose placeholders differ from English (`check` shows them).
* FORMAT.md known limits; consider promoting `variants` handling to context ids.
