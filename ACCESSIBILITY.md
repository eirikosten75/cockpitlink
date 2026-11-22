# Accessibility

Virpil LED Bridge is a small hobby project, but it should be usable by as many simmers as possible. This page
describes what works today, what does not, and how to tell us about barriers.

## What the project does

The bridge turns what happens in MSFS into light on the panel – the LEDs are an extra cue, not the only one. Everything
the LEDs show (gear, flaps, canopy …) is still shown in the cockpit in MSFS.

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
- **Keyboard:** every LED in the panel drawings can be reached with Tab; its names, colour and rule are shown when it
  gets focus, just like on mouse hover. In *Setup*, Enter or Space on an LED makes it blink on the real panel and jumps
  to its name field. The rule editor is a standard dialog (Esc closes it); the simvar search can be used with the
  arrow keys and Enter; LED names can be typed and saved with Enter.
- **Screen readers:** each LED is announced with its names, colour, brightness, whether it blinks and which rule set
  it, and the description is kept up to date. Icon buttons (↑ ↓ ✕), the rule switches, search fields and setup fields
  have labels; messages after saving are announced; the panels' decorative outlines are hidden from screen readers.
- **Reduced motion:** if *Animation effects* is turned off in Windows (or *reduce motion* in your browser), LEDs in the
  drawing do not blink – blinking LEDs get a dashed ring instead. This only affects the control panel, not the real
  LEDs.
- **Everything can also be done without the control panel:** `config.json` and `layouts.json` are plain text files
  that can be edited in any editor, and the LEDs can be driven from scripts through the API (see README).

## Known limitations

- **Tab order in the drawings** follows the LED command numbers, not always the position on the panel.
- **The colour picker** is a grid of colour swatches; each has a name (e.g. “green, 008000”), but it works best with a
  mouse.
- **Not tested with every screen reader.** The labels follow web standards, but have not been checked with NVDA,
  JAWS or Narrator – reports are welcome.
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
