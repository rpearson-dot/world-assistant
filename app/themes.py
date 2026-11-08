"""World Assistant's look: every colour and the design language (type, corners, headings), as named themes.

Pages never write a colour themselves: they use the tokens here (`var(--accent)`, `var(--series-3)`...), and scripts
that draw (the map, the graph, charts) read them with `getComputedStyle`. A test keeps it that way.

Each theme has a light and a dark set of colours. Which theme and which mode (automatic, light or dark) is chosen on
each device (Settings, Look) and kept in that browser; the head script puts it on the page before it is drawn, as
`data-theme`, `data-mode` and `data-dark` on <html>.

To add a theme: add it to THEMES with what differs from Standard (anything left out is Standard's).
"""

from __future__ import annotations

from typing import Any

BRAND = "#14231a"  # the app icon's green: the phone's status bar and the installed app's splash

# --- Standard: the colours World Assistant has always had ----------------------------------------------------------
_LIGHT = {
    "bg": "#f5f6f8", "card": "#ffffff", "ink": "#1c2330", "dim": "#667085", "line": "#e3e6ec", "chip": "#eef1f6",
    "accent": "#1f6feb", "on-accent": "#ffffff",
    "good": "#187847", "good-bg": "#e6f4ec", "warn": "#b54708", "warn-bg": "#fdf0e1",
    "bad": "#b42318", "bad-bg": "#fde8e6", "severe": "#7a0c0c",
    "shadow": "rgb(0 0 0 / .14)", "overlay": "rgb(0 0 0 / .35)", "edge": "rgb(128 128 128 / .35)", "knob": "#ffffff",
    # graded levels (pollen today): 1 is good-bg; 2 to 4 rising
    "lv2": "#f6e7b4", "lv2-ink": "#4a3b00", "lv3": "#f4c28f", "lv3-ink": "#4a2300", "lv4": "#eba0a0", "lv4-ink": "#4a0000",
    # twelve colours for things that are told apart by colour alone (map layers, graph nodes, chart lines)
    "series-1": "#1f6feb", "series-2": "#9333ea", "series-3": "#0891b2", "series-4": "#187847", "series-5": "#d9480f",
    "series-6": "#ca8a04", "series-7": "#c11574", "series-8": "#0e9384", "series-9": "#4f46e5", "series-10": "#b54708",
    "series-11": "#6b7280", "series-12": "#2f9e44",
    # surfaces: bevels (raised buttons and cards, sunken fields), fields, the title bar, links. Plain in Standard
    "raise": "none", "sink": "none", "field": "var(--card)", "bar": "color-mix(in srgb, var(--bg) 88%, transparent)",
    "bar-solid": "var(--bg)", "bar-ink": "var(--ink)", "bar-dim": "var(--dim)", "bar-here": "var(--chip)",
    "link": "var(--accent)", "link-done": "var(--accent)",
    # glass: a card's fill and what shows through it, and a picture behind the page. Plain in Standard
    "panel": "var(--card)", "panel-blur": "none", "page": "none",
}  # fmt: skip
_DARK = {
    "bg": "#12161d", "card": "#1b212b", "ink": "#e8ecf3", "dim": "#98a2b3", "line": "#2b3340", "chip": "#252d3a",
    "accent": "#6ea8fe", "on-accent": "#0b1220",
    "good": "#5fd39a", "good-bg": "#17352a", "warn": "#f4b26a", "warn-bg": "#3a2a14",
    "bad": "#ff8b82", "bad-bg": "#3d1c19", "severe": "#ff5c5c",
    "shadow": "rgb(0 0 0 / .45)", "overlay": "rgb(0 0 0 / .55)", "edge": "rgb(160 160 160 / .3)", "knob": "#ffffff",
    "lv2": "#4a3f12", "lv2-ink": "#f6e7b4", "lv3": "#4f3216", "lv3-ink": "#f4c28f", "lv4": "#4f1f1f", "lv4-ink": "#f3b4b4",
    "series-1": "#6ea8fe", "series-2": "#c084fc", "series-3": "#22d3ee", "series-4": "#5fd39a", "series-5": "#ff8f5a",
    "series-6": "#facc15", "series-7": "#f472b6", "series-8": "#2dd4bf", "series-9": "#818cf8", "series-10": "#f4b26a",
    "series-11": "#9ca3af", "series-12": "#86efac",
    # surfaces: bevels (raised buttons and cards, sunken fields), fields, the title bar, links. Plain in Standard
    "raise": "none", "sink": "none", "field": "var(--card)", "bar": "color-mix(in srgb, var(--bg) 88%, transparent)",
    "bar-solid": "var(--bg)", "bar-ink": "var(--ink)", "bar-dim": "var(--dim)", "bar-here": "var(--chip)",
    "link": "var(--accent)", "link-done": "var(--accent)",
    "panel": "var(--card)", "panel-blur": "none", "page": "none",
}  # fmt: skip
SURFACES = ("raise", "sink", "field", "bar", "bar-solid", "bar-ink", "bar-dim", "bar-here", "link", "link-done", "panel",
            "panel-blur", "page")
