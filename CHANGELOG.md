# Changelog

All notable changes to Virpil LED Bridge. Downloads are on the
[Releases page](https://github.com/eirikosten75/virpil-led-bridge/releases).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [2.2.2] – 2026-11-24

### Changed
- The drawing of the chosen panel in the Panels tab is larger than in 2.2.1 (27 % of the window width, at least
  340 px, and never wider than the window). It still never gets larger when the window gets narrower.

## [2.2.1] – 2026-11-24

### Changed
- The control panel follows the size of the window everywhere. In the Panels tab the drawing of the chosen panel is
  smaller (it grows with the window width but never gets larger when the window gets narrower), its labels shrink with
  it, and the rules column sits right next to it. Below 1100 px the rules go under the panel; on phones everything is
  one column and the top bar scrolls away.

### Fixed
- On narrow windows and phones, the rule list, the Settings tab, the Drawings tab, Setup and the buttons on a panel's
  card no longer reach outside the window.

## [2.2] – 2026-11-24

### Changed
- **The Panels tab works like SPAD.neXt.** The names of your devices are listed on the left, the chosen device is shown
  large in the middle, and clicking one of its LEDs shows the rules for that LED on the right – turn them on/off, edit
  them, change their order, or press *+ New rule for this LED* to open the rule editor with the device and LED already
  filled in. *Identify* makes the LED blink on the panel. The Rules tab still lists all rules.

## [2.1] – 2026-11-24

### Added
- **Back up and share your setup.** *Settings → Export setup …* saves everything in one `.vlb-setup.json` file –
  devices, LED names, rules, aircraft profiles, base colours, own colours, your drawings and the general settings (not
  the paths and ports of this PC). *Export* on a panel's card saves just that panel with its drawing, rules and base
  colour, to share it or keep a copy.
- *Settings → Import …* shows what a file holds before anything changes. Per device you choose *Replace* (the matching
  device gets the file's LED names, drawing, rules and base colour), *Add as a new device* or *Skip*; settings only
  when ticked. `config.json` and `layouts.json` are backed up first, and files made for the old firmware are converted.

## [2.0.1] – 2026-11-24

### Fixed
- Old VPC profiles were taken for the new form. The *Update firmware* warning did not show for devices still on old
  firmware, and *Find new devices* did not read the LEDs from old profiles. Now only the zero-padded fields
  (`cnt_led_rgb_01`–`09`) mark the new form.

## [2.0] – 2026-11-23

### Changed – needs VPC Software Suite VPC-JOY-20261027 and updated firmware
- **Requires Virpil's VPC Software Suite VPC-JOY-20261027 (or newer) with the firmware of your devices updated.** That
  firmware numbers the LEDs on the boards 01–20 instead of 05–24 (checked on Control Panel #1 and #2, SharKa-50 and
  MongoosT-50CM3). The bridge now uses these numbers everywhere – the same as VPC Configuration Tool shows.
- An existing `config.json` is converted automatically on the first start (05–24 → 01–20). The old file is copied to
  `backups`; LED names and rules are unchanged. `config.example.json` and the drawing library use the new numbers.
- The LED control status in the top bar warns when the VPC software is older than 20261027 (*VPC software too old*) or
  when a device's VPC profile is in the old form, a sign of old firmware (*Update firmware*). *Settings → About* shows
  the VPC software version.
- *Find new devices* reads the new VPC profile form; stick bases are recognised by the grip named in the profile.

## [1.9] – 2026-11-23

### Added
- **LED control status** in the top bar, next to *MSFS connected*: green when `VPC_LED_Control.exe` is found, the last
  write to a panel worked and all devices are plugged in (e.g. *LEDs OK · 5/5*); yellow when a device is missing; red
  when the exe is not found or writes fail. The tooltip shows the path, writes and failures, the last error and the
  devices that are not plugged in.

### Fixed
- When MSFS briefly gave no aircraft name, the log showed the aircraft as unknown and back again every few seconds,
  and an aircraft profile could switch back and forth. A missing name now only counts after 5 seconds.

## [1.8] – 2026-11-23

### Added
- **Control Panel #3** in the drawing library, marked *unverified*: its own drawing with its button labels (B1–B4,
  B8–B13), and LED numbers assumed to match the SharKa-50 Panel, which has the same controls. Its USB PID is not known
  yet, so **Find new devices** recognises it by its product name ("VPC Control Panel 3"). Owners: please check the
  LEDs with *Walk through one by one* and send the PID with the panel information form.
- **VMAX Prime Throttle** in the drawing library, marked *unverified*: drawn from a picture with its six KEYBOARD
  buttons, LED numbers assumed to match the MongoosT-50CM3 throttle (05–10). Other lights (EMERG B7, the encoder
  rings) may also be LEDs; find them with *Try command number*. Recognised by its product name ("VPC VMAX Prime
  Throttle").
- **Use a library drawing** in the *Drawings* tab: choose any library drawing for a panel by hand – for a panel the
  bridge does not recognise, or a module you have swapped. Optionally adds the library's LED names as extra names
  (existing names and rules keep working).
- Spots in a drawing that no LED on the panel has yet are shown dashed, and *Setup* lists the names the drawing
  expects: select an LED and click a name.
- **Vector Gear MCP, Vector Lighting MCP, Vector Engine MCP** and the **Constellation VEGA Yoke Handle** in the drawing
  library, drawn from pictures and marked *unverified*. Their PIDs, product names and LED numbers are not known, so
  they are chosen by hand with *Use a library drawing*.
- **Stick bases in Find new devices:** bases without LEDs of their own are no longer left out. They are listed (not
  ticked) with the four LEDs a grip can have, `grip_led1`–`grip_led4` (commands 01–04, the grip LEDs in the VPC
  profile). Tested: a grip LED on a WarBRD-D base answered to command 01.
- **VPC WarBRD Grip** in the drawing library, *verified*: one LED (grip LED 01), checked on a real grip. When a stick
  base's VPC profile says the grip is a VPC WarBRD Grip, **Find new devices** uses this drawing and LED name straight
  away.
- **FLNKR Grip** in the drawing library, drawn from a picture and marked *unverified*: its two LEDs are assumed to be
  grip LEDs 01 and 02. A grip can sit on different bases, so it is chosen by hand with *Use a library drawing*.
- Library drawings can be recognised by product name alone (`"pids": {}`), for panels whose PID is not known yet, or
  be chosen by hand only (no PIDs and no product names, with an optional `"key"`).

## [1.7.1] – 2026-11-23

### Changed
- The panel list in the *Drawings* tab shows each drawing once: panels that share a drawing (such as the left- and
  right-hand Control Panel #1) are one entry, e.g. "Control Panel #1 – VPC Panel #1, L-VPC Panel #1".

## [1.7] – 2026-11-23

### Added
- **Aircraft profiles** (off by default – turn them on in *Settings*): extra rules for some aircraft. A profile's rules
  take over the LEDs they drive, and every other LED keeps the rules for all aircraft (or, if you choose, only the
  profile's rules are used).
  - Chosen automatically from the aircraft name when MSFS is connected through SimConnect, or by hand at the top of
    the control panel or in the tray menu; a choice made by hand stays until *Automatic* is chosen again.
  - Made and edited in the *Rules* tab, including **+ Profile for this aircraft** and *Copy rules from all aircraft*.
  - Profiles can be exported to a file and imported again – devices are matched by name, PID or product name.
- The control panel is organised in tabs: *Panels*, *Rules*, *Drawings*, *Log* and *Settings*. The page remembers the
  last tab, and each has its own address (e.g. `#rules`).

### Changed
- The drawing editor is now the *Drawings* tab instead of a pop-up window, with a list to choose the panel.
- *Start with Windows* and *Find new devices* moved from the top bar to the *Settings* and *Panels* tabs.
- A choice of profile made by hand is saved in `config.json` without making a backup, so it does not push real backups
  out of the `backups` folder.

## [1.6] – 2026-11-22

### Added
- A library of panel drawings in the `layouts` folder (built into the exe): one file per panel, marked verified or
  unverified, with the panel's PIDs, product names and LED command numbers. Panels in the library get their drawing
  straight away, and new panels found by **Find new devices** can get their LED names from it.
- The control panel shows where each drawing comes from (library, changed by you, or your own), and warns about
  library drawings that have not been checked on a real panel.
- Library drawings can be changed with **Edit drawing**: your changes are saved as your own copy in `layouts.json`,
  and **Go back to the library drawing** removes them again. Your own drawings can be deleted the same way.

### Changed
- `layouts.json` now holds only your own drawings and changes, and is no longer in the zip or the repository, so
  updates never overwrite it. An existing `layouts.json` keeps working; drawings in it that are the same as the
  library are simply treated as library drawings.

## [1.5] – 2026-11-22

### Added
- Drawing editor in the control panel (**Edit drawing** / **Make drawing** on each device card): drag LEDs into
  place, change shape and label, draw boxes for switches and knobs, and trace a photo of your own panel. Saves to
  `layouts.json` with a backup; photos stay on your PC in the `photos` folder.

## [1.4] – 2026-11-22

### Added
- Automatic device detection: on the first start (no `config.json`) the bridge finds the connected Virpil devices
  and makes `config.json` from them. Known panels (Control Panel #1 and #2, SharKa-50, MongoosT-50CM3) get checked
  LED names, a drawing and example rules; they are recognised by USB PID, or by product name if the PID has been
  changed. Other devices get the LEDs from their VPC Configuration Tool profile, named `led01`, `led02` …
- **Find new devices** in the control panel, for devices plugged in later.
- `config.example.json`: the template and the list of known panels (also built into the exe).
- A device in `config.json` can use another panel's drawing with `"layout": "<PID>"`.

### Changed
- `config.json` is no longer in the zip or the repository, so an update never overwrites your own setup.

## [1.3.3] – 2026-11-22

### Changed
- Faster start: the panels get their base colours in parallel (one queue per USB device instead of one for all),
  and LEDs driven by rules are set first. With five panels and 57 LEDs, start-up went from about 35 s to about 15 s.
- Faster quit for the same reason.

## [1.3.2] – 2026-11-22

### Added
- The exe is built by GitHub Actions from the source code, with a PyInstaller bootloader compiled from source in the
  same run (fewer antivirus false positives). The release notes link to the build log, the SHA-256 and a VirusTotal
  report.
- Screenshots in the README, a social preview image and this changelog.
- Accessibility in the control panel: every LED in the panel drawings can be reached with Tab and shows its details
  when focused; Enter or Space on an LED in *Setup* makes it blink on the real panel.
- Screen-reader labels for LEDs (names, colour, brightness, blinking, which rule set it), icon buttons, rule switches,
  search and setup fields; messages after saving are announced.
- Reduced motion: with animation effects turned off in Windows, LEDs in the drawing do not blink but get a dashed ring.
- `ACCESSIBILITY.md`, community health files (code of conduct, contributing guide, security policy, issue and pull
  request templates) and a [wiki](https://github.com/eirikosten75/virpil-led-bridge/wiki).

## [1.3.1] – 2026-11-22

### Added
- The version number is shown in the control panel (next to the title and in the browser tab), at the top of the
  tray icon's menu and in its tooltip.

## [1.3] – 2026-11-22

### Added
- L-variables (LVARs) in rules: write `L:name` (or SPAD.neXt's `LVAR:name`) as the simvar. They are read directly
  through SimConnect – no SPAD.neXt, MobiFlight or other WASM module needed.

## [1.2] – 2026-11-21

First public release.

### Added
- Rules that set LEDs from MSFS simvars, with conditions (`==`, `<`, `between`, `moving` …), blinking, base colours,
  `only_if` / `otherwise` and `aircraft_contains`.
- Local web control panel with live values, a rule editor with simvar search and a colour picker limited to the
  brightness levels Virpil LEDs support.
- LED setup by clicking: identify LEDs on the real panel, name them, walk through them one by one, try command numbers.
- Panel drawings for Control Panel #1 and #2, the SharKa-50 panel and the MongoosT-50CM3 throttle, in an editable
  `layouts.json`.
- HTTP and UDP API for SPAD.neXt, Stream Deck and scripts.
- Tray icon, *Start with Windows*, `config.json` backups, licensed under GPL-3.0.

[2.0]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v2.0
[1.9]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.9
[1.8]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.8
[1.7.1]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.7.1
[1.7]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.7
[1.6]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.6
[1.5]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.5
[1.4]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.4
[1.3.3]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.3.3
[1.3.2]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.3.2
[1.3.1]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.3.1
[1.3]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.3
[1.2]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.2
