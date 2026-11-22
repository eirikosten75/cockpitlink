#!/usr/bin/env python3
"""
Virpil LED Bridge for MSFS 2020/2024
====================================

Drives the LEDs on Virpil controllers from what happens in the simulator.

Three ways to set LEDs:
  1. Rules in config.json that read SimConnect variables directly from MSFS.
  2. HTTP API (e.g. from a SPAD.neXt script, Stream Deck, a browser, curl):
        GET http://127.0.0.1:8765/led?device=cp2&led=gear_left&color=green
        GET http://127.0.0.1:8765/led?device=cp2&led=gear_left&color=red&blink=1
        GET http://127.0.0.1:8765/led?device=cp2&led=gear_left&rgb=FF8000
        GET http://127.0.0.1:8765/all?device=cp2&color=off
        GET http://127.0.0.1:8765/state
  3. UDP (low latency), one command per packet:
        "cp2 gear_left green"
        "cp2 gear_left red blink"

The control panel (a web page) is at http://127.0.0.1:8765/. It shows the panels live,
MSFS status and the log, lets you name LEDs and edit rules, and reloads config.json
without a restart.

The LEDs are written through Virpil's own VPC_LED_Control.exe, which comes with the
VPC Software Suite.

Run:
    python virpil_bridge.py                 # normal operation (with a tray icon)
    python virpil_bridge.py --dry-run       # no hardware, log only
    python virpil_bridge.py --no-sim        # API only, no SimConnect
    python virpil_bridge.py --no-tray       # no tray icon (quit with Ctrl+C)
    python virpil_bridge.py --open          # open the control panel in the browser on start
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import logging.handlers
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "1.3"

log = logging.getLogger("virpil")

DEFAULT_COLORS = {
    "off":    (0x00, 0x00, 0x00),
    "red":    (0xFF, 0x00, 0x00),
    "green":  (0x00, 0xFF, 0x00),
    "blue":   (0x00, 0x00, 0xFF),
    "yellow": (0xFF, 0xFF, 0x00),
    "orange": (0xFF, 0x40, 0x00),
    "amber":  (0xFF, 0x80, 0x00),
    "cyan":   (0x00, 0xFF, 0xFF),
    "purple": (0xFF, 0x00, 0xFF),
    "white":  (0xFF, 0xFF, 0xFF),
    "dimgreen": (0x00, 0x40, 0x00),
    "dimred":   (0x40, 0x00, 0x00),
}

# Virpil LEDs only have these levels per channel (tested: 0x20 turns the LED off). We round to the nearest.
VIRPIL_LEVELS = (0x00, 0x40, 0x80, 0xFF)


def quantize(v: int) -> int:
    return min(VIRPIL_LEVELS, key=lambda lvl: abs(lvl - v))


def app_dir() -> Path:
    """The folder with config.json: next to the exe, or next to the script."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def resource_dir() -> Path:
    """The folder with files bundled inside the exe (ui.html)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


# --------------------------------------------------------------------------
# Log that can also be shown in the control panel
# --------------------------------------------------------------------------
class RingHandler(logging.Handler):
    """Keeps the latest log lines in memory, numbered, so the web page can fetch new ones."""

    def __init__(self, size: int = 500):
        super().__init__()
        self.lines: collections.deque[tuple[int, str, str]] = collections.deque(maxlen=size)
        self._next = 1
        self._lock_ = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001
            return
        with self._lock_:
            self.lines.append((self._next, record.levelname, text))
            self._next += 1

    def since(self, after: int) -> list[dict]:
        with self._lock_:
            return [{"id": i, "level": lvl, "text": t} for i, lvl, t in self.lines if i > after]


# --------------------------------------------------------------------------
# LED writer
# --------------------------------------------------------------------------
class LedWriter:
    """Writes colours to LEDs through VPC_LED_Control.exe, with a cache and blinking."""

    def __init__(self, cfg: dict, dry_run: bool = False):
        self.dry_run = dry_run
        self.base: dict[tuple[str, str], str] = {}  # (device, command) -> base colour
        self.frozen = False  # set when shutting down

        self._lock = threading.Lock()
        self._io_lock = threading.Lock()  # one VPC_LED_Control command at a time
        # what is actually on the LED, per physical LED (device, command number), so aliases share the cache
        self._written: dict[tuple[str, str], tuple[int, int, int]] = {}
        self._wanted: dict[tuple[str, str], dict] = {}  # wanted state per name (colour + blink)
        self._phys: dict[tuple[str, str], dict] = {}    # wanted state per physical LED, for the control panel
        self._override: set[tuple[str, str]] = set()   # physical LEDs blinking so they can be identified
        self._blink_phase = True
        self.configure(cfg)
        threading.Thread(target=self._blink_loop, daemon=True, name="blink").start()

    def configure(self, cfg: dict) -> None:
        """Applies a (new) config. Forgets wanted states, but remembers what is on the LEDs."""
        with self._lock:
            self.exe = cfg["vpc_led_control"]["exe"]
            self.template = cfg["vpc_led_control"]["command_template"]
            self.devices = cfg["devices"]
            self.colors = {**DEFAULT_COLORS, **{k: tuple(v) for k, v in cfg.get("colors", {}).items()}}
            self.blink_interval = cfg.get("blink_interval_ms", 500) / 1000.0
            self._wanted.clear()
            self._phys.clear()
            known = {(d, c) for d, v in self.devices.items() for c in v["leds"].values()}
            self._written = {k: v for k, v in self._written.items() if k in known}
        if not self.dry_run and not Path(self.exe).exists():
            log.warning("Could not find %s – check 'vpc_led_control.exe' in config.json", self.exe)

    # ---- public API ----
    def resolve_color(self, color: str | None = None, rgb: str | None = None) -> tuple[int, int, int]:
        if rgb:
            rgb = rgb.strip().lstrip("#")
            if not re.fullmatch(r"[0-9a-fA-F]{6}", rgb):
                raise ValueError(f"Invalid rgb '{rgb}', use e.g. FF8000")
            r, g, b = (int(rgb[i:i + 2], 16) for i in (0, 2, 4))
        else:
            name = (color or "off").lower()
            if name not in self.colors:
                raise ValueError(f"Unknown colour '{color}'. Known: {', '.join(sorted(self.colors))}")
            r, g, b = self.colors[name]
        return quantize(r), quantize(g), quantize(b)

    def set(self, device: str, led: str, color: str | None = None,
            rgb: str | None = None, blink: bool = False, source: str = "?") -> None:
        devices = self.devices
        if device not in devices:
            raise ValueError(f"Unknown device '{device}'. Known: {', '.join(devices)}")
        if led not in devices[device]["leds"]:
            raise ValueError(f"Unknown LED '{led}' on '{device}'. Known: {', '.join(devices[device]['leds'])}")
        if self.frozen and source != "shutdown":
            return  # the bridge is shutting down; rules and the API may not override the final colours
        cmd = devices[device]["leds"][led]
        if not rgb and color and color.lower() == "startup":
            # "startup" = the LED's base colour from 'startup' in the config (or off if it has none)
            color = self.base.get((device, cmd), "off")
        value = self.resolve_color(color, rgb)
        key = (device, led)
        with self._lock:
            new = {"rgb": value, "blink": bool(blink)}
            if self._wanted.get(key) == new:
                return
            self._wanted[key] = new
            self._phys[(device, cmd)] = {**new, "led": led, "color": color or rgb, "source": source}
        log.info("[%s] %s/%s -> %s%s", source, device, led, color or rgb, " (blink)" if blink else "")
        self._apply(key)

    def set_all(self, device: str, color: str = "off", source: str = "?") -> None:
        done = set()
        for led, cmd in self.devices[device]["leds"].items():
            if cmd not in done:  # once per physical LED
                done.add(cmd)
                self.set(device, led, color=color, source=source)

    def is_taken(self, device: str, led: str) -> bool:
        """True if this physical LED (under any of its names) has already been set."""
        cmd = self.devices[device]["leds"][led]
        with self._lock:
            return (device, cmd) in self._phys

    def clear_wanted(self) -> None:
        """Forgets wanted states (stops blinking), but keeps the cache of what is on the LEDs."""
        with self._lock:
            self._wanted.clear()
            self._phys.clear()

    def state(self) -> dict:
        with self._lock:
            return {f"{d}/{l}": {"rgb": "%02X%02X%02X" % s["rgb"], "blink": s["blink"]}
                    for (d, l), s in self._wanted.items()}

    def phys_state(self) -> dict:
        """Wanted state per physical LED: {(device, command): {...}}."""
        with self._lock:
            return {k: {**v, "rgb": "%02X%02X%02X" % v["rgb"]} for k, v in self._phys.items()}

    def identify(self, device: str, cmd: str, seconds: float = 4.0) -> None:
        """Blinks one physical LED white/off for a moment so it can be found on the panel, then
        restores it. Also works for command numbers that are not in the config yet."""
        if device not in self.devices:
            raise ValueError(f"Unknown device '{device}'")
        if not re.fullmatch(r"\d{2}", cmd) or not 1 <= int(cmd) <= 54:
            raise ValueError(f"Invalid command number '{cmd}' (use 01–54)")
        phys = (device, cmd)
        with self._lock:
            if phys in self._override:
                log.info("[identify] %s/%s is already blinking", device, cmd)
                return
            self._override.add(phys)
            prev = self._written.get(phys)
            dev, exe, template = self.devices[device], self.exe, self.template
        log.info("[identify] %s/%s blinking", device, cmd)

        def run() -> None:
            try:
                end, on = time.monotonic() + seconds, True
                while time.monotonic() < end:
                    with self._io_lock:
                        self._write(exe, template, dev, cmd, (0xFF, 0xFF, 0xFF) if on else (0, 0, 0))
                    on = not on
                    time.sleep(0.12)
            finally:
                with self._lock:
                    self._override.discard(phys)
                    self._written.pop(phys, None)
                    st = self._phys.get(phys)
                    key = (device, st["led"]) if st else None
                if key and key in self._wanted:
                    self._apply(key)
                else:
                    restore = prev or (0, 0, 0)
                    with self._io_lock:
                        self._write(exe, template, dev, cmd, restore)
                    with self._lock:
                        self._written[phys] = restore

        threading.Thread(target=run, daemon=True, name="identify").start()

    # ---- internal ----
    def _apply(self, key: tuple[str, str]) -> None:
        # _io_lock makes the writes reach the LEDs in the same order as the cache is updated.
        # Without it, two threads (e.g. startup and a rule) can start VPC_LED_Control at the same time,
        # and the older command can end up last on the LED while the cache shows the newer one.
        with self._io_lock:
            with self._lock:
                want = self._wanted.get(key)
                if want is None or key[0] not in self.devices or key[1] not in self.devices[key[0]]["leds"]:
                    return
                rgb = want["rgb"] if (not want["blink"] or self._blink_phase) else (0, 0, 0)
                phys = (key[0], self.devices[key[0]]["leds"][key[1]])
                if phys in self._override:
                    return  # the LED is blinking to be identified; it is restored afterwards
                if self._written.get(phys) == rgb:
                    return
                self._written[phys] = rgb
                dev = self.devices[key[0]]
                exe, template = self.exe, self.template
            self._write(exe, template, dev, phys[1], rgb)

    def _write(self, exe: str, template: str, dev: dict, cmd: str, rgb: tuple[int, int, int]) -> None:
        args = template.format(
            exe=exe, vid=dev["vid"], pid=dev["pid"], cmd=cmd,
            r="%02X" % rgb[0], g="%02X" % rgb[1], b="%02X" % rgb[2],
        )
        if self.dry_run:
            log.debug("DRY-RUN: %s", args)
            return
        try:
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            res = subprocess.run(args, shell=False if os.name == "nt" else True,
                                 capture_output=True, text=True, timeout=3, creationflags=flags)
            if res.returncode != 0:
                log.warning("VPC_LED_Control failed (%s): %s %s", res.returncode, res.stdout.strip(), res.stderr.strip())
        except Exception as e:  # noqa: BLE001
            log.error("Could not run VPC_LED_Control: %s", e)

    def _blink_loop(self) -> None:
        while True:
            time.sleep(self.blink_interval)
            with self._lock:
                self._blink_phase = not self._blink_phase
                blinking = [k for k, s in self._wanted.items() if s["blink"]]
            for key in blinking:
                self._apply(key)


# --------------------------------------------------------------------------
# Rule engine (SimConnect)
# --------------------------------------------------------------------------
_COND_RE = re.compile(r"^\s*(==|!=|>=|<=|>|<|between)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*$")


def check_condition(cond: str | None, value: float, moving: bool = False) -> bool:
    """Evaluates conditions like '== 100', '> 0.5', 'between 1 99', 'moving', 'stopped'.
    An empty condition is always true."""
    if cond is None or cond.strip() == "":
        return True
    word = cond.strip().lower()
    if word == "moving":
        return moving
    if word == "stopped":
        return not moving
    m = _COND_RE.match(cond)
    if not m:
        raise ValueError(f"Invalid condition '{cond}'")
    op, a, b = m.group(1), float(m.group(2)), m.group(3)
    if op == "==":
        return abs(value - a) < 1e-6
    if op == "!=":
        return abs(value - a) >= 1e-6
    if op == ">":
        return value > a
    if op == ">=":
        return value >= a
    if op == "<":
        return value < a
    if op == "<=":
        return value <= a
    if op == "between":
        if b is None:
            raise ValueError(f"'between' needs two numbers: '{cond}'")
        return a <= value <= float(b)
    return False


_CATALOG: list[dict] | None = None


def simvar_catalog() -> list[dict]:
    """Every simvar Python-SimConnect knows, with description and unit (read once)."""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    out: list[dict] = []
    try:
        import inspect

        from SimConnect.RequestList import AircraftRequests
        for attr in dir(AircraftRequests):
            group = getattr(AircraftRequests, attr)
            lst = getattr(group, "list", None) if inspect.isclass(group) else None
            if not isinstance(lst, dict):
                continue
            category = attr.split("__")[-1]
            if category == "AircraftStringData":
                continue  # text values cannot be used in conditions
            category = re.sub(r"^Aircraft", "", category)
            category = re.sub(r"(?<!^)(?=[A-Z])", " ", category).strip()
            for name, spec in lst.items():
                unit = spec[2].decode(errors="ignore") if isinstance(spec[2], bytes) else str(spec[2])
                out.append({"name": name, "desc": spec[0], "unit": unit, "category": category})
    except Exception as e:  # noqa: BLE001
        log.warning("Could not find the simvar list in the SimConnect package: %s", e)
    _CATALOG = sorted(out, key=lambda s: s["name"])
    return _CATALOG


LVAR_RE = re.compile(r"L:[A-Za-z0-9_.\-:]+")


def is_lvar(name: str) -> bool:
    """True for an L-variable name such as 'L:FUEL_IndicatorButton_IsDown'."""
    return str(name).startswith("L:")


def simvar_known(name: str) -> bool:
    """True if the simvar exists. 'NAME:3' is accepted when the list has 'NAME:index'.
    L-variables ('L:name') cannot be listed, so any well-formed L: name is accepted."""
    if is_lvar(name):
        return bool(LVAR_RE.fullmatch(name))
    cat = simvar_catalog()
    if not cat:
        return True  # without the list we cannot check; let the rule try
    names = {s["name"] for s in cat}
    if name in names:
        return True
    m = re.fullmatch(r"(.+):(\d+)", name)
    return bool(m and f"{m.group(1)}:index" in names)


def search_simvars(q: str, limit: int = 40) -> list[dict]:
    words = [w for w in re.split(r"[\s_]+", q.lower()) if w]
    res = []
    for s in simvar_catalog():
        hay = (s["name"] + " " + s["desc"] + " " + s["category"]).lower().replace("_", " ")
        if all(w in hay for w in words):
            # Matches in the name itself rank first
            score = sum(0 if w in s["name"].lower().replace("_", " ") else 1 for w in words)
            res.append((score, s["name"], s))
    res.sort(key=lambda t: (t[0], t[1]))
    return [s for _, _, s in res[:limit]]


def pick_state(states: list[dict], value: float, moving: bool = False) -> dict | None:
    for st in states:
        if check_condition(st.get("if"), value, moving):
            return st
    return None


class SimReader:
    """Reads simvars through Python-SimConnect's AircraftRequests, and L-variables ('L:name')
    directly with a SimConnect data definition – MSFS accepts L: names there, no WASM module needed."""

    def __init__(self, sm, aq, time_ms: int):
        self.sm, self.aq, self.time_ms = sm, aq, time_ms
        self._lvars: dict = {}

    def get(self, name: str):
        if not is_lvar(name):
            return self.aq.get(name)
        req = self._lvars.get(name)
        if req is None:
            from SimConnect.RequestList import Request
            req = Request((name.encode(), b"Number"), self.sm, _time=self.time_ms)
            self._lvars[name] = req
        return req.value


class SimRules(threading.Thread):
    """Polls SimConnect variables and updates LEDs according to the rules in config.json."""

    def __init__(self, cfg: dict, leds: LedWriter):
        super().__init__(daemon=True, name="simconnect")
        self.leds = leds
        self.connected = False
        self.aircraft: str | None = None
        self.connected_since: float | None = None
        self._last: dict[str, tuple[float, float]] = {}  # simvar -> (last value, time of last change)
        self.live: dict[str, float] = {}       # last value read per simvar, for the control panel
        self._watch: dict[str, float] = {}     # extra simvars the control panel wants to see -> expiry time
        self._watch_lock = threading.Lock()
        self.configure(cfg)

    def watch(self, names: list[str], seconds: float = 30.0) -> None:
        """Also read these simvars for a while (for the rule editor), even if no rule uses them."""
        until = time.monotonic() + seconds
        with self._watch_lock:
            for n in names:
                if n and simvar_known(n):
                    self._watch[n] = until

    def moving(self, simvar: str) -> bool:
        prev = self._last.get(simvar)
        return bool(prev and prev[1] and time.monotonic() - prev[1] < self.moving_hold)

    def configure(self, cfg: dict) -> None:
        self.rules = list(cfg.get("rules", []))
        self.poll = cfg.get("sim_poll_ms", 200) / 1000.0
        self.on_disconnect = cfg.get("on_disconnect_color", "off")
        # 'moving' is true as long as the value has changed within this window
        self.moving_hold = cfg.get("moving_hold_ms", 700) / 1000.0

    def _is_moving(self, simvar: str, value: float) -> bool:
        now = time.monotonic()
        prev = self._last.get(simvar)
        if prev is None:
            self._last[simvar] = (value, 0.0)  # the first reading does not count as movement
            return False
        last_value, changed_at = prev
        if abs(value - last_value) > 1e-4:
            self._last[simvar] = (value, now)
            return True
        return now - changed_at < self.moving_hold

    def run(self) -> None:
        try:
            from SimConnect import AircraftRequests, SimConnect  # pip install SimConnect
        except ImportError:
            log.error("The Python package 'SimConnect' is missing. Install it with: pip install SimConnect")
            return

        quiet = False
        while True:
            try:
                if not quiet:
                    log.info("Connecting to MSFS via SimConnect …")
                sm = SimConnect()
                aq = SimReader(sm, AircraftRequests(sm, _time=int(self.poll * 1000)), int(self.poll * 1000))
                log.info("Connected to MSFS.")
                self.connected, self.connected_since, quiet = True, time.time(), False
                self._loop(aq)
            except Exception as e:  # noqa: BLE001
                was_connected = self.connected
                self.connected, self.aircraft, self.connected_since = False, None, None
                self.live.clear()
                if was_connected or not quiet:
                    log.info("No SimConnect connection (%s). Retrying every 5 seconds.", e.__class__.__name__)
                quiet = True
                self.all_to_disconnect_color()
                time.sleep(5)

    def _loop(self, aq) -> None:
        last_title = None
        while True:
            title = self._title(aq)
            if title != last_title:
                log.info("Aircraft: %s", title or "(unknown)")
                last_title = title
                self.aircraft = title
            seen: set[str] = set()
            for rule in self.rules:
                if rule.get("enabled", True) is False:
                    continue
                ac = rule.get("aircraft_contains")
                if ac and (not title or ac.lower() not in title.lower()):
                    continue
                only_if = rule.get("only_if")
                if only_if:
                    # An extra condition on another simvar, e.g. only for aircraft without a tailwheel.
                    # If it is not met, the LED gets the colour in "otherwise" (default: the base colour).
                    cond_value = aq.get(only_if["simvar"])
                    if cond_value is not None:
                        self.live[only_if["simvar"]] = float(cond_value)
                    if cond_value is not None and not check_condition(only_if.get("if"), float(cond_value)):
                        other = str(rule.get("otherwise", "startup"))
                        is_rgb = bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", other)) and other.lower() not in self.leds.colors
                        try:
                            self.leds.set(rule["device"], rule["led"], color=None if is_rgb else other,
                                          rgb=other if is_rgb else None, source=rule.get("name", rule["simvar"]))
                        except ValueError as e:
                            log.error("Rule '%s': %s", rule.get("name"), e)
                        continue
                value = aq.get(rule["simvar"])
                if value is None:
                    continue
                value = float(value)
                self.live[rule["simvar"]] = value
                if rule["simvar"] in seen:
                    moving = self.moving(rule["simvar"])  # already read this round
                else:
                    moving = self._is_moving(rule["simvar"], value)
                    seen.add(rule["simvar"])
                st = pick_state(rule["states"], value, moving)
                if st is None:
                    continue
                try:
                    self.leds.set(rule["device"], rule["led"], color=st.get("color"), rgb=st.get("rgb"),
                                  blink=st.get("blink", False), source=rule.get("name", rule["simvar"]))
                except ValueError as e:
                    log.error("Rule '%s': %s", rule.get("name"), e)
            # Extra simvars the rule editor is watching
            now = time.monotonic()
            with self._watch_lock:
                for n in [n for n, until in self._watch.items() if until < now]:
                    del self._watch[n]
                extra = [n for n in self._watch if n not in seen]
            for n in extra:
                try:
                    v = aq.get(n)
                except Exception:  # noqa: BLE001
                    v = None
                if v is not None:
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        continue
                    self.live[n] = v
                    self._is_moving(n, v)
            time.sleep(self.poll)

    @staticmethod
    def _title(aq) -> str | None:
        try:
            t = aq.get("TITLE")
            return t.decode(errors="ignore") if isinstance(t, (bytes, bytearray)) else t
        except Exception:  # noqa: BLE001
            return None

    def all_to_disconnect_color(self) -> None:
        if not self.on_disconnect:
            return
        for rule in self.rules:
            try:
                self.leds.set(rule["device"], rule["led"], color=self.on_disconnect, source="disconnect")
            except ValueError:
                pass


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def startup_plan(cfg: dict) -> list[tuple[str, str, str]]:
    """One (device, led, colour) per physical LED from 'startup' in the config.

    'startup' can be one colour per device:        "cp1": "dimyellow"
    or a default colour plus exceptions per LED:   "cp2": {"default": "dimgreen", "leds": {"b5": "dimyellow"}}
    """
    plan = []
    for dev, spec in cfg.get("startup", {}).items():
        if dev not in cfg["devices"]:
            log.warning("startup: unknown device '%s'", dev)
            continue
        names = cfg["devices"][dev]["leds"]
        if isinstance(spec, str):
            default, overrides = spec, {}
        else:
            default, overrides = spec.get("default"), spec.get("leds", {})
        by_cmd: dict[str, tuple[str, str]] = {}
        for led, color in overrides.items():
            if led not in names:
                log.warning("startup: unknown LED '%s' on '%s'", led, dev)
                continue
            by_cmd[names[led]] = (led, color)
        if default:
            for led, cmd in names.items():
                by_cmd.setdefault(cmd, (led, default))
        plan += [(dev, led, color) for led, color in by_cmd.values()]
    return plan


def load_config(path: str) -> dict:
    """Reads and checks config.json. Raises ValueError with a readable message on errors."""
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        raise ValueError(f"Could not find {path}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"config.json is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}") from None
    return validate_config(cfg)


LED_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def validate_config(cfg: dict) -> dict:
    for key in ("vpc_led_control", "devices"):
        if key not in cfg:
            raise ValueError(f"config.json is missing '{key}'")
    for dev, spec in cfg["devices"].items():
        for key in ("vid", "pid", "leds"):
            if key not in spec:
                raise ValueError(f"Device '{dev}' is missing '{key}'")
    known_colors = set(DEFAULT_COLORS) | set(cfg.get("colors", {})) | {"startup"}
    for i, rule in enumerate(cfg.get("rules", []), 1):
        name = rule.get("name", f"rule {i}")
        for key in ("simvar", "device", "led", "states"):
            if key not in rule:
                raise ValueError(f"Rule '{name}' is missing '{key}'")
        if rule["device"] not in cfg["devices"]:
            raise ValueError(f"Rule '{name}' uses unknown device '{rule['device']}'")
        if rule["led"] not in cfg["devices"][rule["device"]]["leds"]:
            raise ValueError(f"Rule '{name}' uses unknown LED '{rule['led']}'")
        for st in rule["states"]:
            check_condition(st.get("if"), 0.0)  # raises ValueError on an invalid condition
            if "color" in st and st["color"].lower() not in known_colors:
                raise ValueError(f"Rule '{name}' uses unknown colour '{st['color']}'")
            if "rgb" in st and not re.fullmatch(r"#?[0-9a-fA-F]{6}", str(st["rgb"])):
                raise ValueError(f"Rule '{name}' has an invalid rgb '{st['rgb']}'")
        oi = rule.get("only_if")
        if oi is not None:
            if not isinstance(oi, dict) or "simvar" not in oi:
                raise ValueError(f"Rule '{name}': 'only_if' needs a simvar")
            check_condition(oi.get("if"), 0.0)
        other = rule.get("otherwise")
        if other is not None and str(other).lower() not in known_colors \
                and not re.fullmatch(r"#?[0-9a-fA-F]{6}", str(other)):
            raise ValueError(f"Rule '{name}' uses unknown colour '{other}' in 'otherwise'")
    return cfg


def validate_rules_strict(rules: list) -> None:
    """Extra checks for rules saved from the control panel: the simvars must exist, etc."""
    if not isinstance(rules, list):
        raise ValueError("'rules' must be a list")
    for i, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule {i} is not an object")
        name = str(rule.get("name", "")).strip()
        if not name:
            raise ValueError(f"Rule {i} has no name")
        if not rule.get("states"):
            raise ValueError(f"Rule '{name}' has no states")
        if not simvar_known(str(rule.get("simvar", ""))):
            raise ValueError(f"Rule '{name}': unknown simvar '{rule.get('simvar')}'")
        oi = rule.get("only_if")
        if oi and not simvar_known(str(oi.get("simvar", ""))):
            raise ValueError(f"Rule '{name}': unknown simvar '{oi.get('simvar')}' in the condition")
        if "enabled" in rule and not isinstance(rule["enabled"], bool):
            raise ValueError(f"Rule '{name}': 'enabled' must be true or false")


def name_usage(cfg: dict) -> dict[tuple[str, str], list[str]]:
    """Where each LED name is used: {(device, name): ["rule: Left flaps", "own base colour"]}."""
    used: dict[tuple[str, str], list[str]] = {}
    for rule in cfg.get("rules", []):
        used.setdefault((rule["device"], rule["led"]), []).append("rule: " + rule.get("name", rule["simvar"]))
    for dev, spec in cfg.get("startup", {}).items():
        if isinstance(spec, dict):
            for led in spec.get("leds", {}):
                used.setdefault((dev, led), []).append("own base colour")
    return used


def dump_config(cfg: dict) -> str:
    """Indented JSON, but small objects and lists on one line (like the hand-written file)."""
    def flat(obj) -> str:
        if isinstance(obj, dict):
            return "{ " + ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {flat(v)}" for k, v in obj.items()) + " }" \
                if obj else "{}"
        if isinstance(obj, list):
            return "[" + ", ".join(flat(v) for v in obj) + "]"
        return json.dumps(obj, ensure_ascii=False)

    def fmt(obj, indent: int) -> str:
        if not isinstance(obj, (dict, list)):
            return flat(obj)
        one_line = flat(obj)
        if indent >= 4 and len(one_line) + indent <= 110:
            return one_line
        pad, inner = " " * indent, " " * (indent + 2)
        if isinstance(obj, dict):
            if not obj:
                return "{}"
            items = [f'{inner}{json.dumps(k, ensure_ascii=False)}: {fmt(v, indent + 2)}' for k, v in obj.items()]
            return "{\n" + ",\n".join(items) + "\n" + pad + "}"
        if not obj:
            return "[]"
        return "[\n" + ",\n".join(inner + fmt(v, indent + 2) for v in obj) + "\n" + pad + "]"
    return fmt(cfg, 0) + "\n"


def layouts_path(config_path: str) -> Path:
    return Path(config_path).with_name("layouts.json")


def ensure_layouts_file(config_path: str) -> None:
    """Writes the bundled default layouts.json next to config.json if there is none, so it can be edited."""
    target = layouts_path(config_path)
    bundled = resource_dir() / "layouts.json"
    if target.exists() or not bundled.exists() or target.resolve() == bundled.resolve():
        return
    try:
        target.write_bytes(bundled.read_bytes())
        log.info("Wrote the default panel drawings to %s", target)
    except OSError as e:
        log.warning("Could not write %s: %s", target, e)


def load_layouts(config_path: str) -> dict:
    """The panel drawings: layouts.json next to config.json, or the bundled default if that is missing or invalid.
    Returns {"layouts": {pid: layout}, "path": str, "error": str | None}."""
    def read(p: Path) -> dict:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("the file must contain a JSON object")
        return data

    path, error = layouts_path(config_path), None
    try:
        data = read(path)
    except FileNotFoundError:
        path, data = resource_dir() / "layouts.json", None
    except (ValueError, json.JSONDecodeError) as e:
        error = f"layouts.json could not be read ({e}); using the built-in drawings."
        path, data = resource_dir() / "layouts.json", None
    if data is None:
        try:
            data = read(resource_dir() / "layouts.json")
        except (OSError, ValueError, json.JSONDecodeError):
            data = {}
    layouts = {k.upper(): v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
    for pid, lay in list(layouts.items()):  # resolve "same_as"
        src = str(lay.get("same_as", "")).upper()
        if src and src in layouts:
            layouts[pid] = {**layouts[src], **{k: v for k, v in lay.items() if k != "same_as"}}
    return {"layouts": layouts, "path": str(path), "error": error}


def file_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def save_config(path: str, cfg: dict) -> Path:
    """Backs up config.json and writes the new one safely (via a temporary file). Returns the backup."""
    src = Path(path)
    backups = src.with_name("backups")
    backups.mkdir(exist_ok=True)
    now = time.time()
    backup = backups / f"config-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{int(now * 1000) % 1000:03d}.json"
    if src.exists():
        backup.write_bytes(src.read_bytes())
    for old in sorted(backups.glob("config-*.json"))[:-20]:  # keep the latest 20
        try:
            old.unlink()
        except OSError:
            pass
    tmp = src.with_suffix(".json.tmp")
    tmp.write_text(dump_config(cfg), encoding="utf-8")
    os.replace(tmp, src)
    return backup


# --------------------------------------------------------------------------
# The bridge itself: config, LEDs, rules and shutdown
# --------------------------------------------------------------------------
class Bridge:
    def __init__(self, config_path: str, cfg: dict, dry_run: bool, no_sim: bool):
        self.config_path = config_path
        self.cfg = cfg
        self.dry_run = dry_run
        self.no_sim = no_sim
        self.started = time.time()
        self.loaded_at = time.time()
        self.leds = LedWriter(cfg, dry_run=dry_run)
        self.rules: SimRules | None = None
        self.plan: list[tuple[str, str, str]] = []
        self._reload_lock = threading.Lock()
        self._edit_lock = threading.Lock()
        self.stopping = threading.Event()
        self.server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        self._apply_base(self.cfg)
        if not self.no_sim and self.cfg.get("rules"):
            self.rules = SimRules(self.cfg, self.leds)
            self.rules.start()
        threading.Thread(target=self._run_startup, daemon=True, name="startup").start()

    def _apply_base(self, cfg: dict) -> None:
        self.plan = startup_plan(cfg)
        self.leds.base = {(d, cfg["devices"][d]["leds"][l]): c for d, l, c in self.plan}

    def _run_startup(self, skip: set[tuple[str, str]] | None = None) -> None:
        skip = skip or set()
        devices = self.cfg["devices"]
        for dev, led, color in self.plan:
            if (dev, devices[dev]["leds"][led]) in skip:
                continue
            if not self.leds.is_taken(dev, led):
                self.leds.set(dev, led, color=color, source="startup")

    def reload(self) -> str:
        """Reads config.json again and applies it without a restart. Returns a short message."""
        with self._reload_lock:
            cfg = load_config(self.config_path)  # ValueError on errors; the old config is kept
            self.cfg = cfg
            self.leds.configure(cfg)
            self._apply_base(cfg)
            rule_leds = {(r["device"], cfg["devices"][r["device"]]["leds"][r["led"]]) for r in cfg.get("rules", [])}
            if self.rules:
                self.rules.configure(cfg)
            elif not self.no_sim and cfg.get("rules"):
                self.rules = SimRules(cfg, self.leds)
                self.rules.start()
            connected = bool(self.rules and self.rules.connected)
            # With MSFS connected the rules set their own LEDs in the next round; otherwise they get the base colour.
            skip = rule_leds if connected else set()
            threading.Thread(target=self._run_startup, args=(skip,), daemon=True, name="reload").start()
            if self.rules and not connected:
                threading.Thread(target=self.rules.all_to_disconnect_color, daemon=True).start()
            self.loaded_at = time.time()
            n_leds = sum(len(set(v["leds"].values())) for v in cfg["devices"].values())
            msg = f"Config loaded: {len(cfg['devices'])} devices, {n_leds} LEDs, {len(cfg.get('rules', []))} rules."
            log.info(msg)
            return msg

    def _edit_config(self, change) -> str:
        """Reads config.json from disk, makes a change, checks it, backs up, saves and reloads."""
        with self._edit_lock:
            cfg = load_config(self.config_path)
            msg = change(cfg)
            validate_config(cfg)
            backup = save_config(self.config_path, cfg)
            log.info("Saved config.json (backup of the previous one: %s)", backup.name)
            self.reload()
            return msg

    def set_led_names(self, device: str, cmd: str, names: list[str]) -> str:
        """Gives one physical LED (command) new names. A name used by rules or base colours can only be
        swapped for exactly one new name; the rules are then updated too."""
        names = [n.strip().lower() for n in names if n.strip()]
        if not re.fullmatch(r"\d{2}", cmd) or not 1 <= int(cmd) <= 54:
            raise ValueError(f"Invalid command number '{cmd}' (use 01–54)")
        if not names:
            raise ValueError("The LED needs at least one name")
        for n in names:
            if not LED_NAME_RE.match(n):
                raise ValueError(f"Invalid name '{n}': use lower-case letters, digits and _, starting with a letter")
        if len(set(names)) != len(names):
            raise ValueError("The same name appears more than once")

        def change(cfg: dict) -> str:
            if device not in cfg["devices"]:
                raise ValueError(f"Unknown device '{device}'")
            leds: dict = cfg["devices"][device]["leds"]
            for n in names:
                if n in leds and leds[n] != cmd:
                    raise ValueError(f"The name '{n}' is already used by LED {leds[n]} on this device")
            old = [n for n, c in leds.items() if c == cmd]
            removed = [n for n in old if n not in names]
            added = [n for n in names if n not in old]
            usage = name_usage(cfg)
            in_use = [n for n in removed if (device, n) in usage]
            rename_to = None
            if in_use:
                if len(added) != 1:
                    uses = "; ".join(f"'{n}' ({', '.join(usage[(device, n)])})" for n in in_use)
                    raise ValueError(f"Cannot remove names that are in use: {uses}. "
                                     "Keep the name, or replace it with exactly one new name.")
                rename_to = added[0]
                for rule in cfg.get("rules", []):
                    if rule["device"] == device and rule["led"] in in_use:
                        rule["led"] = rename_to
                spec = cfg.get("startup", {}).get(device)
                if isinstance(spec, dict):
                    spec["leds"] = {(rename_to if k in in_use else k): v for k, v in spec.get("leds", {}).items()}
            # Keep the order in the file: the new names go where the LED was before.
            new_leds, inserted = {}, False
            for n, c in leds.items():
                if c == cmd:
                    if not inserted:
                        new_leds.update({x: cmd for x in names})
                        inserted = True
                    continue
                new_leds[n] = c
            if not inserted:
                new_leds.update({x: cmd for x in names})
            cfg["devices"][device]["leds"] = new_leds
            what = f"LED {cmd} on {cfg['devices'][device].get('name', device)} is now called {', '.join(names)}"
            if rename_to:
                what += f" (rules updated: {', '.join(in_use)} → {rename_to})"
            return what + "."

        return self._edit_config(change)

    def config_mtime(self) -> float:
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0.0

    def set_rules(self, rules: list, based_on_mtime: float | None = None) -> str:
        validate_rules_strict(rules)

        def change(cfg: dict) -> str:
            if based_on_mtime is not None and abs(self.config_mtime() - float(based_on_mtime)) > 1e-3:
                raise ValueError("config.json has changed since the rules were loaded (perhaps in another window "
                                 "or by hand). Reload the page and make the change again.")
            cfg["rules"] = rules
            return f"Rules saved ({len(rules)} rules)."

        return self._edit_config(change)

    def config_for_ui(self) -> dict:
        """The rules and colours as the rule editor needs them."""
        cfg = self.cfg
        colors = {**DEFAULT_COLORS, **{k: tuple(v) for k, v in cfg.get("colors", {}).items()}}
        return {
            "rules": cfg.get("rules", []),
            "colors": {k: "%02X%02X%02X" % tuple(quantize(c) for c in v) for k, v in colors.items()},
            "custom_colors": list(cfg.get("colors", {})),
            "mtime": self.config_mtime(),
        }

    def live(self, watch: list[str]) -> dict:
        r = self.rules
        if r is None:
            return {"connected": False, "values": {}, "moving": {}}
        if watch:
            r.watch(watch)
        values = dict(r.live)
        return {"connected": r.connected, "values": values, "moving": {k: r.moving(k) for k in values}}

    def set_device_name(self, device: str, name: str) -> str:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValueError("The name must be 1–60 characters")

        def change(cfg: dict) -> str:
            if device not in cfg["devices"]:
                raise ValueError(f"Unknown device '{device}'")
            cfg["devices"][device]["name"] = name
            return f"Device {device} is now called “{name}”."

        return self._edit_config(change)

    def request_stop(self) -> None:
        """Can be called from any thread (tray icon, web page)."""
        if not self.stopping.is_set():
            self.stopping.set()
            if self.server:
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    def restore_leds(self) -> None:
        """Sets the LEDs to their base colours (or off) before the program exits."""
        on_exit = self.cfg.get("on_exit", "startup")
        log.info("Shutting down (LEDs -> %s).", on_exit)
        self.leds.frozen = True
        self.leds.clear_wanted()
        if on_exit == "startup":
            covered = {d for d, _, _ in self.plan}
            for dev, led, color in self.plan:
                self.leds.set(dev, led, color=color, source="shutdown")
            for dev in self.cfg["devices"]:
                if dev not in covered:
                    self.leds.set_all(dev, "off", source="shutdown")
        else:
            for dev in self.cfg["devices"]:
                self.leds.set_all(dev, "off", source="shutdown")
        log.info("LEDs set. Done.")

    def status(self) -> dict:
        cfg = self.cfg
        phys = self.leds.phys_state()
        usage = name_usage(cfg)
        devices = []
        for dev_id, dev in cfg["devices"].items():
            by_cmd: dict[str, list[str]] = {}
            for name, cmd in dev["leds"].items():
                by_cmd.setdefault(cmd, []).append(name)
            leds = []
            for cmd in sorted(by_cmd, key=lambda c: int(c) if c.isdigit() else c):
                names = by_cmd[cmd]
                st = phys.get((dev_id, cmd))
                leds.append({
                    "cmd": cmd,
                    "names": names,
                    "rgb": st["rgb"] if st else None,
                    "blink": bool(st and st["blink"]),
                    "color": st.get("color") if st else None,
                    "source": st.get("source") if st else None,
                    "used_by": {n: usage[(dev_id, n)] for n in names if (dev_id, n) in usage},
                })
            devices.append({"id": dev_id, "name": dev.get("name", dev_id), "vid": dev["vid"], "pid": dev["pid"],
                            "leds": leds})
        rules = self.rules
        return {
            "version": VERSION,
            "started": self.started,
            "uptime_s": round(time.time() - self.started),
            "config_path": self.config_path,
            "config_loaded_at": self.loaded_at,
            "dry_run": self.dry_run,
            "sim": {
                "enabled": rules is not None,
                "connected": bool(rules and rules.connected),
                "aircraft": rules.aircraft if rules else None,
                "connected_since": rules.connected_since if rules else None,
            },
            "n_rules": len(cfg.get("rules", [])),
            "autostart": autostart_status(),
            "layouts_mtime": file_mtime(layouts_path(self.config_path)),
            "devices": devices,
        }


# --------------------------------------------------------------------------
# HTTP and UDP API
# --------------------------------------------------------------------------
def make_http_handler(bridge: Bridge, ring: RingHandler):
    ui_file = resource_dir() / "ui.html"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence the default logging
            log.debug("HTTP %s", fmt % args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _reply(self, code: int, payload: dict) -> None:
            self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _params(self) -> dict:
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            if self.command == "POST":
                n = int(self.headers.get("Content-Length", 0) or 0)
                if n:
                    body = self.rfile.read(n)
                    try:
                        params.update(json.loads(body))
                    except json.JSONDecodeError:
                        pass
            return params

        def _from_ui(self) -> bool:
            # Actions that change the bridge require this header. An ordinary web page cannot send
            # custom headers to another address without a preflight check (CORS), which we never answer.
            return self.headers.get("X-Bridge-UI") == "1"

        def _handle(self):
            path = urlparse(self.path).path.rstrip("/")
            leds = bridge.leds
            try:
                p = self._params()
                if self.command == "POST" and path.startswith("/api/"):
                    shown = json.dumps(p, ensure_ascii=False)
                    shown = shown if len(shown) <= 160 else shown[:157] + "…"
                    log.info("[control panel] %s %s%s", path, shown,
                             "" if self._from_ui() else " (no X-Bridge-UI – rejected)")
                if path in ("", "/ui", "/index.html") and self.command == "GET":
                    try:
                        self._send(200, ui_file.read_bytes(), "text/html; charset=utf-8")
                    except OSError:
                        self._reply(500, {"ok": False, "error": f"Could not find {ui_file}"})
                elif path == "/api/status":
                    self._reply(200, bridge.status())
                elif path == "/api/simvars":
                    self._reply(200, {"results": search_simvars(p.get("q", ""), int(p.get("limit", 40) or 40)),
                                      "total": len(simvar_catalog())})
                elif path == "/api/live":
                    watch = [w for w in str(p.get("watch", "")).split(",") if w]
                    self._reply(200, bridge.live(watch))
                elif path == "/api/layouts":
                    self._reply(200, load_layouts(bridge.config_path))
                elif path == "/api/config" and self.command == "GET":
                    self._reply(200, bridge.config_for_ui())
                elif path == "/api/rules" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        self._reply(200, {"ok": True, "message": bridge.set_rules(p["rules"], p.get("mtime"))})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not write config.json: {e}"})
                elif path == "/api/log":
                    self._reply(200, {"lines": ring.since(int(p.get("after", 0) or 0))})
                elif path == "/api/reload" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        self._reply(200, {"ok": True, "message": bridge.reload()})
                    except ValueError as e:
                        log.error("Could not read the config: %s", e)
                        self._reply(400, {"ok": False, "error": str(e)})
                elif path in ("/api/identify", "/api/led-names", "/api/device-name") and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    if path == "/api/identify":
                        leds.identify(p["device"], str(p["cmd"]).zfill(2))
                        self._reply(200, {"ok": True})
                    else:
                        try:
                            if path == "/api/led-names":
                                names = p["names"] if isinstance(p["names"], list) else re.split(r"[,\s]+", p["names"])
                                msg = bridge.set_led_names(p["device"], str(p["cmd"]).zfill(2), names)
                            else:
                                msg = bridge.set_device_name(p["device"], p["name"])
                            self._reply(200, {"ok": True, "message": msg})
                        except OSError as e:
                            self._reply(500, {"ok": False, "error": f"Could not write config.json: {e}"})
                elif path == "/api/autostart" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        self._reply(200, {"ok": True, "message": set_autostart(bool(p["enabled"]))})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not change the setting: {e}"})
                elif path == "/api/quit" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    self._reply(200, {"ok": True})
                    bridge.request_stop()
                elif path == "/led":
                    leds.set(p["device"], p["led"], color=p.get("color"), rgb=p.get("rgb"),
                             blink=str(p.get("blink", "0")).lower() in ("1", "true", "yes", "on"), source="http")
                    self._reply(200, {"ok": True})
                elif path == "/all":
                    leds.set_all(p["device"], p.get("color", "off"), source="http")
                    self._reply(200, {"ok": True})
                elif path == "/state":
                    self._reply(200, {"devices": {d: list(v["leds"]) for d, v in leds.devices.items()},
                                      "colors": sorted(leds.colors), "state": leds.state()})
                else:
                    self._reply(404, {"ok": False, "error": "Unknown endpoint"})
            except KeyError as e:
                log.warning("HTTP %s: missing parameter %s", path, e)
                self._reply(400, {"ok": False, "error": f"Missing parameter {e}"})
            except ValueError as e:
                log.warning("HTTP %s: %s", path, e)
                self._reply(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                log.exception("HTTP %s failed", path)
                self._reply(500, {"ok": False, "error": f"Internal error: {e}"})

        do_GET = _handle
        do_POST = _handle

    return Handler


def udp_server(bridge: Bridge, host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
    except OSError as e:
        log.error("UDP: could not listen on %s:%d (%s)", host, port, e)
        return
    log.info("UDP listening on %s:%d", host, port)
    while True:
        data, _ = sock.recvfrom(1024)
        leds = bridge.leds
        for line in data.decode(errors="ignore").splitlines():
            parts = line.split()
            if not parts:
                continue
            try:
                if len(parts) >= 3:
                    dev, led, col = parts[0], parts[1], parts[2]
                    blink = len(parts) > 3 and parts[3].lower() == "blink"
                    if re.fullmatch(r"#?[0-9a-fA-F]{6}", col) and col.lower() not in leds.colors:
                        leds.set(dev, led, rgb=col, blink=blink, source="udp")
                    else:
                        leds.set(dev, led, color=col, blink=blink, source="udp")
                else:
                    log.warning("UDP: expected 'device led colour [blink]', got '%s'", line)
            except ValueError as e:
                log.warning("UDP: %s", e)


# --------------------------------------------------------------------------
# Start with Windows (per user, no admin rights needed)
# --------------------------------------------------------------------------
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "VirpilLedBridge"


def autostart_command() -> str | None:
    """The command Windows should run at sign-in, or None when not running as the exe."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    return f'"{sys.executable}"'


