# Microsoft Store listing for CockpitLink

Texts to paste into Partner Center (CockpitLink → submission). Language: English (United Kingdom).

## Product name
CockpitLink

## Short description (up to 100 characters)
Light the LEDs on your Virpil panels from Microsoft Flight Simulator 2020/2024.

## Description

CockpitLink makes the LEDs on your VIRPIL control panels follow what happens in Microsoft Flight Simulator 2020 and 2024 – gear down and locked, gear in transit, parking brake, flaps, canopy, autopilot, lights, warnings, or any L-variable your aircraft uses.

It runs quietly in the system tray, reads the simulator through SimConnect and drives the LEDs with VIRPIL's own VPC_LED_Control.exe. Everything is set up in a local control panel in your browser – no programming needed:

• Choose a panel, click an LED and make rules for it – like in SPAD.neXt.
• Search about 800 simulator variables, or use L-variables from your aircraft (A2A, PMDG, Fenix …).
• Colours with the four brightness levels the panels actually have, and blinking.
• Aircraft profiles: different rules for some aircraft, switched automatically.
• Drawings of your panels with the LEDs live, and an editor to draw your own.
• Back up and share your setup – or one panel – in a single file.
• A local API (HTTP and UDP) for SPAD.neXt, Stream Deck or your own scripts.

REQUIREMENTS
• VIRPIL controllers with LEDs (control panels, throttles, grips), with firmware updated by VIRPIL's VPC Software Suite VPC-JOY-20261027 or newer – the suite is free from VIRPIL and must be installed, as it contains VPC_LED_Control.exe.
• Microsoft Flight Simulator 2020 or 2024 on the same PC.

CockpitLink is free and open source (GPL-3.0): https://github.com/eirikosten75/cockpitlink
Not affiliated with VIRPIL Controls or Microsoft.

## What's new in this version
First version in the Microsoft Store. The same program as CockpitLink 3.2 on GitHub; your setup is kept in %LOCALAPPDATA%\CockpitLink and shared with the zip version.

## Features (one per line, up to 20)
Rules per LED: click an LED on the drawing of your panel and set its colours
Reads Microsoft Flight Simulator 2020/2024 through SimConnect
About 800 simulator variables, plus aircraft L-variables
Aircraft profiles switched automatically by aircraft name
Live drawings of your Virpil panels, and a drawing editor
Back up and share your setup in one file
Local HTTP and UDP API for SPAD.neXt, Stream Deck and scripts
Starts with Windows if you want, runs in the tray

## Keywords (up to 7)
Virpil, MSFS, flight simulator, LED, SimConnect, cockpit, SPAD.neXt

## Category
Utilities & tools (subcategory: none) – or Games → Simulation if Utilities is not accepted.

## Pricing
Free. Markets: all.

## Privacy policy URL
https://github.com/eirikosten75/cockpitlink/wiki/Privacy-policy

## Website / support
Website: https://github.com/eirikosten75/cockpitlink
Support: https://github.com/eirikosten75/cockpitlink/issues

## System requirements
There is no checkbox for depending on other hardware or software; the REQUIREMENTS part of the description covers it.
Properties → Product declarations and System requirements (keyboard, mouse, camera …) are optional – leave them empty.
If the Store listing page has "Additional system requirements", add under Minimum:
- VIRPIL controllers with LEDs (USB)
- VIRPIL VPC Software Suite VPC-JOY-20261027 or newer
- Microsoft Flight Simulator 2020 or 2024
The package itself requires Windows 10 version 2004 or newer, x64.

## Notes for certification
CockpitLink controls the LEDs of VIRPIL USB controllers from Microsoft Flight Simulator. To test it without the
hardware: start the app – it opens a tray icon; double-click it to open the control panel in the browser at
http://127.0.0.1:8765/. Without VIRPIL devices and the VPC Software Suite it shows no panels and reports that
VPC_LED_Control.exe was not found; this is expected. The app only listens on 127.0.0.1 (this PC).

## Restricted capabilities – why they are needed
- runFullTrust: CockpitLink is a desktop program. It runs VIRPIL's VPC_LED_Control.exe (part of the VIRPIL VPC
  Software Suite, installed separately) to set the LEDs, connects to Microsoft Flight Simulator through SimConnect,
  shows a tray icon and serves its control panel on 127.0.0.1.
- unvirtualizedResources: CockpitLink keeps the user's setup (config.json, layouts.json, backups) in
  %LOCALAPPDATA%\CockpitLink. The same program is also distributed as a standalone exe, which uses the same folder, so
  users keep their setup when they move between the two or reinstall. Without this, the Store version would see a
  separate copy and the setup would be deleted when the app is uninstalled.

## Age rating (IARC questionnaire) – suggested answers
Category: "Utility, productivity, communication or other". No violence, no user-to-user communication, no sharing of
location or personal information, no purchases, no gambling. Expected rating: 3+ / Everyone.

## Screenshots (1366×768 or larger, dark mode)
docs/images/overview-dark.png (Panels tab), docs/images/rules-dark.png (Rules tab),
docs/images/rule-editor-dark.png (rule editor). Store logo: docs/images/cockpitlink-logo.svg (300×300 PNG made from it).