_DESIGN = {
    "font": 'system-ui, -apple-system, "Segoe UI", sans-serif',
    "font-head": "inherit",
    "radius": "14px",  # cards
    "radius-sm": "8px",  # inputs, buttons, small boxes
    "pill": "999px",  # chips, pills, tabs
    "h2-case": "uppercase",  # card headings
    "h2-track": ".06em",
    "head-weight": "650",
    "frame": "1px",  # the width of the line round cards, fields and buttons
}

THEMES: dict[str, dict[str, Any]] = {
    "standard": {"name": "Standard", "about": "Blue on grey: World Assistant's own look",
                 "light": {}, "dark": {}, "design": {}},
    "forest": {"name": "Forest", "about": "The app icon's greens",
               "light": {"bg": "#f3f5f1", "card": "#ffffff", "ink": "#14231a", "dim": "#5d6b60", "line": "#dde4dc",
                         "chip": "#e8efe6", "accent": "#2e7d4f", "good": "#1f7a45", "series-1": "#2e7d4f",
                         "series-4": "#1f6feb"},
               "dark": {"bg": "#0f1a13", "card": "#16241b", "ink": "#e6efe7", "dim": "#9aab9e", "line": "#26392c",
                        "chip": "#1e3025", "accent": "#6fcf97", "on-accent": "#0f1a13", "good": "#7ee0a6",
                        "good-bg": "#183a26", "series-1": "#6fcf97", "series-4": "#6ea8fe"},
               "design": {"radius": "16px"}},
    "contrast": {"name": "High contrast", "about": "Black and white with strong colours, for bright light or tired eyes",
                 "light": {"bg": "#ffffff", "card": "#ffffff", "ink": "#000000", "dim": "#333333", "line": "#6b6b6b",
                           "chip": "#ececec", "accent": "#0040c0", "good": "#006b2e", "good-bg": "#d9f2e2",
                           "warn": "#8a3c00", "warn-bg": "#ffe9cc", "bad": "#a10000", "bad-bg": "#ffdcdc",
                           "edge": "rgb(0 0 0 / .5)"},
                 "dark": {"bg": "#000000", "card": "#0a0a0a", "ink": "#ffffff", "dim": "#d0d0d0", "line": "#9a9a9a",
                          "chip": "#262626", "accent": "#8fb8ff", "on-accent": "#000000", "good": "#7ee2a8",
                          "good-bg": "#0b3320", "warn": "#ffc078", "warn-bg": "#3d2600", "bad": "#ff9c94",
                          "bad-bg": "#470c0c", "edge": "rgb(255 255 255 / .45)"},
                 "design": {"head-weight": "750"}},
    "paper": {"name": "Paper", "about": "Warm paper, serif headings and squarer corners",
              "light": {"bg": "#f7f3ea", "card": "#fffdf8", "ink": "#2b2620", "dim": "#706558", "line": "#e6dccb",
                        "chip": "#f0e8d8", "accent": "#a3471d", "good": "#456b22", "good-bg": "#eaf1dc",
                        "warn": "#8f5106", "warn-bg": "#f8ead2", "bad": "#a12a1d", "bad-bg": "#f7e0da"},
              "dark": {"bg": "#1c1915", "card": "#25211b", "ink": "#efe7da", "dim": "#b3a796", "line": "#3a332a",
                       "chip": "#2f2a22", "accent": "#e2895f", "on-accent": "#1c1915", "good": "#9bc76b",
                       "good-bg": "#26301a", "warn": "#e8a95a", "warn-bg": "#382813", "bad": "#f08c7c",
                       "bad-bg": "#3d1e19"},
              "design": {"font-head": '"Iowan Old Style", Palatino, Georgia, serif', "radius": "6px", "radius-sm": "4px",
                         "pill": "6px", "h2-case": "none", "h2-track": "0", "head-weight": "600"}},
    "win95": {"name": "Windows 95", "about": "Flat grey metal, bevelled buttons and navy title bars, as everything looked in 1995. "
                                           "Dark is its High Contrast Black",
              "light": {"bg": "#c0c0c0", "card": "#c0c0c0", "ink": "#000000", "dim": "#404040", "line": "#808080",
                        "chip": "#dfdfdf", "accent": "#000080", "on-accent": "#ffffff", "good": "#005a00",
                        "good-bg": "#c8e6c8", "warn": "#6b3a00", "warn-bg": "#ffffe1", "bad": "#800000",
                        "bad-bg": "#ffd0d0", "severe": "#800000", "edge": "#808080", "lv2": "#ffffe1",
                        "lv3": "#ffd8a8", "lv4": "#ffb0b0", "series-1": "#000080", "series-2": "#800080",
                        "series-3": "#008080", "series-4": "#008000", "series-5": "#ff0000", "series-6": "#808000",
                        "series-7": "#ff00ff", "series-8": "#00a0a0", "series-9": "#0000ff", "series-10": "#800000",
                        "series-11": "#808080", "series-12": "#00c000",
                        "raise": "inset -1px -1px #0a0a0a, inset 1px 1px #ffffff, inset -2px -2px #808080, inset 2px 2px #dfdfdf",
                        "sink": "inset -1px -1px #ffffff, inset 1px 1px #0a0a0a, inset -2px -2px #dfdfdf, inset 2px 2px #808080",
                        "field": "#ffffff", "bar": "#000080", "bar-solid": "#000080", "bar-ink": "#ffffff",
                        "bar-dim": "#dfdfdf", "bar-here": "#0e70b4", "link": "#0000ee", "link-done": "#551a8b"},
              "dark": {"bg": "#000000", "card": "#000000", "ink": "#ffffff", "dim": "#c0c0c0", "line": "#ffffff",
                       "chip": "#303030", "accent": "#00ffff", "on-accent": "#000000", "good": "#00ff00",
                       "good-bg": "#003000", "warn": "#ffff00", "warn-bg": "#303000", "bad": "#ff6060",
                       "bad-bg": "#400000", "severe": "#ff0000", "edge": "#c0c0c0",
                       "series-1": "#00ffff", "series-2": "#ff00ff", "series-3": "#00c0c0", "series-4": "#00ff00",
                       "series-5": "#ff8040", "series-6": "#ffff00", "series-7": "#ff80ff", "series-8": "#80ffc0",
                       "series-9": "#8080ff", "series-10": "#ffc000", "series-11": "#c0c0c0", "series-12": "#c0ff80",
                       "raise": "inset -1px -1px #ffffff, inset 1px 1px #ffffff, inset -2px -2px #808080, inset 2px 2px #808080", "sink": "inset -1px -1px #ffffff, inset 1px 1px #ffffff, inset -2px -2px #808080, inset 2px 2px #808080",
                       "field": "#000000", "bar": "#800080", "bar-solid": "#800080", "bar-ink": "#ffffff",
                       "bar-dim": "#ffffff", "bar-here": "#000000", "link": "#ffff00", "link-done": "#00ff00"},
              "design": {"font": 'Tahoma, "MS Sans Serif", "Microsoft Sans Serif", Arial, sans-serif',
                         "radius": "0", "radius-sm": "0", "pill": "0", "h2-case": "none", "h2-track": "0",
                         "head-weight": "700", "frame": "0px"}},
    "glass": {"name": "Glass", "about": "Frosted, see-through cards over soft colour, with rounder corners: the look of "
                                         "recent phones and Macs",
              "light": {"bg": "#eef1f7", "card": "#ffffff", "ink": "#1d1d1f", "dim": "#5c6170", "line": "rgb(120 130 150 / .22)",
                        "chip": "#e4e8f1", "accent": "#0062cc", "good": "#167a43", "good-bg": "#e3f4ea", "warn": "#a84a00",
                        "warn-bg": "#fdefdf", "bad": "#c0261b", "bad-bg": "#fde6e4", "shadow": "rgb(30 40 80 / .12)",
                        "panel": "color-mix(in srgb, #ffffff 58%, transparent)", "panel-blur": "blur(22px) saturate(1.8)",
                        "field": "color-mix(in srgb, #ffffff 80%, transparent)",
                        "raise": "inset 0 1px 0 rgb(255 255 255 / .8), 0 8px 28px rgb(30 40 80 / .10)",
                        "bar": "color-mix(in srgb, #eef1f7 55%, transparent)", "bar-here": "color-mix(in srgb, #ffffff 70%, transparent)",
                        "page": "radial-gradient(at 12% 8%, #cddcff 0, transparent 46%), radial-gradient(at 88% 14%, #f1d6ff 0, "
                                "transparent 42%), radial-gradient(at 70% 92%, #c9f0e4 0, transparent 46%), "
                                "radial-gradient(at 8% 85%, #ffe6cc 0, transparent 40%)"},
              "dark": {"bg": "#0b0e14", "card": "#1b2030", "ink": "#f2f4f8", "dim": "#a6aec0", "line": "rgb(255 255 255 / .12)",
                       "chip": "#252b3b", "accent": "#5aa2ff", "on-accent": "#06101f", "shadow": "rgb(0 0 0 / .5)",
                       "panel": "color-mix(in srgb, #1b2030 55%, transparent)", "panel-blur": "blur(22px) saturate(1.6)",
                       "field": "color-mix(in srgb, #1b2030 75%, transparent)",
                       "raise": "inset 0 1px 0 rgb(255 255 255 / .08), 0 10px 30px rgb(0 0 0 / .35)",
                       "bar": "color-mix(in srgb, #0b0e14 55%, transparent)", "bar-here": "color-mix(in srgb, #ffffff 12%, transparent)",
                       "page": "radial-gradient(at 12% 8%, #1d2f5c 0, transparent 46%), radial-gradient(at 88% 14%, #3b1f52 0, "
                               "transparent 42%), radial-gradient(at 70% 92%, #12403a 0, transparent 46%)"},
              "design": {"font": '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif',
                         "radius": "22px", "radius-sm": "12px", "h2-case": "none", "h2-track": "0", "head-weight": "600"}},
}  # fmt: skip
DEFAULT = "standard"
MODES = {"auto": "Automatic (as the device)", "light": "Light", "dark": "Dark"}

