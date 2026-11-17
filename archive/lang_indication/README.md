# Language badges

Small labels that mark a language: the language code in bold and the language's own name below it, on black.
Originals: `uk/fr/es/pt/ko/zh_black.png` (PNG, 200 x 101). Recreated as SVG by `make_badges.py`, plus German.

## Layout (measured from the originals)

| Part | Value |
|---|---|
| Canvas | 200 x 101 px, background `#000000` |
| Code (UK, FR, ...) | Open Sans Bold, 64 px, letter-spacing -1, centred, baseline y = 54 (caps from y ≈ 8 to 54) |
| Native name | Open Sans Regular, uppercase where the script has case, 27 px (25 px for names longer than 9 letters), centred, baseline y = 84 |
| CJK names | same size; falls back to Malgun Gothic (Korean) / Microsoft YaHei (Chinese) |
| Fill | horizontal linear gradient, left to right, **applied to each line separately** across that line's own width (SVG `objectBoundingBox`), so the code and the name start and end with the same colours |

## Colours

Stops at 10 / 30 / 50 / 70 / 90 % of the line width. Measured as the average colour of solid text pixels
(brightness > 200) in five equal slices of each line, averaged over the code and the name line.

| Code | Name | 10 % | 30 % | 50 % | 70 % | 90 % | Character |
|---|---|---|---|---|---|---|---|
| uk | УКРАЇНСЬКА | `#76cbd4` | `#99d1bb` | `#afd4a9` | `#cad997` | `#e5de84` | sky blue → wheat yellow (flag) |
| fr | FRANÇAIS | `#82affd` | `#88b8f6` | `#8fc4fa` | `#97d1f8` | `#a3e1fb` | blue → pale cyan |
| es | ESPAÑOL | `#f1a45f` | `#f3b264` | `#f5c068` | `#f6cb6b` | `#f6d26c` | orange → yellow |
| **es_latam** | LATAM | `#f4f1e6` | `#dfe8cf` | `#c7e0b8` | `#b0d8a6` | `#98cf93` | **new:** Latin American Spanish, code ES with "LATAM"; white bark → young leaves (former Birch spare palette) |
| pt | PORTUGUÊS | `#94de5b` | `#a4e35f` | `#aee361` | `#bbe463` | `#c4e465` | green → lime |
| ko | 한국어 | `#6a80d0` | `#937ace` | `#c57ac7` | `#d989a6` | `#eba16c` | blue → violet → orange (3 hues) |
| zh | 中文 | `#e1657b` | `#d86593` | `#cc65ac` | `#a85dcf` | `#955ae1` | rose red → violet |
| **de** | DEUTSCH | `#c7a8ff` | `#d5b6e8` | `#e2c4cf` | `#edcfae` | `#f5d88c` | **new:** lilac → gold (picked from the Faberge variant) |
| **it** | ITALIANO | `#ffdf8f` | `#f8c97d` | `#eeb26e` | `#e49a62` | `#d98358` | **new:** gold → copper (picked from the Palekh variant) |
| **cs** | ČEŠTINA | `#8ff0ff` | `#79d4ff` | `#68b8ff` | `#5d9cff` | `#5580ff` | **new:** turquoise → deep blue (picked from the Baikal ice variant) |
| **th** | ไทย | `#a8f5dc` | `#86ebc9` | `#64dfb6` | `#47d1a3` | `#2ec292` | **new:** light → deep green (picked from the Malachite variant) |
| **tr** | TÜRKÇE | `#ffa08e` | `#ff8d84` | `#ff7c7a` | `#ff6c70` | `#ff5c66` | **new:** coral → flag red |
| **pl** | POLSKI | `#ffe6ee` | `#f6d6f0` | `#e6c6f8` | `#cfb6ff` | `#b8a6ff` | **new:** pale pink → lilac (White nights variant, pale end brighter) |
| **ar** | العربية | `#36c99a` | `#6fcf8e` | `#a9d27f` | `#dbc96f` | `#f2c45f` | **new:** emerald → desert gold |
| **ru** | РУССКИЙ | `#f2f5ff` | `#c9d4ff` | `#a0b2ff` | `#7890ff` | `#566fff` | **new:** white porcelain → cobalt (Gzhel, not the flag) |
| **ja** | 日本語 | `#6ff0b8` | `#6fd6d0` | `#7bb5f0` | `#8b98ff` | `#a07dff` | **new:** green → violet (former Northern lights spare palette) |
| **id** | INDONESIA | `#f5e3a1` | `#e2dc9f` | `#cdd59e` | `#b6cd9d` | `#9fc59b` | **new:** wheat → sage (former Feather grass spare palette) |
| **vi** | TIẾNG VIỆT | `#ffd1e6` | `#f7b3d9` | `#ee95cc` | `#e077c0` | `#cf5cb5` | **new:** pale pink → orchid (Lotus) |
| **nl** | NEDERLANDS | `#ff7ac8` | `#f07ad8` | `#d87ce6` | `#bb80f0` | `#9c85f7` | **new:** magenta → violet (Dragon fruit) |
| **hi** | हिन्दी | `#ffe0c2` | `#ffcdb8` | `#f9b8b4` | `#f0a3b4` | `#e38eb7` | **new:** peach → rose (Silk) |

Spare palettes (no language yet, badges read "NN / VARIANT", files `nn_<name>_black.*`): silver `#f4f6f8 → #8b98aa`. To use one, copy its
five colours into a new language entry and delete the `nn_` entry.

PNG: `make_badges.py` renders `<code>_black.png` from each SVG with headless Edge (200 x 101); the six original PNGs are
never overwritten. `index.html` shows every badge with PNG and SVG download buttons.

Codes are ISO 639-1 language codes like the originals (uk, ko, zh): Czech is `cs`, not the country code CZ.

Common traits, to keep new ones in the same family: saturation about 50-70 %, lightness 60-75 % (pastel on black,
no pure primaries), the gradient moves 40-70° of hue across the line (Korean and Chinese up to ~150°), and no two
languages share the same start hue. German starts at lilac (~265°) and ends in gold.

## Making a new one

1. Add a line to `PALETTE` in `make_badges.py`: `"it": ("IT", "ITALIANO", [five colours left to right])`.
   Pick a start hue not used above (free: teal ~175°, magenta ~310°, deep blue ~230° start with a different end).
2. `python archive/lang_indication/make_badges.py it` writes `it_black.svg`.
3. For a PNG, open the SVG in a browser and export / screenshot at 200 x 101 (the SVG uses the system's Open Sans;
   to make it independent of installed fonts, convert the text to outlines in Inkscape/Figma).

Files: `*_black.png` originals (and `de_black.png` rendered from the SVG), `*_black.svg` recreations, `make_badges.py`.
