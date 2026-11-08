# Themes

Every page's colours and design language (fonts, corners, headings) come from one file, `app/themes.py`. Pages use
named tokens (`var(--accent)`, `var(--good)`, `var(--series-3)`); the map, graph and pollen charts read the same
tokens. `tests/test_themes.py` fails if a page writes a colour of its own, uses a token that does not exist, or if a
theme is missing a colour or is hard to read.

## Choosing
**Settings, Look**: a theme, and Automatic (follows the device), Light or Dark. **Make this the default for every
device** saves it on the server: any device that has not chosen its own shows it (from its next page). Choosing on a
device keeps that device's own choice in its browser; **Follow the server's default** forgets it again.

| Theme | |
|---|---|
| Standard | Blue on grey: World Assistant's own look |
| Forest | The app icon's greens |
| High contrast | Black and white with strong colours |
| Paper | Warm paper, serif headings, squarer corners |
| Glass | Frosted, see-through cards over soft colour, rounder corners and the system font: the look of recent phones and Macs |
| Windows 95 | Flat grey metal, bevelled buttons, sunken white fields, navy title bars, blue and purple links; dark is its High Contrast Black |

## Tokens
- **Colours** (each with a light and a dark value): `bg`, `card`, `ink`, `dim`, `line`, `chip`, `accent`, `on-accent`,
  `good`/`good-bg`, `warn`/`warn-bg`, `bad`/`bad-bg`, `severe`, `shadow`, `overlay`, `edge`, `knob`, graded levels
  `lv2`..`lv4` with their `-ink`, and `series-1`..`series-12` for things told apart by colour (map layers, graph
  nodes, chart lines).
- **Surfaces** (light and dark): `raise` and `sink` (bevels, as box shadows; none in Standard), `field` (inputs),
  `bar`, `bar-solid`, `bar-ink`, `bar-dim`, `bar-here` (the title bar and its tabs), `link`, `link-done`, and for glass `panel` (a card's fill),
  `panel-blur` (what shows through it, a backdrop filter) and `page` (a picture behind the page, such as gradients).
- **Design**: `font`, `font-head`, `radius` (cards), `radius-sm` (inputs, buttons), `pill`, `h2-case`, `h2-track`,
  `head-weight`, `frame` (the width of the line round cards, fields and buttons).
- **Fixed** (the same in every theme, not ours to change): the pollen risk scale `pollen-0`..`pollen-4`, and
  what3words' red `w3w`. The pollen map picture uses `IMAGE`.

## Adding a theme
Add an entry to `THEMES` with a name, a line about it, and only what differs from Standard in `light`, `dark` and
`design`. The test checks contrast: text 7:1 on its background, dim text and status colours 4.5:1.