# Colours that are not ours to change: published scales and other people's marks. The same in every theme.
FIXED = {
    # the pollen allergy-risk scale, none to very high, as the pollen services draw it
    "pollen-0": "#ffffff", "pollen-1": "#8cc700", "pollen-2": "#f5e400", "pollen-3": "#f7a600", "pollen-4": "#e63a10",
    "w3w": "#e11f26",  # what3words' red, for its ///words
}  # fmt: skip
# A bike's own paint, chosen by name for its picture (Bikes, Its answers): real colours, the same in every theme
PAINTS = {
    "black": ("Black", "#1d1d1f"), "white": ("White", "#f4f4f2"), "silver": ("Silver", "#b8bcc2"), "grey": ("Grey", "#6b7078"),
    "red": ("Red", "#c8102e"), "orange": ("Orange", "#f06a14"), "yellow": ("Yellow", "#f5c400"), "green": ("Green", "#2e8b57"),
    "racing-green": ("Racing green", "#0f4d2f"), "blue": ("Blue", "#1f5fbf"), "metallic-blue": ("Metallic blue", "#2f5d9e"),
    "navy": ("Navy", "#1b2a4a"), "turquoise": ("Turquoise", "#21a5a5"), "purple": ("Purple", "#6c3fa0"),
    "pink": ("Pink", "#f06aa8"), "barbie-pink": ("Barbie pink", "#e0218a"), "bronze": ("Bronze", "#a3693a"),
    "gold": ("Gold", "#c9a227"), "copper": ("Copper", "#b0643c"), "titanium": ("Raw titanium", "#9a9a94"),
    "polished-aluminium": ("Polished aluminium", "#d2d5da"),
    "burnt-orange": ("Burnt orange (metallic copper)", "#c65a24"), "slate": ("Slate grey-blue", "#56606f"),
}  # fmt: skip

