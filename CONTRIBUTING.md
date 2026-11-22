# Contributing to Virpil LED Bridge

Thanks for wanting to help! Bug reports, panel drawings, rule ideas and code are all welcome.
Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting a bug

Open an [issue](https://github.com/eirikosten75/virpil-led-bridge/issues/new/choose) using the **Bug report** form, and
include:

- the version (shown next to the title in the control panel, and at the top of the tray icon's menu),
- MSFS 2020 or 2024, and the aircraft,
- which Virpil devices you use (name and PID),
- the relevant lines from the log (the *Log* panel in the control panel, or `bridge.log` next to `config.json`).

`bridge.log` can contain your Windows user name in file paths – remove anything you do not want to share.

## Suggesting a feature

Open an issue using the **Feature request** form. Describe what you want to see on the panel and in which situation,
not just how to build it.

## Sharing a panel drawing or rules

Drawings of more Virpil panels are very welcome – they go into the library in the [`layouts`](layouts) folder, one
file per panel. The easiest way:

1. Draw the panel with **Edit drawing** in the control panel (name the LEDs first with **Setup → Walk through one by
   one**, so the names match the buttons).
2. Copy your drawing from your own `layouts.json` into a new file in `layouts/`, and add the panel's PIDs, product
   name, status and LED command numbers – see [`layouts/README.md`](layouts/README.md).
3. Open a pull request.

No time for a pull request? Fill in the
[panel information form](https://github.com/eirikosten75/virpil-led-bridge/issues/new?template=panel_info.yml)
instead – the PID, the LED names and numbers, and (if you have one) your drawing are all that is needed. Panels we
especially lack: **Control Panel #3**, and any other Virpil device with LEDs.

Mark the drawing **verified** only if you checked it on a real panel; drawings made from pictures are **unverified**.
Please draw the panel yourself (outlines and buttons) – do not include Virpil's product photos.

Rules for specific aircraft (for example with LVARs) can be shared as an issue or pull request with the rule from
`config.json` and a short description of what it does.

## Contributing code

1. Fork the repository and create a branch for your change.
2. Install the requirements and run from Python (README, section 8):
   ```
   pip install SimConnect pystray pillow
   python virpil_bridge.py --dry-run -v
   ```
   `--dry-run` logs the LED commands instead of sending them, so you can test without the hardware.
3. Keep changes focused – one fix or feature per pull request.
4. Follow the existing style: plain Python 3 with type hints, no new dependencies unless needed, and the control panel
   (`ui.html`) as one self-contained file without external scripts.
5. Text in the program, the control panel and the docs is in English (British spelling).
6. Update `README.md` if the change affects how the program is used.
7. Test against MSFS if your change touches SimConnect, and say in the pull request what you tested.

You do not need to build the exe – that is done when a new release is made.

## Licence

By contributing, you agree that your contribution is licensed under the
[GNU General Public License v3.0](LICENSE), like the rest of the project.