def autostart_status() -> dict:
    cmd = autostart_command()
    if cmd is None:
        return {"available": False, "enabled": False, "reason": "Only available when running VirpilLedBridge.exe"}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, _RUN_NAME)
    except OSError:
        return {"available": True, "enabled": False}
    if os.path.normcase(value.strip()) != os.path.normcase(cmd):
        # An entry exists, but for an exe in another folder (e.g. it has been moved).
        return {"available": True, "enabled": False, "stale": value}
    return {"available": True, "enabled": True}


def set_autostart(enabled: bool) -> str:
    cmd = autostart_command()
    if cmd is None:
        raise ValueError("Start with Windows is only available when running VirpilLedBridge.exe")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
        if enabled:
            winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, _RUN_NAME)
            except FileNotFoundError:
                pass
    msg = "The bridge will now start with Windows." if enabled else "The bridge will no longer start with Windows."
    log.info("%s", msg)
    return msg


# --------------------------------------------------------------------------
# Tray icon
# --------------------------------------------------------------------------
def make_icon_image():
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=12, fill=(28, 30, 34, 255), outline=(90, 95, 105, 255),
                        width=2)
    for i, col in enumerate([(60, 200, 90), (60, 200, 90), (230, 70, 60)]):
        cx = 16 + i * 16
        d.ellipse((cx - 6, 34, cx + 6, 46), fill=col)
    d.rectangle((14, 16, 50, 24), fill=(220, 190, 60))
    return img


