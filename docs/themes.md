# Themes

Every page's colours and design language (fonts, corners, headings) come from one file, `app/themes.py`. Pages use
named tokens (`var(--accent)`, `var(--good)`, `var(--series-3)`); the map, graph and pollen charts read the same
tokens. `tests/test_themes.py` fails if a page writes a colour of its own, uses a token that does not exist, or if a
theme is missing a colour or is hard to read.

## Choosing
**Settings, Look**: a theme, and Automatic (follows the device), Light or Dark. Each device keeps its own choice.

| Theme | |
|---|---|
| Standard | Blue on grey: World Assistant's own look |
| Forest | The app icon's greens |
| High contrast | Black and white with strong colours |
| Paper | Warm paper, serif headings, squarer corners |

## Tokens
- **Colours** (each with a light and a dark value): `bg`, `card`, `ink`, `dim`, `line`, `chip`, `accent`, `on-accent`,
  `good`/`good-bg`, `warn`/`warn-bg`, `bad`/`bad-bg`, `severe`, `shadow`, `overlay`, `edge`, `knob`, graded levels
  `lv2`..`lv4` with their `-ink`, and `series-1`..`series-12` for things told apart by colour (map layers, graph
  nodes, chart lines).
- **Design**: `font`, `font-head`, `radius` (cards), `radius-sm` (inputs, buttons), `pill`, `h2-case`, `h2-track`,
  `head-weight`.
- **Fixed** (the same in every theme, not ours to change): the pollen risk scale `pollen-0`..`pollen-4`, and
  what3words' red `w3w`. The pollen map picture uses `IMAGE`.

## Adding a theme
Add an entry to `THEMES` with a name, a line about it, and only what differs from Standard in `light`, `dark` and
`design`. The test checks contrast: text 7:1 on its background, dim text and status colours 4.5:1.
