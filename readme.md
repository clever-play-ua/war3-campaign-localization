# War3 campaign localization

Translations of the Warcraft III Reforged custom campaigns by LoreCraft Designs, kept as plain JSON
(English text + hash per string) and turned into ready-to-play campaigns by one script.

| Campaign | Version | Languages |
|---|---|---|
| `CSW-RoA` Rise of the Alliance | 1.03 | PL, UK, ES, PT, CZ, DE, FR, KR |
| `CSW-ToD` Tides of Darkness | 2.01 | PL (complete, with assets), UK, ES, FR, PT, CZ, KR, RU, CN (story and quests) |

Data model: [FORMAT.md](FORMAT.md). Requirements: Python 3.10+, Pillow (only for previews). No other tools,
no World Editor.

```
python tools/l10n.py extract CSW-RoA            # clean English .wts out of the source campaign
python tools/l10n.py index   CSW-RoA            # canonical strings/ (keeps ids)
python tools/l10n.py check   CSW-RoA            # coverage, stale hashes, broken |c |r <AAAA,DataA1> tags
python tools/l10n.py build   CSW-RoA DE         # DE/wts/*.wts and the localized .w3n (--wts-only to skip the .w3n)
python tools/l10n.py import-wts CSW-RoA FR <dir>   # take translated .wts files into FR/strings
```

Paths of the source campaigns and the output folder are in each `campaign.json`.
Older releases: `source/<ver>/` keeps the English of that release and `<LANG>/pairs/<ver>/` the exact English/translation pairs (`l10n.py pairs`), so a translation can be matched to any later release by hash.
`archive/` holds the older databases, translator spreadsheets and reference `.wts` files this repository
was built from; nothing reads it.