def start_tray(bridge: Bridge, url: str):
    try:
        import pystray
    except ImportError:
        log.info("pystray is not installed – running without a tray icon (quit with Ctrl+C).")
        return None

    def open_ui(icon=None, item=None):
        webbrowser.open(url)

    def reload_cfg(icon, item):
        try:
            icon.notify(bridge.reload(), "LED Bridge")
        except ValueError as e:
            log.error("Could not read the config: %s", e)
            icon.notify(str(e), "LED Bridge – config error")

    def quit_app(icon, item):
        bridge.request_stop()

    def toggle_autostart(icon, item):
        try:
            icon.notify(set_autostart(not autostart_status()["enabled"]), "LED Bridge")
        except (ValueError, OSError) as e:
            log.error("Start with Windows: %s", e)
            icon.notify(str(e), "LED Bridge")

    menu = pystray.Menu(
        pystray.MenuItem("Open control panel", open_ui, default=True),
        pystray.MenuItem("Reload config", reload_cfg),
        pystray.MenuItem("Start with Windows", toggle_autostart,
                         checked=lambda item: autostart_status()["enabled"],
                         enabled=lambda item: autostart_status()["available"]),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("virpil-led-bridge", make_icon_image(), "Virpil LED Bridge", menu)
    threading.Thread(target=icon.run, daemon=True, name="tray").start()
    return icon


# --------------------------------------------------------------------------
def main() -> int:
    # As an exe without a console there is no stdout/stderr; some libraries still write there.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    has_console = sys.stderr is not None and getattr(sys.stderr, "name", "") != os.devnull

    ap = argparse.ArgumentParser(description="Virpil LED Bridge for MSFS")
    ap.add_argument("--config", default=str(app_dir() / "config.json"))
    ap.add_argument("--dry-run", action="store_true", help="Do not call VPC_LED_Control, log only")
    ap.add_argument("--no-sim", action="store_true", help="Do not connect to MSFS, API only")
    ap.add_argument("--no-tray", action="store_true", help="Do not show a tray icon")
    ap.add_argument("--open", action="store_true", help="Open the control panel in the browser on start")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    ring = RingHandler()
    ring.setFormatter(fmt)
    root.addHandler(ring)
    try:
        fh = logging.handlers.RotatingFileHandler(Path(args.config).with_name("bridge.log"), maxBytes=1_000_000,
                                                  backupCount=1, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        root.addHandler(fh)
    except OSError:
        pass
    if has_console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    try:
        cfg = load_config(args.config)
    except ValueError as e:
        log.error("%s", e)
        return 1

    api = cfg.get("api", {})
    host = api.get("host", "127.0.0.1")
    port = api.get("http_port", 8765)
    url = f"http://{host}:{port}/"

    ensure_layouts_file(args.config)
    bridge = Bridge(args.config, cfg, dry_run=args.dry_run, no_sim=args.no_sim)
    try:
        server = ThreadingHTTPServer((host, port), make_http_handler(bridge, ring))
    except OSError:
        # The port is in use – usually because the bridge is already running. Show that one instead.
        log.error("Port %d is in use. Is the bridge already running? Opening the control panel.", port)
        webbrowser.open(url)
        return 2
    server.daemon_threads = True
    bridge.server = server

    log.info("Virpil LED Bridge %s. Config: %s", VERSION, args.config)
    if api.get("udp_port"):
        threading.Thread(target=udp_server, args=(bridge, host, api["udp_port"]), daemon=True, name="udp").start()
    bridge.start()
    log.info("Control panel and API at %s", url)

    icon = None if args.no_tray else start_tray(bridge, url)
    if icon is not None:
        time.sleep(0.5)
        try:
            icon.notify("Running. Double-click the icon for the control panel.", "Virpil LED Bridge")
        except Exception:  # noqa: BLE001
            pass
    if args.open:
        webbrowser.open(url)

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    bridge.stopping.set()
    server.server_close()
    bridge.restore_leds()
    if icon is not None:
        icon.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
