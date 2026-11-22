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
  versions of a panel share one file.
- `product_names`: the names Windows shows for the device (used when the PID has been changed in the VPC
  Configuration Tool).
- `commands`: LED name → command number for `VPC_LED_Control.exe` (as in `config.json`). Used for new devices found
  by **Find new devices**. Leave it out if you do not know them.
- `aspect`, `btn`, `deco`, `leds`: the drawing, in the same format as `layouts.json` (README, section 5). The names in
  `leds` must be the names in `commands`.

Draw the panels yourself – outlines and buttons only, no Virpil product photos.

Have a panel that is not here? Send its information with the
[panel information form](https://github.com/eirikosten75/virpil-led-bridge/issues/new?template=panel_info.yml), or
see [CONTRIBUTING.md](../CONTRIBUTING.md).
