# Accessibility

Virpil LED Bridge is a small hobby project, but it should be usable by as many simmers as possible. This page
describes what works today, what does not, and how to tell us about barriers.

## What the project does

The bridge turns what happens in MSFS into light on the panel – the LEDs are an extra cue, not the only one. Everything
the LEDs show (gear, flaps, canopy …) is still shown in the cockpit, and in MSFS's own accessibility options.

## Supported environments

- **Windows 10 / 11** with Microsoft Flight Simulator 2020 or 2024.
- **Control panel:** a local web page (http://127.0.0.1:8765/) that works in any current browser – Edge, Chrome or
  Firefox. You can use the browser's own zoom, high-contrast and screen-reader support.
- **Tray icon:** a standard Windows tray menu, which works with the keyboard and Windows screen readers.

## What works today

- **Colours are your choice.** Every LED colour is set in your rules, so you can pick colours that are easy for you to
  tell apart – for example blue instead of red/green if you are colour-blind.
- **Not only colour.** A state can blink as well as change colour (e.g. gear in transit), so important changes can be
  shown in a way that does not depend on colour.
- **Brightness.** Each colour has four levels (off, dim, medium, full), so LEDs can be dimmed if they are too bright
  for you, or made brighter.
- **Text alongside colour in the control panel.** Rules show their states as text (“≥ 0.99 → geargreen”), and colours
  have names, not just swatches.
- **Light and dark mode** follow your Windows setting.
- **Keyboard:** the rule editor is a standard dialog (Esc closes it); the simvar search can be used with the arrow keys
  and Enter; LED names can be typed and saved with Enter.
- **Everything can also be done without the control panel:** `config.json` and `layouts.json` are plain text files
  that can be edited in any editor, and the LEDs can be driven from scripts through the API (see README).

## Known limitations

- **The panel drawings are mostly visual.** An LED's names, colour and rule are shown in a tooltip when you hover with
  the mouse; the drawings are not yet reachable with the keyboard or labelled for screen readers.
- **Few ARIA labels.** Some buttons and controls in the control panel rely on their visual position and may be read
  out unclearly by screen readers.
- **Blinking:** blinking LEDs in the drawing blink once a second and do not yet respect the *reduce motion* setting.
- **The physical LEDs** only have the colours and four brightness levels the Virpil hardware supports.
- **English only.** The program and documentation are in English.

## Reporting a barrier

If something in the bridge or the control panel is hard or impossible for you to use, please
[open an issue](https://github.com/eirikosten75/virpil-led-bridge/issues/new/choose) and describe:

- what you were trying to do,
- what got in the way,
- what you use (browser, screen reader, zoom level, Windows settings), if relevant.

Accessibility issues are treated as bugs. This is a spare-time project, so there is no guaranteed response time, but
reports are read and taken seriously.
