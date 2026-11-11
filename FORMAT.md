# Data model

One idea: **a string is its English text plus the hash of that text.** Everything else (ids, speakers,
STRING numbers, acts) is metadata. Nothing is built per act or per mission by hand-written scripts any more;
one engine (`tools/l10n.py`) works the same way for every campaign.

hash = first 10 hex chars of `sha256(utf-8 text)`, taken on the text with `\n` line endings.

## Layout

```
<Campaign>/                     CSW-RoA, CSW-ToD
  campaign.json                 source .w3n, task list (task id <-> map file), title/description string numbers
  source/enUS/<task>.wts        clean English .wts extracted from the game's campaign (machine-owned, never edited)
  strings/                      canonical English, what translators work from
    story/<Camp>_<ver>_<task>_enUS.json   spoken lines (VO), in scene order
    quests/<task>.json          objectives, hints/messages, on-screen texts, faction/unit/building names of a mission
    global/                     campaign-wide: menu.json, heroes/<CODE>.json, units.json, buildings.json, artifacts.json ...
    other/<task>.json           every remaining block (editor names, standard tooltips...), keyed by hash, not for review
  <LANG>/                       PL, UK, ES, PT, CZ, DE, FR, KR, RU, CN
    lang.json                   title, credit line, version, output file name, files that replace files in the .w3n
    strings/...                 same files as above, one entry per id
    wts/<task>.wts              generated, ready to inject
    assets/                     loading screens (dds + jpg preview), textures, scripts that replace English ones
```

A *task* is one map plus its `war3map.wts` (e.g. `A01_M03` = `WC2Human03.w3x`), or the campaign-wide
`GLOBAL` = `war3campaign.wts`.

## Entry formats

Canonical (`strings/`):

```json
"RoA_A01_M03_VO_S011_002": {
  "hash": "31ce38aa89",
  "en": "My best strategy would be to place a barrel ...",
  "type": "VO",
  "speaker": "Khadgar"
}
```
`other/` entries use the id `#<hash>` and also carry `refs` (STRING numbers in the source wts) and `note`
(the editor comment: which unit/ability/field the text belongs to).

Files are named `<Camp>_<campaign version>_<task>_<LANG>.json`, e.g. `ToD_2_01_A01_M01_RU.json`
(canonical English: `..._enUS.json`). Translation (`<LANG>/strings/`), English first, then the translation:

```json
"RoA_A01_M03_VO_S011_002": { "hash": "31ce38aa89", "en": "My best strategy ...", "tr": "Ma meilleure stratégie ..." }
```
`en` is informational (copied from the canonical file); only `tr` and `hash` are read back.
`hash` is the hash of the English the translation was made for. If it differs from the canonical hash the
English changed since: the entry is *stale* (`check` counts them), the translation is still used until updated.
In `other/` only translated entries are stored. An optional `"variants": {"<editor comment or #STRINGnumber>": "..."}`
holds the rare case of one English text translated differently in two places of the same map.

## Build

For every block of `source/enUS/<task>.wts`: hash its text, look the hash up in that language's strings
(same task first, then any task), replace the body, write CRLF. Then `tools/w3mpq.py` writes the
campaign exactly like World Editor's save (embedded maps stored raw), so no resave is needed.
Title and description (`title_block`, `description_block` in `campaign.json`) come from `lang.json`;
the credit line is appended to the description.

## Updating to a new game version

1. Put the new `.w3n` path in `campaign.json`, run `extract` (new clean `.wts`).
2. Run `index`: ids are carried over by hash, then by near match (>= 90 %, same `<AAAA,DataA1>` placeholders);
   new texts get no id until someone adds one, removed texts disappear.
3. `check` shows what is new or stale per language. Translators fill `tr`, set `hash` to the new hash.

## Known limits

* The `war3map.j` scripts of ToD contain translatable strings (oil harvest messages). They are kept as
  replaced files under `CSW-ToD/PL/assets/maps/`, not as strings.
* The writer can only replace files that already exist in a campaign. Files a translation *adds* (the Polish
  quest-dialog font and fixed models) are listed under `added_files` in `lang.json` and stored in `assets/added/`,
  but are not injected by `build` yet.
* One text with different meanings in different blocks that have no editor comment is told apart by STRING
  number, which is only valid for that game version.
