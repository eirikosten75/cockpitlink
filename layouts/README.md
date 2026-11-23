# Panel drawing library

One file per Virpil panel. The bridge has these built in: a panel in the library gets its drawing in the control
panel straight away, and **Find new devices** can take its LED names from here. Your own changes are kept in
`layouts.json` next to `config.json` and win over the library (see README, section 5).

| File | Panel | PIDs | Status |
|---|---|---|---|
| [control-panel-1.json](control-panel-1.json) | Control Panel #1 (and left-hand version) | 0259, 8259 | verified |
| [control-panel-2.json](control-panel-2.json) | Control Panel #2 | 025B | verified |
| [sharka-50-panel.json](sharka-50-panel.json) | SharKa-50 Panel | 025D | verified |
| [mongoost-50cm3-throttle.json](mongoost-50cm3-throttle.json) | MongoosT-50CM3 throttle | 0198 | verified |
| [control-panel-3.json](control-panel-3.json) | Control Panel #3 (drawn from a picture; LED numbers assumed as on the SharKa-50 Panel) | not known yet – recognised by product name | unverified |
| [vmax-prime-throttle.json](vmax-prime-throttle.json) | VMAX Prime Throttle (drawn from a picture; only the six KEYBOARD buttons, LED numbers assumed as on the MongoosT-50CM3) | not known yet – recognised by product name | unverified |

| [vector-gear-mcp.json](vector-gear-mcp.json) | Vector Gear MCP (module for the Vector Flight Yoke Base) | not known – choose by hand | unverified |
| [vector-lighting-mcp.json](vector-lighting-mcp.json) | Vector Lighting MCP (module) | not known – choose by hand | unverified |
| [vector-engine-mcp.json](vector-engine-mcp.json) | Vector Engine MCP (module) | not known – choose by hand | unverified |
| [vega-yoke-handle.json](vega-yoke-handle.json) | Constellation VEGA Yoke Handle | not known – choose by hand | unverified |
| [warbrd-grip.json](warbrd-grip.json) | VPC WarBRD Grip (one LED, grip LED 01) | a grip – picked from the grip name in the base's VPC profile, or by hand | verified |
| [flnkr-grip.json](flnkr-grip.json) | FLNKR Grip (two LEDs, assumed grip LEDs 01 and 02) | sits on different stick bases – choose by hand | unverified |

The last five can only be chosen by hand (**Drawings → Use a library drawing**), since nothing about them is known
that the bridge could recognise them by. Give the LEDs the names the drawing expects with **Setup**.

Own a Control Panel #3, a VMAX Prime Throttle or a Vector part? Please check the LEDs with **Setup → Walk through one by one** (and
**Try command number** for lights that are missing) and send the PID and any differences with the [panel information form](https://github.com/eirikosten75/virpil-led-bridge/issues/new?template=panel_info.yml).

## Format

```json
{
  "title": "Control Panel #1",
  "status": "verified",
  "checked": "How and when it was checked, or what it was made from",
  "pids": { "0259": "Control Panel #1", "8259": "Control Panel #1 (left)" },
  "product_names": ["VPC Panel #1", "LEFT VPC Panel #1"],
  "commands": { "b1": "16", "b2": "13" },
  "aspect": "1120 / 1300",
  "btn": 9.6,
  "deco": [ { "x": 6, "y": 3.5, "w": 35, "h": 33, "text": "EMERG  T1 · T2" } ],
  "leds": { "b1": [16.8, 62.8, "btn", "B1"], "b2": [32.9, 62.5, "btn", "B2"] }
}
```

- `status`: `verified` (checked on a real panel) or `unverified` (made from pictures or manuals – the control panel
  then asks the user to check it).
- `checked`: a short note on how it was checked.
- `pids`: the USB PIDs this drawing is for, each with the title shown in the control panel. Left- and right-hand
  versions of a panel share one file. May be empty (`{}`) when the PID is not known yet; the panel is then recognised
  by `product_names` only, and its drawing gets a key made from the file name (e.g. `CONTROL_PANEL_3`).
- `product_names`: the names Windows shows for the device (used when the PID has been changed in the VPC
  Configuration Tool). For a grip, the grip name in the VPC profile (e.g. `VPC WarBRD Grip`). With no `pids` and no
  `product_names`, the drawing can only be chosen by hand.
- `key` (optional): the drawing's key when it has no PID, e.g. `VECTOR_GEAR` (1–16 capital letters, digits or `_`).
- `commands`: LED name → command number for `VPC_LED_Control.exe` (as in `config.json`). Used for new devices found
  by **Find new devices**. Leave it out if you do not know them.
- `aspect`, `btn`, `deco`, `leds`: the drawing, in the same format as `layouts.json` (README, section 5). The names in
  `leds` must be the names in `commands`.

Draw the panels yourself – outlines and buttons only, no Virpil product photos.

Have a panel that is not here? Send its information with the
[panel information form](https://github.com/eirikosten75/virpil-led-bridge/issues/new?template=panel_info.yml), or
see [CONTRIBUTING.md](../CONTRIBUTING.md).
