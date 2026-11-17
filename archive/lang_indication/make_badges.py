"""Language badges (200 x 101, black): the language code in bold and the language's own name below, each line filled with
a left-to-right gradient across its own width. Writes <code>_black.svg for every entry of PALETTE.

    python archive/lang_indication/make_badges.py [de ...]          (no codes = all)

To add a language: add an entry to PALETTE (code text, native name, gradient stops). Stops are (offset, colour); the
measured badges use stops at 10/30/50/70/90 % of the line width. See README.md for how the colours were measured.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))

# code, native name, gradient stops. uk..zh measured from the original PNGs (average of both lines, 5 slices each);
# de: lilac -> gold (the Faberge variant).
PALETTE = {
    "uk": ("UK", "УКРАЇНСЬКА", ["#76cbd4", "#99d1bb", "#afd4a9", "#cad997", "#e5de84"]),
    "fr": ("FR", "FRANÇAIS", ["#82affd", "#88b8f6", "#8fc4fa", "#97d1f8", "#a3e1fb"]),
    "es": ("ES", "ESPAÑOL", ["#f1a45f", "#f3b264", "#f5c068", "#f6cb6b", "#f6d26c"]),
    "pt": ("PT", "PORTUGUÊS", ["#94de5b", "#a4e35f", "#aee361", "#bbe463", "#c4e465"]),
    "ko": ("KO", "한국어", ["#6a80d0", "#937ace", "#c57ac7", "#d989a6", "#eba16c"]),
    "zh": ("ZH", "中文", ["#e1657b", "#d86593", "#cc65ac", "#a85dcf", "#955ae1"]),
    # Latin American Spanish: white bark to young leaves (former "Birch" spare palette)
    "es_latam": ("ES", "LATAM", ["#f4f1e6", "#dfe8cf", "#c7e0b8", "#b0d8a6", "#98cf93"]),
    "de": ("DE", "DEUTSCH", ["#c7a8ff", "#d5b6e8", "#e2c4cf", "#edcfae", "#f5d88c"]),  # lilac enamel to gold ("Faberge" variant)
    # gold to copper (chosen from the ru_variants set: "Palekh")
    "it": ("IT", "ITALIANO", ["#ffdf8f", "#f8c97d", "#eeb26e", "#e49a62", "#d98358"]),
    # turquoise ice to deep blue (chosen from the ru_variants set: "Baikal ice")
    "cs": ("CS", "ČEŠTINA", ["#8ff0ff", "#79d4ff", "#68b8ff", "#5d9cff", "#5580ff"]),
    # light to deep green (chosen from the ru_variants set: "Malachite")
    "th": ("TH", "ไทย", ["#a8f5dc", "#86ebc9", "#64dfb6", "#47d1a3", "#2ec292"]),
    # coral -> flag red
    "tr": ("TR", "TÜRKÇE", ["#ffa08e", "#ff8d84", "#ff7c7a", "#ff6c70", "#ff5c66"]),
    # emerald -> desert gold
    # pale pink to lilac ("White nights" variant), the pale end brighter
    "pl": ("PL", "POLSKI", ["#ffe6ee", "#f6d6f0", "#e6c6f8", "#cfb6ff", "#b8a6ff"]),
    "ar": ("AR", "العربية", ["#36c99a", "#6fcf8e", "#a9d27f", "#dbc96f", "#f2c45f"]),
    # white porcelain to cobalt (chosen from the ru_variants set: "Gzhel")
    "ru": ("RU", "РУССКИЙ", ["#f2f5ff", "#c9d4ff", "#a0b2ff", "#7890ff", "#566fff"]),
    # Japanese: green to violet (the former "Northern lights" spare palette)
    "ja": ("JA", "日本語", ["#6ff0b8", "#6fd6d0", "#7bb5f0", "#8b98ff", "#a07dff"]),
    # Indonesian: wheat to sage (the former "Feather grass" spare palette)
    "id": ("ID", "INDONESIA", ["#f5e3a1", "#e2dc9f", "#cdd59e", "#b6cd9d", "#9fc59b"]),
    # Vietnamese: pale pink to orchid ("Lotus"); Dutch: magenta to violet ("Dragon fruit"); Hindi: peach to rose ("Silk")
    "vi": ("VI", "TIẾNG VIỆT", ["#ffd1e6", "#f7b3d9", "#ee95cc", "#e077c0", "#cf5cb5"]),
    "nl": ("NL", "NEDERLANDS", ["#ff7ac8", "#f07ad8", "#d87ce6", "#bb80f0", "#9c85f7"]),
    "hi": ("HI", "हिन्दी", ["#ffe0c2", "#ffcdb8", "#f9b8b4", "#f0a3b4", "#e38eb7"]),
    # spare palettes not used by any language yet ("NN / VARIANT" placeholders, from the old Russian candidates)
    "nn_silver": ("NN", "VARIANT", ["#f4f6f8", "#d9dee5", "#bec6d1", "#a4afbe", "#8b98aa"]),
}
OFFSETS = [0.1, 0.3, 0.5, 0.7, 0.9]
ORIGINALS = {"uk", "fr", "es", "pt", "ko", "zh"}                 # hand-made PNGs the palette was measured from
LABELS = {"nn_silver": "Silver: bright to steel"}
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def png(code):
    """<code>_black.png rendered from the SVG by headless Edge at 200 x 101 (the originals are never overwritten)."""
    import subprocess
    if code in ORIGINALS:
        return
    src = os.path.join(HERE, "%s_black.svg" % code)
    out = os.path.join(HERE, "%s_black.png" % code)
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--window-size=200,101", "--screenshot=" + out, "file:///" + src.replace("\\", "/")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="101" viewBox="0 0 200 101">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="0">
{stops}
    </linearGradient>
  </defs>
  <rect width="200" height="101" fill="#000"/>
  <text x="100" y="54" text-anchor="middle" fill="url(#g)"
        font-family="{code_font}" font-weight="700" font-size="64"
        letter-spacing="-1">{code}</text>
  <text x="100" y="84" text-anchor="middle" fill="url(#g)"
        font-family="'Open Sans', 'Noto Sans', 'Malgun Gothic', 'Yu Gothic', 'Meiryo', 'Nirmala UI', 'Microsoft YaHei', 'Leelawadee UI', 'Segoe UI', Tahoma, Arial, sans-serif"
        font-weight="400" font-size="{name_size}">{name}</text>
</svg>
"""


