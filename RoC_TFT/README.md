# RoC_TFT: Warcraft III string reference

English Warcraft III (Reforged, Reign of Chaos + The Frozen Throne) strings next to one translation, one file per
language. Use it to look up how the game itself translates a unit, spell, place or character, and to check campaign
translations against it.

| File | Language | Source of the translation |
|---|---|---|
| `cz.json` | Czech | community package "Warcraft III Reforged čeština 0.9" (appears to carry the classic Czech localization) |

The English side always comes from the enUS files of the installed game (CASC), never from a translation package.

## Format

```json
{
  "lang": "cz",
  "source": "...",
  "english": "Reforged enUS (CASC, ...)",
  "built": "2026-11-18",
  "count": 18051,
  "strings": {
    "0ced97b65f": {
      "en": "Uther the Lightbringer",
      "tr": "Uther Lightbringer",
      "refs": ["Campaign/Classic/ROC/Human01.w3m#20", "..."],
      "variants": {"other translation": ["ref", "..."]}
    }
  }
}
```

* key: `sha256(English)[:10]`, the same hash the campaign string files in this repository use, so a campaign string
  is found directly by its hash.
* `tr`: the most frequent translation of that English text; `variants`: other translations of the same English text
  in other places (a name can be declined, a line can be phrased differently per map).
* `refs`: where the pair comes from:
  * `Units/HumanUnitStrings.txt|Hblm|Name`: string file, object id, field
  * `UI/FrameDef/GlobalStrings.fdf|KEY`: frame definition key
  * `Campaign/Reforged/ROC/Human01.w3x#123`: campaign map, STRING number in its `war3map.wts`

## Tool

```
python tools/war3_ref.py search cz "Holy Light"
python tools/war3_ref.py search cz --regex "^Uther"
python tools/war3_ref.py build cz --folder "<package folder>" --label "<source>"
python tools/war3_ref.py build it --casc itIT        (an official locale installed in Battle.net)
```

Translation packages are read as data only; nothing from them is run.