# The pollen map is a picture of its own (an SVG image file, not part of a page): drawn the same in every theme
IMAGE = {"sea": "#a8c8e0", "land": "#d9d9d9", "frame": "#999999", "paper": "#ffffff", "ink": "#222222", "small": "#555555"}


def tokens(theme: str, mode: str) -> dict[str, str]:
    """A theme's full set of colours for a mode, Standard's filling in what it leaves out."""
    t = THEMES.get(theme, THEMES[DEFAULT])
    base = _DARK if mode == "dark" else _LIGHT
    return {**base, **t[mode]}


def design(theme: str) -> dict[str, str]:
    return {**_DESIGN, **THEMES.get(theme, THEMES[DEFAULT])["design"]}


def _block(selector: str, values: dict[str, str], scheme: str | None = None) -> str:
    body = " ".join(f"--{k}: {v};" for k, v in values.items())
    return f"  {selector} {{ {('color-scheme: ' + scheme + '; ') if scheme else ''}{body} }}\n"


def css() -> str:
    """Every theme's tokens. The page's <html> says which theme (data-theme) and whether it is dark (data-dark);
    without the head script (no JavaScript), Standard follows the device."""
    out = _block(":root", {**FIXED, **_DESIGN, **_LIGHT}, "light")
    out += "  @media (prefers-color-scheme: dark) {\n" + _block(":root:not([data-mode])", _DARK, "dark") + "  }\n"
    out += _block(":root[data-dark]", _DARK, "dark")
    for tid, t in THEMES.items():
        if tid == DEFAULT:
            continue
        sel = f':root[data-theme="{tid}"]'
        out += _block(sel, {**_DESIGN, **t["design"], **tokens(tid, "light")}, "light")
        out += _block(sel + "[data-dark]", tokens(tid, "dark"), "dark")
    return out


