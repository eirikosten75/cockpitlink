# Virpil LED Bridge for MSFS 2020/2024

Drives the LEDs on Virpil controllers from what happens in MSFS. The program runs in the background on your PC and:

- **reads MSFS directly** via SimConnect, using rules you set up in the control panel (stored in `config.json`), and/or
- **accepts commands** over a local API (HTTP and UDP), from SPAD.neXt, Stream Deck, a script or the browser.

The LEDs themselves are driven through Virpil's own `VPC_LED_Control.exe`, which comes with the VPC Software Suite.

## ⬇️ Download

**[Download the latest version (VirpilLedBridge-v1.2.zip)](https://github.com/eirikosten75/virpil-led-bridge/releases/download/v1.2/VirpilLedBridge-v1.2.zip)**
– or see all versions under [Releases](https://github.com/eirikosten75/virpil-led-bridge/releases).

The zip contains `VirpilLedBridge.exe` (no Python needed), an example `config.json`, `layouts.json`, this README and
the licence. Unzip it to a folder of your choice and follow the quick start below. The example `config.json` is the
author's own setup – adapt it to your devices (section 2).

## 1. Quick start

1. Install the **VPC Software Suite** from Virpil (it contains `VPC_LED_Control.exe`).
2. Put `VirpilLedBridge.exe` and `config.json` in the same folder.
3. Check that `vpc_led_control.exe` in `config.json` points to your `VPC_LED_Control.exe`
   (normally `C:\Program Files (x86)\VPC Software Suite\tools\VPC_LED_Control.exe`).
4. Double-click `VirpilLedBridge.exe`. It needs no Python and has no console window.
5. Double-click the **tray icon** (it may be hidden under the ^ arrow) to open the control panel
   at http://127.0.0.1:8765/.

The bridge connects to MSFS automatically when the sim starts, and reconnects if the connection drops.

**Start with Windows:** tick *Start with Windows* in the tray icon's menu or at the top of the control panel. This adds
an entry for your Windows user only (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, no admin rights needed), and
unticking removes it again. It points to the exe where it is now; if you move the exe, tick it again. You can also see
and turn it off under Windows *Settings → Apps → Startup*.

## 2. Set up your own devices

`config.json` describes each Virpil device by its USB `vid`/`pid` (Virpil's VID is `3344`; VPC Configuration Tool shows
the PID) and lists its LEDs by **command number** – the number `VPC_LED_Control.exe` uses for that LED. On the boards
tested, 01–04 are the device LEDs and 05–24 are the LEDs on the board, in the same order as in VPC Configuration Tool.

You do not need to work out which physical LED is which by hand:

- **Setup** (button on each device in the control panel): click an LED and it blinks white on the physical panel.
  Give it a name (several names are separated by commas) and press Enter.
- **Walk through one by one** blinks every LED on the device in turn – type a name, press Enter, next.
- **Try command number** finds LEDs that are not in the config yet (01–54) and adds them.
- A name used by a rule can be swapped for exactly one new name – the rule is then updated too.
- Before every save, a copy of `config.json` is put in the `backups` folder (the latest 20 are kept).

**To test one LED by hand** from a command prompt:
```
"C:\Program Files (x86)\VPC Software Suite\tools\VPC_LED_Control.exe" 3344 025B 05 00 FF 00
```
If that LED lights green, the format is right. LEDs that should be driven from outside may need to be configured in
VPC Configuration Tool so that the buttons themselves do not control them.

**Brightness:** Virpil LEDs only have four levels per colour channel: off (00), dim (40), medium (80) and full (FF).
Other values are rounded to the nearest of these (tested: 20 turns the LED off).

## 3. The control panel

- **Top bar:** MSFS connection and aircraft, number of rules, uptime, when the config was last loaded.
  *Reload config* applies changes made to `config.json` by hand without a restart. If the file is invalid, the old
  config is kept and you are told what is wrong. *Quit bridge* sets the LEDs to their base colours and exits.
- **Rules:** each rule with its current value from MSFS and which state applies right now. Turn rules on/off, move them
  up/down, or press *Edit* / *New rule*. Rules are evaluated top to bottom; if several rules drive the same LED, the last one wins.
- **Rule editor:** search about 800 simvars (with description, unit and live value), choose device and LED, and set up
  states (condition → colour, optionally blinking). The colour picker only offers the four brightness levels the
  panels actually have. *More options* has “only aircraft whose name contains …” and an extra condition on another
  simvar (e.g. only aircraft without a tailwheel). The state that applies right now is highlighted while you edit.
- **Devices:** panels that have a drawing in `layouts.json` (Control Panel #1 and #2, SharKa-50 Panel and the
  MongoosT-50CM3 throttle) are drawn with their LEDs where they are on the real panel; other devices are shown as a
  grid. Hover over an LED to see its names, colour and which rule set it.
- **Log:** live, with a filter. The log is also written to `bridge.log` next to `config.json`.

Starting the exe while it is already running just opens the control panel. If the bridge is restarted, the control
panel reloads itself.

## 4. Rules in config.json

The rule editor writes these for you, but you can also edit `config.json` by hand:

```json
{
  "name": "Left gear",
  "simvar": "GEAR_LEFT_POSITION",
  "device": "cp2",
  "led": "gear_left",
  "states": [
    { "if": "moving", "color": "gearred", "blink": true },
    { "if": ">= 0.99", "color": "geargreen" },
    { "if": "<= 0.01", "color": "gearupblue" }
  ]
}
```
- The first `states` entry that matches is used. An entry without `if` works as “otherwise”.
- Conditions: `==`, `!=`, `>`, `>=`, `<`, `<=`, `between 1 99`, `moving`, `stopped`.
  `moving` is true while the value has changed within the last `moving_hold_ms` (default 700 ms).
- Colours: named (`green`, `red`, `amber` …), your own in `colors`, `"rgb": "008000"`, or `startup` (the LED's base colour).
- `aircraft_contains` (optional): the rule only applies when the aircraft title contains this text.
- `only_if` (optional): `{ "simvar": "IS_TAIL_DRAGGER", "if": "== 0" }` – when not met, the LED gets the colour in
  `otherwise` (default: the base colour).
- `enabled: false` turns a rule off without deleting it.
- Simvar names are written with underscores, as Python-SimConnect uses them (`AUTOPILOT_MASTER`, `LIGHT_NAV_ON`,
  `GENERAL_ENG_FUEL_PRESSURE:1`).

**Base colours** (`startup`) are what each LED shows when no rule or API call says otherwise – also when MSFS is
closed (`on_disconnect_color: "startup"`) and when the bridge quits (`on_exit: "startup"`).

**Limitation:** L-variables (LVARs) from aircraft such as Fenix and PMDG cannot be read by the rules. Some aircraft also
model their gauges with LVARs (e.g. the Spitfire's fuel pressure only appears in the simvar with the engine running).
For LVARs, use SPAD.neXt, which already reads them, and let SPAD send commands to the API (see section 7).

## 5. Panel drawings (layouts.json)

The drawings of the panels live in `layouts.json` next to `config.json`. If the file is missing, the bridge writes
out its built-in default on start, so you always have one to edit. Changes show up in the control panel within a
second – no rebuild or restart needed. If the file is invalid, the built-in drawings are used and the control panel
tells you what is wrong.

Each drawing is keyed by the device's USB PID (the same `pid` as in `config.json`):

```json
"0259": {
  "title": "Control Panel #1",
  "aspect": "1120 / 1300",
  "btn": 9.6,
  "deco": [ { "x": 6, "y": 3.5, "w": 35, "h": 33, "text": "EMERG  T1 · T2" } ],
  "leds": {
    "b7": [51.3, 10.8, "btn", "B7"],
    "ind_top": [41, 33, "rect"]
  }
},
"8259": { "same_as": "0259", "title": "Control Panel #1 (left)" }
```
- `leds`: one entry per LED **name** from `config.json`: `[x, y, shape, label]`. `x`/`y` are the centre in percent of
  the panel face (0,0 = top left). `shape` is `btn` (square button), `round` or `rect` (small indicators); the label is
  optional and is shown above the LED.
- `aspect`: width / height of the panel face. `btn`: button width in percent of the face width (default 10.5).
- `deco`: outlines for switches, knobs, levers etc. (`x`, `y`, `w`, `h` in percent).
- `same_as`: reuse another drawing (e.g. left- and right-hand versions of the same panel).
- LEDs that are not in the drawing are shown in a grid under it.

A quick way to make a drawing for a new panel: take a straight-on photo, measure each button's centre in pixels, and
divide by the panel's width/height in the photo.

## 6. API

| Call | Effect |
|---|---|
| `GET /led?device=cp2&led=gear_left&color=green` | Set a colour |
| `GET /led?device=cp2&led=b1&color=red&blink=1` | Blink |
| `GET /led?device=cp2&led=b1&rgb=FF8000` | Custom RGB |
| `GET /all?device=cp2&color=off` | All LEDs on a device |
| `GET /state` | Devices, colours and current state |

UDP on port 8766, plain text: `cp2 gear_left green` or `cp2 b1 red blink`.

The bridge only sends a command to an LED when its colour actually changes, so sending the same command often is fine.
The control panel's own `/api/...` endpoints that change things require the header `X-Bridge-UI: 1`.

## 7. SPAD.neXt

SPAD.neXt has a C# scripting interface where a class implementing `IScriptAction` can be bound to an event, and
`Execute(IApplication app, ISPADEventArgs eventArgs)` is called when the event fires. A minimal script that sends a
command to the bridge:

```csharp
using System.Net;
using SPAD.neXt.Interfaces;
using SPAD.neXt.Interfaces.Scripting;

public class GearLeftGreen : IScriptAction
{
    public void Execute(IApplication app, ISPADEventArgs eventArgs)
    {
        using (var wc = new WebClient())
            wc.DownloadString("http://127.0.0.1:8765/led?device=cp2&led=gear_left&color=green");
    }
}
```
This script has not been tested against SPAD. Namespaces and setup may differ in your SPAD version, and scripts need a
SPAD licence with scripting support. In SPAD, create an event on a data value or LVAR (“when the value changes /
equals X”) and choose the script action.

## 8. Running from Python and building the exe

```
pip install SimConnect pystray pillow
python virpil_bridge.py --dry-run -v   # test without hardware: logs the commands
python virpil_bridge.py                # normal operation
python virpil_bridge.py --no-sim       # API only, no SimConnect rules
python virpil_bridge.py --no-tray      # no tray icon (quit with Ctrl+C)
```

If you change `virpil_bridge.py` or `ui.html`, rebuild the exe with `.\build.ps1`
(requires `pip install pyinstaller SimConnect pystray pillow`). Changes to `config.json` and `layouts.json` need no
rebuild.

## Troubleshooting

- **Nothing happens:** run with `-v` and look at the command line that is logged. Run the same command by hand.
- **“Could not find VPC_LED_Control.exe”:** wrong path in `config.json`.
- **VPC_LED_Control fails:** VPC_LED_Control must be the same version as the VPC Configuration Tool the firmware
  setup was made with.
- **The control panel looks out of date:** press F5. The page also reloads itself when the bridge restarts.
- **Antivirus warning about the exe:** exes built with PyInstaller are sometimes flagged by mistake. Add an exception
  for the folder, or run from Python instead.

## Licence

Virpil LED Bridge is free software, released under the **GNU General Public License v3.0** – see [LICENSE](LICENSE).
You may use, change and share it; if you distribute a modified version, it must also be released under GPL-3.0 with
its source code.

Not affiliated with Virpil Controls or Microsoft. `VPC_LED_Control.exe` is part of Virpil's VPC Software Suite and is
not included here.