def svg(code):
    text, name, stops = PALETTE[code]
    stop_xml = "\n".join('      <stop offset="%g" stop-color="%s"/>' % (o, c) for o, c in zip(OFFSETS, stops))
    size = 27 if len(name) <= 9 else 25                    # long native names a little smaller to fit 200 px
    # Open Sans / Segoe UI draw "J" with a tail below the baseline; Arial keeps it on the line
    font = "Arial, 'Helvetica Neue', sans-serif" if "J" in text else "'Open Sans', 'Noto Sans', 'Segoe UI', Arial, sans-serif"
    return SVG.format(stops=stop_xml, code=text, name=name, name_size=size, code_font=font)


if __name__ == "__main__":
    import json
    for code in sys.argv[1:] or PALETTE:
        path = os.path.join(HERE, "%s_black.svg" % code)
        open(path, "w", encoding="utf-8").write(svg(code))
        png(code)
        print("wrote", path)
    # list for index.html: code, name, stops, is an original, svg, label, png, section (lang / candidate / spare)
    def section(c):
        return "spare" if c.startswith("nn_") else "lang"
    rows = [[PALETTE[c][0].lower() if c != "es_latam" else c, n, s,
             c in ORIGINALS, "%s_black.svg" % c, LABELS.get(c, ""), "%s_black.png" % c, section(c)]
            for c, (_, n, s) in PALETTE.items() if not c.startswith("_")]
    open(os.path.join(HERE, "palette.js"), "w", encoding="utf-8").write(
        "var BADGES = " + json.dumps(rows, ensure_ascii=False) + ";\n")