# The server's default (Settings, Look: "for every device"): what a device that has not chosen shows. Loaded when the
# API starts and changed there (app/look.py).
SERVER = {"theme": DEFAULT, "mode": "auto"}


def head_script(theme: str = DEFAULT, mode: str = "auto") -> str:
    """Before the page is drawn: the theme and mode this device chose (else the server's default), and whether that
    comes out dark (following the device when automatic). window.waTheme(theme, mode) changes this device's choice;
    window.waTheme() with nothing forgets it, so the device follows the server's default again."""
    theme = theme if theme in THEMES else DEFAULT
    mode = mode if mode in MODES else "auto"
    return (
        "<script>(()=>{const r=document.documentElement,q=matchMedia('(prefers-color-scheme: dark)'),D=['" + theme + "','" + mode + "'];"
        "let t=D[0],m=D[1],own=false;try{own=!!(localStorage.getItem('wa_theme')||localStorage.getItem('wa_mode'));"
        "t=localStorage.getItem('wa_theme')||t;m=localStorage.getItem('wa_mode')||m}catch(e){}"
        "const set=()=>{r.dataset.theme=t;r.dataset.mode=m;r.toggleAttribute('data-dark',m==='dark'||(m==='auto'&&q.matches));"
        "dispatchEvent(new Event('wa-theme'))};"
        "window.waServer={theme:D[0],mode:D[1]};window.waOwn=()=>own;"
        "window.waTheme=(a,b)=>{if(a==null&&b==null){t=window.waServer.theme;m=window.waServer.mode;own=false;try{localStorage.removeItem('wa_theme');"
        "localStorage.removeItem('wa_mode')}catch(e){}}else{t=a||t;m=b||m;own=true;try{localStorage.setItem('wa_theme',t);"
        "localStorage.setItem('wa_mode',m)}catch(e){}}set()};"
        "q.addEventListener('change',set);set()})()</script>"
    )


HEAD_SCRIPT = head_script()


def catalogue() -> list[dict[str, Any]]:
    """The themes, with a few of their colours to show (Settings, Look)."""
    keys = ("bg", "card", "accent", "good", "warn", "bad")
    return [{"id": tid, "name": t["name"], "about": t["about"],
             "light": {k: tokens(tid, "light")[k] for k in keys}, "dark": {k: tokens(tid, "dark")[k] for k in keys}}
            for tid, t in THEMES.items()]  # fmt: skip
