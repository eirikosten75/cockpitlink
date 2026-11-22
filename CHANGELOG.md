# Changelog

All notable changes to Virpil LED Bridge. Downloads are on the
[Releases page](https://github.com/eirikosten75/virpil-led-bridge/releases).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[1.5]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.5
[1.4]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.4
[1.3.3]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.3.3
[1.3.2]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.3.2
[1.3.1]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.3.1
[1.3]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.3
[1.2]: https://github.com/eirikosten75/virpil-led-bridge/releases/tag/v1.2
