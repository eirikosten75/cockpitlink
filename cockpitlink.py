#!/usr/bin/env python3
"""
CockpitLink for MSFS 2020/2024
==============================

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
    python cockpitlink.py                 # normal operation (with a tray icon)
    python cockpitlink.py --dry-run       # no hardware, log only
    python cockpitlink.py --no-sim        # API only, no SimConnect
    python cockpitlink.py --no-tray       # no tray icon (quit with Ctrl+C)
    python cockpitlink.py --open          # open the control panel in the browser on start
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import logging.handlers
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "3.1"

log = logging.getLogger("cockpitlink")

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
    """The folder of the exe (or of the script when run from Python)."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


# Your files – config.json, layouts.json, backups, photos and bridge.log – are kept in one data folder:
# %LOCALAPPDATA%\CockpitLink, the same for the zip and the Microsoft Store version. With a file called
# portable.txt next to the exe, they are kept in the exe's folder instead (as before 3.1).
DATA_FOLDER_NAME = "CockpitLink"
PORTABLE_MARKER = "portable.txt"
SETUP_ITEMS = ("config.json", "layouts.json", "backups", "photos")
DATA_INFO: dict = {"portable": False, "copied_from": None}


def is_portable() -> bool:
    return (app_dir() / PORTABLE_MARKER).exists()


def data_dir() -> Path:
    if is_portable():
        return app_dir()
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / DATA_FOLDER_NAME if base else app_dir()


def copy_old_setup(target: Path) -> Path | None:
    """Before 3.1 the files were kept next to the exe. If the data folder has no config.json yet but the exe's
    folder has one, the setup is copied over (the originals are left where they are, with a note). Returns the
    folder it was copied from, or None."""
    src = app_dir()
    if src.resolve() == target.resolve() or (target / "config.json").exists() or not (src / "config.json").exists():
        return None
    target.mkdir(parents=True, exist_ok=True)
    for name in SETUP_ITEMS:
        item = src / name
        if item.is_dir():
            shutil.copytree(item, target / name, dirs_exist_ok=True)
        elif item.is_file():
            shutil.copy2(item, target / name)
    try:
        (src / "SETUP-MOVED.txt").write_text(
            f"From CockpitLink 3.1 your setup (config.json, layouts.json, backups, photos) is kept in\n{target}\n\n"
            "It was copied there from this folder. The files here are not used any more and can be deleted.\n"
            "To keep everything in this folder instead, put a file called portable.txt next to CockpitLink.exe.\n",
            encoding="utf-8")
    except OSError:
        pass
    return src


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
        # One VPC_LED_Control command at a time per USB device. Different devices are written in parallel
        # (tested: about 2.3x faster with three panels), while each LED still gets its writes in order.
        self._io_locks: dict[tuple[str, str], threading.Lock] = {}
        self._io_locks_guard = threading.Lock()
        # what is actually on the LED, per physical LED (device, command number), so aliases share the cache
        self._written: dict[tuple[str, str], tuple[int, int, int]] = {}
        self._wanted: dict[tuple[str, str], dict] = {}  # wanted state per name (colour + blink)
        self._phys: dict[tuple[str, str], dict] = {}    # wanted state per physical LED, for the control panel
        self._override: set[tuple[str, str]] = set()   # physical LEDs blinking so they can be identified
        self._blink_phase = True
        # Results of the calls to VPC_LED_Control, for the status in the control panel
        self.write_stats = {"ok": 0, "failed": 0, "last_ok": None, "last_fail": None, "last_error": ""}
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
                    with self._io_lock_for(dev):
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
                    with self._io_lock_for(dev):
                        self._write(exe, template, dev, cmd, restore)
                    with self._lock:
                        self._written[phys] = restore

        threading.Thread(target=run, daemon=True, name="identify").start()

    # ---- internal ----
    def _io_lock_for(self, dev: dict) -> threading.Lock:
        """The write lock for one USB device (by VID/PID, so two config entries for the same hardware share it)."""
        hw = (str(dev["vid"]).upper(), str(dev["pid"]).upper())
        with self._io_locks_guard:
            return self._io_locks.setdefault(hw, threading.Lock())

    def _apply(self, key: tuple[str, str]) -> None:
        # The device's write lock makes the writes reach the LEDs in the same order as the cache is updated.
        # Without it, two threads (e.g. startup and a rule) can start VPC_LED_Control at the same time,
        # and the older command can end up last on the LED while the cache shows the newer one.
        dev = self.devices.get(key[0])
        if dev is None:
            return
        with self._io_lock_for(dev):
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
        name = next((k for k, v in self.devices.items() if v is dev), str(dev.get("pid")))
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
                detail = f"exit code {res.returncode} {res.stdout.strip()} {res.stderr.strip()}".strip()
                log.warning("VPC_LED_Control failed (%s): %s %s", res.returncode, res.stdout.strip(), res.stderr.strip())
                self._note_write(False, name, detail)
            else:
                self._note_write(True, name)
        except Exception as e:  # noqa: BLE001
            log.error("Could not run VPC_LED_Control: %s", e)
            self._note_write(False, name, str(e))

    def _note_write(self, ok: bool, device: str, error: str = "") -> None:
        with self._lock:
            s = self.write_stats
            now = time.time()
            if ok:
                s["ok"] += 1
                s["last_ok"] = now
            else:
                s["failed"] += 1
                s["last_fail"] = now
                s["last_error"] = f"{device}: {error}"

    def health(self) -> dict:
        """How LED control is doing, for the control panel: is VPC_LED_Control.exe there, and did the last write
        work? state: dry_run, missing (no exe), error (the last write failed), ok, or ready (no writes yet)."""
        with self._lock:
            s = dict(self.write_stats)
            exe = self.exe
        found = Path(exe).exists()
        if self.dry_run:
            state = "dry_run"
        elif not found:
            state = "missing"
        elif s["last_fail"] and (not s["last_ok"] or s["last_fail"] > s["last_ok"]):
            state = "error"
        elif s["last_ok"]:
            state = "ok"
        else:
            state = "ready"
        return {"state": state, "exe": exe, "exe_found": found, **s}

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
        self.cfg = cfg
        self.poll = cfg.get("sim_poll_ms", 200) / 1000.0
        self.on_disconnect = cfg.get("on_disconnect_color", "off")
        # 'moving' is true as long as the value has changed within this window
        self.moving_hold = cfg.get("moving_hold_ms", 700) / 1000.0
        self.select = cfg.get("profile_select", "auto")
        self.profile: str | None = None
        self.rules = []
        self.choose_profile(quiet=True)

    def choose_profile(self, quiet: bool = False) -> None:
        """Picks the profile for the current aircraft (or the one chosen by hand) and switches the rules.
        LEDs that only the old rules drove go back to their base colour."""
        prof, why = pick_profile(self.cfg, self.aircraft, self.select)
        name = prof.get("name") if prof else None
        new_rules = effective_rules(self.cfg, prof)
        old_rules, old_name = self.rules, self.profile
        self.rules, self.profile, self.profile_why = new_rules, name, why
        if quiet or name == old_name:
            return
        log.info("Profile: %s (%s)", name or "all aircraft", why)
        devices = self.cfg["devices"]

        def phys(r: dict):
            return r.get("device"), devices.get(r.get("device"), {}).get("leds", {}).get(r.get("led"))

        now_driven = {phys(r) for r in new_rules}
        done = set()
        for r in old_rules:
            p = phys(r)
            if p[1] and p not in now_driven and p not in done:
                done.add(p)
                try:
                    self.leds.set(r["device"], r["led"], color="startup", source="profile change")
                except ValueError:
                    pass

    def set_select(self, select: str) -> None:
        self.select = select
        self.choose_profile()

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
                if was_connected:
                    self.choose_profile()
                if was_connected or not quiet:
                    log.info("No SimConnect connection (%s). Retrying every 5 seconds.", e.__class__.__name__)
                quiet = True
                self.all_to_disconnect_color()
                time.sleep(5)

    def _loop(self, aq) -> None:
        last_title = None
        no_title_since = None
        while True:
            title = self._title(aq)
            # SimConnect now and then gives no title for a single reading. Keep the aircraft (and its profile)
            # until the title has been missing for 5 s, so the profile does not flip back and forth.
            if title is None and last_title is not None:
                no_title_since = no_title_since or time.monotonic()
                if time.monotonic() - no_title_since < 5.0:
                    title = last_title
            else:
                no_title_since = None
            if title != last_title:
                log.info("Aircraft: %s", title or "(unknown)")
                last_title = title
                self.aircraft = title
                self.choose_profile()
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
    if isinstance(cfg, dict) and migrate_led_numbers(cfg):
        validate_config(cfg)
        backup = save_config(path, cfg)
        log.warning("config.json converted to the LED numbers of Virpil firmware %s (board LEDs 05-24 are now "
                    "01-20; your LED names and rules are unchanged). The old file is in backups\\%s.",
                    REQUIRED_VPC_VERSION, backup.name)
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
    if not isinstance(cfg.get("rules", []), list):
        raise ValueError("'rules' must be a list")
    for i, rule in enumerate(cfg.get("rules", []), 1):
        _validate_rule(cfg, rule, rule.get("name", f"rule {i}") if isinstance(rule, dict) else f"rule {i}",
                       known_colors)
    profiles = cfg.get("profiles", [])
    if not isinstance(profiles, list):
        raise ValueError("'profiles' must be a list")
    seen: set[str] = set()
    for i, prof in enumerate(profiles, 1):
        if not isinstance(prof, dict):
            raise ValueError(f"Profile {i} is not an object")
        pname = str(prof.get("name", "")).strip()
        if not pname or len(pname) > 60:
            raise ValueError(f"Profile {i} needs a name (1–60 characters)")
        if pname.lower() in seen or pname.lower() in RESERVED_PROFILE_NAMES:
            raise ValueError(f"The profile name '{pname}' is used more than once or is reserved")
        seen.add(pname.lower())
        match = prof.get("match", [])
        if not isinstance(match, list) or not all(isinstance(m, str) for m in match):
            raise ValueError(f"Profile '{pname}': 'match' must be a list of texts")
        if prof.get("mode", "extend") not in PROFILE_MODES:
            raise ValueError(f"Profile '{pname}': 'mode' must be one of {', '.join(PROFILE_MODES)}")
        if not isinstance(prof.get("rules", []), list):
            raise ValueError(f"Profile '{pname}': 'rules' must be a list")
        for j, rule in enumerate(prof.get("rules", []), 1):
            rname = rule.get("name", f"rule {j}") if isinstance(rule, dict) else f"rule {j}"
            _validate_rule(cfg, rule, f"{rname}' in profile '{pname}", known_colors)
    sel = cfg.get("profile_select", "auto")
    if not isinstance(sel, str):
        raise ValueError("'profile_select' must be a text: auto, default or a profile name")
    return cfg


def _validate_rule(cfg: dict, rule, name: str, known_colors: set[str]) -> None:
    if not isinstance(rule, dict):
        raise ValueError(f"Rule '{name}' is not an object")
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


# --------------------------------------------------------------------------
# Aircraft profiles: extra rules for some aircraft, chosen automatically from the aircraft name (needs MSFS
# running and connected through SimConnect) or by hand. Off unless "profiles_enabled" is true.
# --------------------------------------------------------------------------
PROFILE_MODES = ("extend", "replace")
RESERVED_PROFILE_NAMES = {"auto", "default", "all aircraft"}


def profiles_on(cfg: dict) -> bool:
    return bool(cfg.get("profiles_enabled")) and bool(cfg.get("profiles"))


def pick_profile(cfg: dict, title: str | None, select: str | None = None) -> tuple[dict | None, str]:
    """The profile to use: (profile, or None for the rules for all aircraft; why, in words).
    select is 'auto' (by aircraft name), 'default' (rules for all aircraft) or a profile name."""
    if not cfg.get("profiles_enabled"):
        return None, "profiles are off"
    if not cfg.get("profiles"):
        return None, "no profiles yet"
    profs = [p for p in cfg.get("profiles", []) if p.get("enabled", True) is not False]
    select = select if select is not None else cfg.get("profile_select", "auto")
    if select == "default":
        return None, "chosen by hand"
    if select and select != "auto":
        chosen = next((p for p in profs if p.get("name") == select), None)
        if chosen:
            return chosen, "chosen by hand"
    if not title:
        return None, "automatic – no aircraft (MSFS not connected, or no aircraft loaded)"
    t = title.lower()
    for p in profs:
        for m in p.get("match", []):
            if m.strip() and m.strip().lower() in t:
                return p, f"automatic – the aircraft name contains “{m.strip()}”"
    return None, "automatic – no profile matches this aircraft"


def effective_rules(cfg: dict, profile: dict | None) -> list[dict]:
    """The rules in use with this profile. 'extend' (the default): the profile's rules take over the LEDs they
    drive, and every other LED keeps the rules for all aircraft. 'replace': only the profile's rules."""
    base = [r for r in cfg.get("rules", []) if isinstance(r, dict)]
    if profile is None:
        return base
    own = [r for r in profile.get("rules", []) if isinstance(r, dict)]
    if profile.get("mode", "extend") == "replace":
        return own
    devices = cfg["devices"]

    def phys(r: dict):
        return r.get("device"), devices.get(r.get("device"), {}).get("leds", {}).get(r.get("led"))

    taken = {phys(r) for r in own if r.get("enabled", True) is not False}
    return [r for r in base if phys(r) not in taken] + own


def has_any_rules(cfg: dict) -> bool:
    return bool(cfg.get("rules")) or any(p.get("rules") for p in cfg.get("profiles", []) if isinstance(p, dict))


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
    for prof in cfg.get("profiles", []):
        for rule in prof.get("rules", []):
            used.setdefault((rule["device"], rule["led"]), []).append(
                f"rule: {rule.get('name', rule['simvar'])} (profile {prof.get('name')})")
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


LAYOUTS_INFO = ("Your own panel drawings, and your changes to the drawings in the bridge's built-in library "
                 "(the layouts folder in the repository). Keyed by USB PID. Made by Edit drawing in the control "
                 "panel; you can also edit it by hand – changes show up within a second. Delete a panel's entry "
                 "(or press 'Go back to the library drawing' in the editor) to use the library drawing again.")


def ensure_layouts_file(config_path: str) -> None:
    """Writes an (empty) layouts.json for your own drawings next to config.json if there is none."""
    target = layouts_path(config_path)
    if target.exists():
        return
    try:
        target.write_text(dump_layouts({"_info": LAYOUTS_INFO}), encoding="utf-8")
        log.info("Wrote %s for your own panel drawings", target)
    except OSError as e:
        log.warning("Could not write %s: %s", target, e)


# The library: one file per panel in the layouts folder (bundled inside the exe) ----------------
LIBRARY_STATUSES = ("verified", "unverified")


def library_dir() -> Path:
    return resource_dir() / "layouts"


def library_key(entry_id: str) -> str:
    """The drawing key for a library panel whose PID is not known yet (found by product name only)."""
    return (re.sub(r"[^A-Z0-9]+", "_", entry_id.upper()).strip("_") or "PANEL")[:16]


def load_library() -> dict:
    """The panel drawings in the library: {"by_pid": {drawing key: entry}, "by_name": {product name: entry},
    "errors": []}. The drawing key is the USB PID, or for a panel whose PID is not known yet (only its product
    name), a key made from the file name, e.g. CONTROL_PANEL_3.
    entry = {"id", "key", "title", "status", "checked", "pids": {PID: title}, "product_names", "commands",
    "drawing"}."""
    by_pid, by_name, errors = {}, {}, []
    folder = library_dir()
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            pids = {str(k).upper(): str(v) for k, v in (d.get("pids") or {}).items()}
            if not all(re.fullmatch(r"[0-9A-F]{4}", p) for p in pids):
                raise ValueError("'pids' must map USB PIDs (4 hex digits) to titles")
            own_key = str(d.get("key", "")).upper()
            if own_key and not LAYOUT_KEY_RE.match(own_key):
                raise ValueError("'key' must be 1-16 capital letters, digits or _")
            status = d.get("status", "unverified")
            if status not in LIBRARY_STATUSES:
                raise ValueError(f"'status' must be one of {', '.join(LIBRARY_STATUSES)}")
            drawing = clean_layout({k: d[k] for k in ("title", "aspect", "btn", "wide", "deco", "leds") if k in d})
            commands = {str(k): f"{int(c):02d}" for k, c in (d.get("commands") or {}).items()}
            for name, c in commands.items():
                if not LED_NAME_RE.match(name) or not 1 <= int(c) <= 54:
                    raise ValueError(f"invalid command entry '{name}': '{c}'")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            errors.append(f"{f.name}: {e}")
            log.warning("Library drawing %s could not be read: %s", f.name, e)
            continue
        names = [str(n) for n in d.get("product_names", [])]
        entry = {"id": f.stem, "key": next(iter(pids)) if pids else (own_key or library_key(f.stem)),
                 "title": d.get("title", f.stem), "status": status, "checked": d.get("checked", ""),
                 "pids": pids, "product_names": names, "commands": commands, "drawing": drawing,
                 # Without PIDs and product names the panel cannot be recognised: it can only be chosen by hand
                 # (Drawings tab → Use a library drawing), e.g. modules that are swapped on one base.
                 "manual": not pids and not names}
        for p in pids or [entry["key"]]:
            by_pid.setdefault(p, entry)
        for n in entry["product_names"]:
            by_name.setdefault(_norm_name(n), entry)
    return {"by_pid": by_pid, "by_name": by_name, "errors": errors}


def _library_drawing(entry: dict, pid: str) -> dict:
    return {**entry["drawing"], "title": entry["pids"].get(pid, entry["title"])}


def _same_drawing(a: dict, b: dict) -> bool:
    try:
        return clean_layout(a) == clean_layout(b)
    except ValueError:
        return False


def read_own_layouts(config_path: str) -> tuple[dict, str | None]:
    """Your layouts.json as it is on disk (raw), and an error text if it cannot be read."""
    try:
        with open(layouts_path(config_path), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("the file must contain a JSON object")
        return data, None
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return {}, f"layouts.json could not be read ({e}); only the library drawings are used until it is fixed."


def load_layouts(config_path: str) -> dict:
    """The panel drawings: the library, with your own drawings and changes from layouts.json on top.
    Each drawing gets "source": library / changed (your copy of a library drawing) / own, and for library panels
    "library": {id, status, checked, pids}. Returns {"layouts": {pid: layout}, "path", "error", "photos", "mtime"}."""
    lib = load_library()
    data, error = read_own_layouts(config_path)
    own = {k.upper(): v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
    for pid, lay in list(own.items()):  # resolve "same_as"
        src = str(lay.get("same_as", "")).upper()
        if not src:
            continue
        if src in own and not own[src].get("same_as"):
            own[pid] = {**own[src], **{k: v for k, v in lay.items() if k != "same_as"}, "shared_with": src}
        elif src in lib["by_pid"]:
            own[pid] = {**_library_drawing(lib["by_pid"][src], src), **{k: v for k, v in lay.items() if k != "same_as"}}
        else:
            del own[pid]
    layouts = {}
    for pid, entry in lib["by_pid"].items():
        info = {"id": entry["id"], "key": entry["key"], "title": entry["title"], "status": entry["status"],
                "checked": entry["checked"], "pids": list(entry["pids"]), "product_names": entry["product_names"],
                "manual": entry["manual"], "led_names": list(entry["commands"])}
        layouts[pid] = {**_library_drawing(entry, pid), "source": "library", "library": info}
    for pid, lay in own.items():
        base = layouts.get(pid)
        if base and _same_drawing(lay, base):
            continue  # an unchanged copy of the library drawing (e.g. layouts.json from before 1.6)
        layouts[pid] = {**lay, "source": "changed" if base else "own", **({"library": base["library"]} if base else {})}
    if lib["errors"]:
        error = ((error + " ") if error else "") + "Library: " + "; ".join(lib["errors"])
    return {"layouts": layouts, "path": str(layouts_path(config_path)), "error": error,
            "photos": list_photos(config_path), "mtime": file_mtime(layouts_path(config_path))}


# Drawings edited in the control panel ------------------------------------------------------
LAYOUT_KEY_RE = re.compile(r"^[0-9A-Z_]{1,16}$")
LED_SHAPES = ("btn", "round", "rect")
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PHOTO_BYTES = 8 * 1024 * 1024


def _pct(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not -50 <= v <= 150:
        raise ValueError(f"{what} must be a number (percent of the panel face)")
    v = round(float(v), 1)
    return int(v) if v == int(v) else v


def clean_layout(lay: dict) -> dict:
    """Checks a drawing sent by the control panel and returns it in the layouts.json form."""
    if not isinstance(lay, dict):
        raise ValueError("The drawing must be an object")
    out: dict = {}
    title = str(lay.get("title", "")).strip()[:80]
    if title:
        out["title"] = title
    aspect = str(lay.get("aspect", "")).strip()
    if aspect:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", aspect)
        if not m or float(m.group(1)) <= 0 or float(m.group(2)) <= 0:
            raise ValueError(f"Invalid aspect '{aspect}' (use width / height, e.g. 1310 / 1490)")
        out["aspect"] = f"{m.group(1)} / {m.group(2)}"
    if lay.get("btn") not in (None, ""):
        btn = lay["btn"]
        if isinstance(btn, bool) or not isinstance(btn, (int, float)) or not 2 <= btn <= 30:
            raise ValueError("Button size must be between 2 and 30 % of the panel width")
        out["btn"] = _pct(btn, "Button size")
    if lay.get("wide"):
        out["wide"] = True
    photo = lay.get("photo")
    if photo is True:
        out["photo"] = 0.5
    elif isinstance(photo, (int, float)) and not isinstance(photo, bool) and photo > 0:
        out["photo"] = round(min(float(photo), 1.0), 2)  # opacity of the photo behind the drawing
    deco = []
    for d in lay.get("deco") or []:
        if not isinstance(d, dict):
            raise ValueError("Each box must be an object")
        box = {k: _pct(d.get(k), f"Box {k}") for k in ("x", "y", "w", "h")}
        if box["w"] <= 0 or box["h"] <= 0:
            raise ValueError("A box needs a width and height above 0")
        box["text"] = str(d.get("text", ""))[:60]
        deco.append(box)
    out["deco"] = deco
    leds = {}
    for name, v in (lay.get("leds") or {}).items():
        if not LED_NAME_RE.match(str(name)):
            raise ValueError(f"Invalid LED name '{name}'")
        if not isinstance(v, list) or len(v) < 3 or v[2] not in LED_SHAPES:
            raise ValueError(f"LED '{name}' must be [x, y, shape, label?, below?] with shape btn, round or rect")
        entry = [_pct(v[0], f"x of {name}"), _pct(v[1], f"y of {name}"), v[2]]
        label = str(v[3]).strip()[:20] if len(v) > 3 and v[3] else ""
        below = len(v) > 4 and v[4] == "below"
        if label or below:
            entry.append(label)
        if below:
            entry.append("below")
        leds[str(name)] = entry
    out["leds"] = leds
    return out


def dump_layouts(data: dict) -> str:
    """layouts.json in the hand-written style: a blank line between the drawings, small ones on one line."""
    parts = []
    for k, v in data.items():
        one = json.dumps(v, ensure_ascii=False, separators=(", ", ": "))
        if isinstance(v, dict) and len(one) <= 100 and not any(isinstance(x, (dict, list)) for x in v.values()):
            body = "{ " + one[1:-1] + " }"
        else:
            body = dump_config(v).rstrip("\n").replace("\n", "\n  ")
        parts.append(f"  {json.dumps(k, ensure_ascii=False)}: {body}")
    return "{\n" + ",\n\n".join(parts) + "\n}\n"


def _own_layouts_for_edit(config_path: str, key: str, mtime: float | None) -> tuple[str, Path, dict]:
    key = str(key).upper()
    if not LAYOUT_KEY_RE.match(key):
        raise ValueError(f"Invalid drawing key '{key}'")
    path = layouts_path(config_path)
    ensure_layouts_file(config_path)
    if mtime and path.exists() and abs(file_mtime(path) - float(mtime)) > 0.001:
        raise ValueError("layouts.json has been changed since the editor was opened. Close the editor, "
                         "open it again and redo the change.")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("the file must contain a JSON object")
    except FileNotFoundError:
        data = {}
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"layouts.json could not be read ({e}). Fix or delete it first.")
    if str(data.get("_info", "")).startswith("Drawings of the panels in the control panel."):
        data["_info"] = LAYOUTS_INFO  # the text from before the library (1.5 and older)
    return key, path, data


def save_layout(config_path: str, key: str, layout: dict, mtime: float | None = None) -> str:
    """Saves one drawing into layouts.json (next to config.json), keeping the others. A drawing that reuses
    another one with same_as is saved into that one, so both stay the same. Changing a library drawing saves your
    own copy; for a library drawing shared by several PIDs (left/right panels) the copy is shared the same way.
    Backs up the old file."""
    key, path, data = _own_layouts_for_edit(config_path, key, mtime)
    clean = clean_layout(layout)
    existing = {k.upper(): k for k in data}
    target, note = key, ""
    raw = data.get(existing.get(key, key))
    entry = load_library()["by_pid"].get(key)
    group = [p for p in (entry["pids"] if entry else {}) if p != key]
    if isinstance(raw, dict) and raw.get("same_as"):
        src = str(raw["same_as"]).upper()
        if src in existing:
            target = src
            note = f" (shared with {existing[src]} through same_as)"
            src_title = data[existing[src]].get("title")
            if src_title and clean.get("title") == raw.get("title"):
                clean["title"] = src_title
    elif raw is None and entry and group and not any(p in existing for p in group):
        # Your first change to a library drawing used by several PIDs: one copy, shared like in the library.
        target = next(iter(entry["pids"]))
        if clean.get("title") == entry["pids"].get(key):
            clean["title"] = entry["pids"][target]
        for p in entry["pids"]:
            if p != target:
                data[p] = {"same_as": target, "title": entry["pids"][p]}
        note = f" (your copy of the library drawing, shared by {', '.join(entry['pids'])})"
    elif raw is None and entry:
        note = " (your copy of the library drawing)"
    real_key = existing.get(target, target)
    data[real_key] = clean
    _write_own_layouts(path, data)
    log.info("Saved the drawing %s in %s%s", real_key, path, note)
    return f"Drawing saved in layouts.json{note}."


def reset_layout(config_path: str, key: str, mtime: float | None = None) -> str:
    """Removes your own drawing for this PID from layouts.json, so the library drawing is used again (or, for a
    panel that is not in the library, the simple grid). Drawings shared with same_as are removed together."""
    key, path, data = _own_layouts_for_edit(config_path, key, mtime)
    entry = load_library()["by_pid"].get(key)
    existing = {k.upper(): k for k in data}
    remove = {key} | (set(entry["pids"]) if entry else set())
    raw = data.get(existing.get(key, key))
    if isinstance(raw, dict) and raw.get("same_as"):
        remove.add(str(raw["same_as"]).upper())
    changed = True
    while changed:  # also drop drawings that point at one being removed
        changed = False
        for k, v in data.items():
            if isinstance(v, dict) and str(v.get("same_as", "")).upper() in remove and k.upper() not in remove:
                remove.add(k.upper())
                changed = True
    gone = [existing[k] for k in remove if k in existing]
    if not gone:
        return "There was no drawing of your own to remove."
    for k in gone:
        del data[k]
    _write_own_layouts(path, data)
    log.info("Removed the drawings %s from %s", ", ".join(sorted(gone)), path)
    return ("Back to the library drawing." if entry else "Drawing removed – the panel is shown as a grid.") + \
        f" Removed from layouts.json: {', '.join(sorted(gone))}."


def _write_own_layouts(path: Path, data: dict) -> None:
    if path.exists():
        backups = path.with_name("backups")
        backups.mkdir(exist_ok=True)
        now = time.time()
        stamp = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{int(now * 1000) % 1000:03d}"
        (backups / f"layouts-{stamp}.json").write_bytes(path.read_bytes())
        for old in sorted(backups.glob("layouts-*.json"))[:-20]:  # keep the latest 20
            try:
                old.unlink()
            except OSError:
                pass
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(dump_layouts(data), encoding="utf-8")
    os.replace(tmp, path)


def photos_dir(config_path: str) -> Path:
    return Path(config_path).with_name("photos")


def list_photos(config_path: str) -> list[str]:
    try:
        return sorted(p.stem.upper() for p in photos_dir(config_path).iterdir()
                      if p.suffix.lower() in PHOTO_TYPES.values() and LAYOUT_KEY_RE.match(p.stem.upper()))
    except OSError:
        return []


def find_photo(config_path: str, key: str) -> Path | None:
    key = str(key).upper()
    if not LAYOUT_KEY_RE.match(key):
        return None
    for ext in PHOTO_TYPES.values():
        p = photos_dir(config_path) / f"{key}{ext}"
        if p.exists():
            return p
    return None


def save_photo(config_path: str, key: str, data_url: str | None) -> str:
    """Stores (or with no data removes) the background photo for a drawing in the photos folder.
    The photo stays on this PC – it is not part of layouts.json."""
    import base64
    import binascii
    key = str(key).upper()
    if not LAYOUT_KEY_RE.match(key):
        raise ValueError(f"Invalid drawing key '{key}'")
    old = find_photo(config_path, key)
    if not data_url:
        if old:
            old.unlink()
            log.info("Removed the photo for drawing %s", key)
            return "Photo removed."
        return "There was no photo."
    m = re.fullmatch(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/=\s]+)", str(data_url))
    if not m or m.group(1) not in PHOTO_TYPES:
        raise ValueError("The photo must be a JPEG, PNG or WebP image")
    try:
        raw = base64.b64decode(m.group(2))
    except binascii.Error:
        raise ValueError("The photo could not be read")
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("The photo is too large (max 8 MB)")
    folder = photos_dir(config_path)
    folder.mkdir(exist_ok=True)
    if old:
        old.unlink()
    (folder / f"{key}{PHOTO_TYPES[m.group(1)]}").write_bytes(raw)
    log.info("Saved the photo for drawing %s in %s", key, folder)
    return "Photo saved (only on this PC)."


def file_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def save_config(path: str, cfg: dict, backup_first: bool = True) -> Path:
    """Backs up config.json and writes the new one safely (via a temporary file). Returns the backup.
    backup_first=False is for small setting changes (e.g. the chosen profile) that should not push real
    backups out of the backups folder."""
    src = Path(path)
    backups = src.with_name("backups")
    backups.mkdir(exist_ok=True)
    now = time.time()
    backup = backups / f"config-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{int(now * 1000) % 1000:03d}.json"
    if src.exists() and backup_first:
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
# Finding connected Virpil devices, and the starter config for new users
# --------------------------------------------------------------------------
VIRPIL_VID = "3344"
# LED commands to try when no VPC profile says which LEDs a device has (01-20 = LEDs on the board).
FALLBACK_LED_CMDS = [f"{n:02d}" for n in range(1, 21)]


def connected_usb_devices(vid: str = VIRPIL_VID) -> list[str]:
    """The PIDs (upper case, unique, in order) of the USB devices with this VID that are plugged in now."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    cm = ctypes.WinDLL("cfgmgr32")
    flags = 0x1 | 0x100  # CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    size = wintypes.ULONG()
    if cm.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), "USB", flags) != 0 or not size.value:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if cm.CM_Get_Device_ID_ListW("USB", buf, size.value, flags) != 0:
        return []
    pids: list[str] = []
    for dev_id in ctypes.wstring_at(ctypes.addressof(buf), size.value).split("\0"):
        m = re.match(rf"USB\\VID_{vid}&PID_([0-9A-F]{{4}})(?:\\|$)", dev_id.upper())
        if m and m.group(1) not in pids:
            pids.append(m.group(1))
    return pids


def usb_product_name(vid: str, pid: str) -> str | None:
    """The name Windows shows for a game controller (e.g. 'VPC Panel #2'), or None."""
    if sys.platform != "win32":
        return None
    import winreg
    key = rf"System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM\VID_{vid}&PID_{pid}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return str(winreg.QueryValueEx(k, "OEMName")[0]).strip() or None
    except OSError:
        return None


def vpc_profile_leds() -> dict[str, dict]:
    """LEDs per PID from the newest VPC Configuration Tool profile in %LOCALAPPDATA%\\VIRPIL\\PROFILES:
    {pid: {"name": "VPC Panel #2", "cmds": ["05", "06", ...]}}. An LED is there when its type is not '---';
    its command number is the Tag value in the OUTPUT section."""
    import xml.etree.ElementTree as ET
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "VIRPIL" / "PROFILES"
    if not root.is_dir():
        return {}
    newest: dict[str, tuple[float, dict]] = {}
    for f in root.glob("*/*"):
        if f.suffix.lower() != ".xml":
            continue
        try:
            mtime = f.stat().st_mtime
            xml = ET.parse(f).getroot()
        except (OSError, ET.ParseError):
            continue
        pid_el, out = xml.find(".//dev_pid"), xml.find("OUTPUT")
        if pid_el is None or out is None:
            continue
        pid = str(pid_el.get("Data", "")).strip().upper()
        if not re.fullmatch(r"[0-9A-F]{4}", pid) or (pid in newest and newest[pid][0] >= mtime):
            continue
        # The VPC software from October 2026 saves profiles in a new form (cnt_led_rgb_01 instead of cnt_led_rgb_1, no
        # LED types). Its firmware numbers the board LEDs 01-20 instead of 05-24, see LED_NUMBERS. Only the zero-padded
        # 01-09 tell the forms apart: the classic form has cnt_led_rgb_10 to _20 too.
        new_form = any(re.fullmatch(r"cnt_led_rgb_0\d", el.tag) for el in out.iter())
        types = {el.tag[12:]: el.get("Data", "") for el in out.iter() if el.tag.startswith("cnt_led_typ_")}
        cmds = set()
        if not new_form:
            for el in out.iter():
                n, tag = el.tag[12:], str(el.get("Tag", ""))
                if el.tag.startswith("cnt_led_rgb_") and n.isdigit() and types.get(n, "---") != "---" \
                        and tag.isdigit() and 1 <= int(tag) <= 54:
                    cmds.add(f"{int(tag) - 4:02d}" if int(tag) >= 5 else f"{int(tag):02d}")  # old firmware: 05-24
        name = re.sub(r"^\d{8}-\d{6}-", "", f.stem).strip()
        # The grip chosen in the VPC Configuration Tool, e.g. "[FLIGHT GRIP] VPC WarBRD Grip" -> "VPC WarBRD Grip".
        g = xml.find(".//pfl_grip")
        grip_data = str(g.get("Data", "")) if g is not None else ""
        grip_name = re.sub(r"^\[[^\]]*\]\s*", "", grip_data).strip()
        # A base that may have grip LEDs: the classic form lists them (stk_led_rgb_1..4, commands 01-04) for every
        # stick base, whether the grip has LEDs or not; the new form names a [FLIGHT GRIP].
        grip = any(el.tag.startswith("stk_led_rgb_") for el in xml.iter()) or grip_data.upper().startswith("[FLIGHT GRIP]")
        newest[pid] = (mtime, {"name": name, "cmds": sorted(cmds), "grip": grip, "grip_name": grip_name,
                               "numbering": "2026" if new_form else "classic", "leds_known": not new_form})
    return {pid: info for pid, (_, info) in newest.items()}


# The LEDs a grip can have, as named for a stick base found by Find new devices (commands 01-04).
GRIP_LED_CMDS = {f"grip_led{i}": f"{i:02d}" for i in range(1, 5)}

# LED numbers: since 2.0 the bridge uses the numbers of Virpil firmware 20261027 (VPC Software Suite VPC-JOY-20261027),
# the same as VPC Configuration Tool shows: board LEDs 01-20, slave boards from 21. Older firmware counted the board
# LEDs from 05; a config.json from before 2.0 is converted once (see migrate_led_numbers).
LED_NUMBERS = "vpc-20261027"
REQUIRED_VPC_VERSION = "20261027"

# Setup files (Settings → Export / Import, and Export on a device card).
CONFIG_FILE_KEY = "cockpitlink_config"
PROFILE_FILE_KEY = "cockpitlink_profile"
# Files exported before 3.0, when the program was called Virpil LED Bridge, can still be imported.
OLD_CONFIG_FILE_KEY = "virpil_led_bridge_config"
OLD_PROFILE_FILE_KEY = "virpil_led_bridge_profile"


def _file_kind(data) -> str | None:
    """'config' or 'profile' for a file exported by this program (also before 3.0), otherwise None."""
    if not isinstance(data, dict):
        return None
    if data.get(CONFIG_FILE_KEY) == 1 or data.get(OLD_CONFIG_FILE_KEY) == 1:
        return "config"
    if data.get(PROFILE_FILE_KEY) == 1 or data.get(OLD_PROFILE_FILE_KEY) == 1:
        return "profile"
    return None
# Settings that an import of the whole setup may bring along (when ticked). Paths and ports of this PC are not.
IMPORT_SETTINGS = ("sim_poll_ms", "blink_interval_ms", "on_disconnect_color", "on_exit", "profiles_enabled",
                   "profile_select")


def migrate_led_numbers(cfg: dict) -> bool:
    """Converts a config.json made for the old firmware (board LEDs 05-24) to the new numbers (01-20).
    Commands 01-04 (grip LEDs) are kept. Returns True if something was changed."""
    if cfg.get("led_numbers") == LED_NUMBERS:
        return False
    for spec in cfg.get("devices", {}).values():
        if isinstance(spec, dict) and isinstance(spec.get("leds"), dict):
            spec["leds"] = {n: (f"{int(c) - 4:02d}" if str(c).isdigit() and int(c) >= 5 else c)
                            for n, c in spec["leds"].items()}
            spec.pop("led_numbering", None)
    cfg["led_numbers"] = LED_NUMBERS
    return True


_vpc_version_cache: dict = {}


def vpc_software_version(led_control_exe: str) -> str | None:
    """The VPC Software Suite version (e.g. "20261027"), read from VPC_JOY_SETUP.exe next to the tools folder that
    VPC_LED_Control.exe is in. Cached by file time, as the file is large. None if it cannot be found."""
    exe = Path(led_control_exe).parent.parent / "VPC_JOY_SETUP.exe"
    try:
        st = exe.stat()
    except OSError:
        return None
    key = (str(exe), st.st_mtime, st.st_size)
    if key not in _vpc_version_cache:
        version = None
        try:
            data = exe.read_bytes()
            m = re.search(rb"VPC-JOY-(\d{8})", data) or \
                re.search(rb"V\x00P\x00C\x00-\x00J\x00O\x00Y\x00-\x00((?:\d\x00){8})", data)
            if m:
                version = m.group(1).replace(b"\x00", b"").decode()
        except OSError:
            pass
        _vpc_version_cache.clear()
        _vpc_version_cache[key] = version
    return _vpc_version_cache[key]


DEFAULT_BASE_CONFIG = {
    "vpc_led_control": {"exe": r"C:\Program Files (x86)\VPC Software Suite\tools\VPC_LED_Control.exe",
                        "command_template": "\"{exe}\" {vid} {pid} {cmd} {r} {g} {b}"},
    "api": {"host": "127.0.0.1", "http_port": 8765, "udp_port": 8766},
    "sim_poll_ms": 200, "blink_interval_ms": 500, "on_disconnect_color": "startup", "on_exit": "startup",
    "led_numbers": LED_NUMBERS, "devices": {}, "rules": [],
}


def load_example_config(config_path: str) -> dict:
    """config.example.json next to config.json, or the copy bundled inside the exe."""
    for p in (Path(config_path).with_name("config.example.json"), resource_dir() / "config.example.json"):
        try:
            with open(p, encoding="utf-8") as f:
                return validate_config(json.load(f))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, json.JSONDecodeError) as e:
            log.warning("Could not read %s (%s).", p, e)
    return json.loads(json.dumps(DEFAULT_BASE_CONFIG))


def _norm_name(name: str) -> str:
    """Product names compared loosely: 'LEFT VPC Panel #1' and 'L-VPC Panel #1' are the same."""
    n = re.sub(r"\s+", " ", str(name).strip().lower())
    n = re.sub(r"^(left |l-)", "l-", n)
    return re.sub(r"^(right |r-)", "r-", n)


def _device_key(name: str, taken) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", re.sub(r"^vpc\s+", "", name.lower())).strip("_")[:24] or "device"
    if not base[0].isalpha():
        base = "d_" + base
    key, i = base, 2
    while key in taken:
        key, i = f"{base}_{i}", i + 1
    return key


def detect_devices(config_path: str, cfg: dict | None = None) -> list[dict]:
    """The Virpil devices plugged in now, each with a ready config entry:
    [{"key", "pid", "name", "source", "in_config", "spec", "example_key"}]. source is 'config' (already in
    config.json), 'known' (a panel with verified LED names in config.example.json), 'library' or
    'library_unverified' (LED names from a drawing in the library), 'profile' (LEDs from the
    VPC profile) or 'guess' (no profile: LEDs 05-24, to be checked in LED setup)."""
    example = load_example_config(config_path)["devices"]
    known_by_pid = {str(s["pid"]).upper(): (k, s) for k, s in example.items()}
    known_by_name = {_norm_name(s.get("name", k)): (k, s) for k, s in example.items()}
    library = load_library()
    profiles = vpc_profile_leds()
    current = (cfg or {}).get("devices", {})
    in_config = {str(s["pid"]).upper(): k for k, s in current.items()}
    taken = set(current)
    found = []
    for pid in connected_usb_devices():
        prof = profiles.get(pid)
        name = usb_product_name(VIRPIL_VID, pid) or (prof or {}).get("name") or f"Virpil device {pid}"
        if pid in in_config:
            k = in_config[pid]
            found.append({"key": k, "pid": pid, "name": current[k].get("name", k), "source": "config",
                          "in_config": True, "n_leds": len(set(current[k]["leds"].values())), "example_key": None})
            continue
        # Match on the PID first, then on the product name (PIDs can be changed, and left/right variants differ).
        known = known_by_pid.get(pid) or known_by_name.get(_norm_name(name))
        lib = None if known else (library["by_pid"].get(pid) or library["by_name"].get(_norm_name(name)))
        if lib and not lib["commands"]:
            lib = None
        grip_base = bool(prof is not None and not prof["cmds"] and prof.get("grip"))
        if grip_base and not known and not lib and prof.get("grip_name"):
            # The VPC profile says which grip is on the base: use its library drawing and LED names if we have it.
            g = library["by_name"].get(_norm_name(prof["grip_name"]))
            if g and g["commands"]:
                lib, grip_base = g, False
        if prof is not None and prof.get("leds_known", True) and not prof["cmds"] and not known and not lib                 and not grip_base:
            log.info("Found %s (PID %s), but its VPC profile has no LEDs – left out.", name, pid)
            continue
        if known:
            key = known[0] if known[0] not in taken else _device_key(name, taken)
            spec = json.loads(json.dumps(known[1]))
            if str(spec["pid"]).upper() != pid:  # the same panel under another PID: keep its drawing
                spec.setdefault("layout", str(spec["pid"]).upper())
            spec.update({"vid": VIRPIL_VID, "pid": pid})
            source = "known"
        elif lib:
            # A panel in the drawing library: its LED names and drawing (verified or not – see the status).
            key = _device_key(name, taken)
            spec = {"name": name, "vid": VIRPIL_VID, "pid": pid, "leds": dict(lib["commands"])}
            if pid not in lib["pids"]:
                spec["layout"] = lib["key"]  # another PID, or a panel known only by its product name
            source = "library" if lib["status"] == "verified" else "library_unverified"
        elif grip_base:
            # A stick (or other) base with no LEDs of its own: the grip on it may have up to four (commands 01-04).
            # Grips are swapped between bases, so which ones exist is checked with Walk through one by one.
            key = _device_key(name, taken)
            spec = {"name": name, "vid": VIRPIL_VID, "pid": pid, "leds": dict(GRIP_LED_CMDS)}
            source = "grip"
        else:
            key = _device_key(name, taken)
            # The new profile form (2026) does not say which LEDs exist: guess 05-24 like without a profile.
            cmds = prof["cmds"] if prof and prof["cmds"] else FALLBACK_LED_CMDS
            spec = {"name": name, "vid": VIRPIL_VID, "pid": pid,
                    "leds": {f"led{i:02d}": c for i, c in enumerate(cmds, 1)}}
            source = "profile" if prof and prof["cmds"] else "guess"
        taken.add(key)
        found.append({"key": key, "pid": pid, "name": spec.get("name", name), "source": source, "in_config": False,
                      "n_leds": len(set(spec["leds"].values())), "spec": spec,
                      "example_key": known[0] if known else None})
    # A steady order: devices already in the config, then known panels as in the example, then the rest by name.
    order = {k: i for i, k in enumerate(example)}
    cfg_order = {k: i for i, k in enumerate(current)}
    found.sort(key=lambda d: (not d["in_config"], cfg_order.get(d["key"], 0) if d["in_config"] else
                              order.get(d["example_key"], len(order)), d["name"].lower()))
    return found


def _colour_names(obj) -> set[str]:
    """All strings in a rule or base-colour entry (some of them colour names)."""
    if isinstance(obj, str):
        return {obj.lower()}
    if isinstance(obj, dict):
        return set().union(*(_colour_names(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(_colour_names(v) for v in obj)) if obj else set()
    return set()


def add_device_to_config(cfg: dict, example: dict, d: dict) -> int:
    """Adds one detected device to cfg. For a known panel, also its example rules and base colour – but only if
    cfg has no rules for that device yet. Returns the number of rules added."""
    key, ex_key = d["key"], d.get("example_key")
    cfg.setdefault("devices", {})[key] = d["spec"]
    if not ex_key or any(r.get("device") == key for r in cfg.get("rules", [])):
        return 0
    leds, added = d["spec"]["leds"], []
    for rule in example.get("rules", []):
        if rule.get("device") == ex_key and rule.get("led") in leds:
            added.append({**json.loads(json.dumps(rule)), "device": key})
    cfg.setdefault("rules", []).extend(added)
    start = example.get("startup", {}).get(ex_key)
    if start is not None:
        cfg.setdefault("startup", {}).setdefault(key, json.loads(json.dumps(start)))
    for c in _colour_names(added) | _colour_names(start):
        if c in example.get("colors", {}):
            cfg.setdefault("colors", {}).setdefault(c, example["colors"][c])
    return len(added)


SOURCE_TEXT = {"known": "known panel", "library": "LED names and drawing from the library",
               "library_unverified": "LED names and drawing from the library, not yet checked on a real panel – "
                                     "check them with LED setup",
               "profile": "LEDs from the VPC profile",
               "guess": "no VPC profile, LEDs 05-24 guessed – check them with LED setup",
               "grip": "a base without LEDs of its own – adds the grip's LEDs 01-04 (many grips have none; "
                       "check which ones yours has with Walk through one by one)"}


def make_starter_config(config_path: str) -> dict:
    """A new config.json from config.example.json and the Virpil devices plugged in now."""
    example = load_example_config(config_path)
    cfg = {k: v for k, v in example.items() if k not in ("_info", "devices", "rules", "startup", "colors")}
    cfg = {"_info": "Made by CockpitLink from the devices it found. Change it in the control panel, or by "
                    "hand and then press Reload config. config.example.json and the wiki show the format.",
           **cfg, "startup": {}, "colors": {}, "devices": {}, "rules": []}
    try:
        found = detect_devices(config_path)
    except Exception as e:  # noqa: BLE001 – a new user should still get a working (empty) config
        log.warning("Could not look for Virpil devices: %s", e)
        found = []
    for d in found:
        n = add_device_to_config(cfg, example, d)
        log.info("Found %s (PID %s): %d LEDs, %s%s.", d["name"], d["pid"], d["n_leds"], SOURCE_TEXT[d["source"]],
                 f", {n} example rules" if n else "")
    if not found:
        log.warning("No Virpil devices found. Connect them and press 'Find new devices' in the control panel.")
    return validate_config(cfg)


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
        if not self.no_sim and has_any_rules(self.cfg):
            self.rules = SimRules(self.cfg, self.leds)
            self.rules.start()
        threading.Thread(target=self._run_startup, daemon=True, name="startup").start()

    def _apply_base(self, cfg: dict) -> None:
        self.plan = startup_plan(cfg)
        self.leds.base = {(d, cfg["devices"][d]["leds"][l]): c for d, l, c in self.plan}

    def active_rules(self, cfg: dict | None = None) -> list[dict]:
        """The rules in use now: the rules for all aircraft, with the active profile's rules on top."""
        cfg = cfg or self.cfg
        if self.rules is not None and self.rules.cfg is cfg:
            return self.rules.rules
        return effective_rules(cfg, pick_profile(cfg, None)[0])

    def _rule_leds(self, cfg: dict) -> set[tuple[str, str]]:
        """Physical LEDs (device, command) that a rule drives."""
        out = set()
        for r in self.active_rules(cfg):
            names = cfg["devices"].get(r.get("device"), {}).get("leds", {})
            if r.get("led") in names:
                out.add((r["device"], names[r["led"]]))
        return out

    def _per_device(self, items: list[tuple[str, str, str]], work, name: str) -> None:
        """Runs work(device, led, colour) for the items, one thread per device so the panels are written in
        parallel. Within a device, LEDs driven by rules come first, so the important ones light up first."""
        cfg = self.cfg
        rule_leds = self._rule_leds(cfg)
        by_dev: dict[str, list[tuple[str, str, str]]] = {}
        for item in items:
            by_dev.setdefault(item[0], []).append(item)

        def run(dev: str, todo: list[tuple[str, str, str]]) -> None:
            names = cfg["devices"][dev]["leds"]
            todo.sort(key=lambda it: (dev, names.get(it[1])) not in rule_leds)  # stable: rule LEDs first
            for d, led, color in todo:
                try:
                    work(d, led, color)
                except ValueError as e:
                    log.error("%s: %s", name, e)

        threads = [threading.Thread(target=run, args=(d, t), daemon=True, name=f"{name}-{d}") for d, t in by_dev.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _run_startup(self, skip: set[tuple[str, str]] | None = None) -> None:
        skip = skip or set()
        devices = self.cfg["devices"]
        t0 = time.monotonic()

        def work(dev: str, led: str, color: str) -> None:
            if (dev, devices[dev]["leds"][led]) not in skip and not self.leds.is_taken(dev, led):
                self.leds.set(dev, led, color=color, source="startup")

        self._per_device(list(self.plan), work, "startup")
        log.info("Base colours set in %.1f s.", time.monotonic() - t0)

    def reload(self) -> str:
        """Reads config.json again and applies it without a restart. Returns a short message."""
        with self._reload_lock:
            cfg = load_config(self.config_path)  # ValueError on errors; the old config is kept
            self.cfg = cfg
            self.leds.configure(cfg)
            self._apply_base(cfg)
            if self.rules:
                self.rules.configure(cfg)
            elif not self.no_sim and has_any_rules(cfg):
                self.rules = SimRules(cfg, self.leds)
                self.rules.start()
            rule_leds = {(r["device"], cfg["devices"][r["device"]]["leds"][r["led"]]) for r in self.active_rules(cfg)}
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
                all_rules = cfg.get("rules", []) + [r for p in cfg.get("profiles", []) for r in p.get("rules", [])]
                for rule in all_rules:
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

    def _check_mtime(self, based_on_mtime: float | None) -> None:
        if based_on_mtime is not None and abs(self.config_mtime() - float(based_on_mtime)) > 1e-3:
            raise ValueError("config.json has changed since the rules were loaded (perhaps in another window "
                             "or by hand). Reload the page and make the change again.")

    def set_rules(self, rules: list, based_on_mtime: float | None = None, profile: str | None = None) -> str:
        """Saves the rules for all aircraft, or (with profile) the rules of that profile."""
        validate_rules_strict(rules)

        def change(cfg: dict) -> str:
            self._check_mtime(based_on_mtime)
            if not profile:
                cfg["rules"] = rules
                return f"Rules saved ({len(rules)} rules)."
            prof = next((p for p in cfg.get("profiles", []) if p.get("name") == profile), None)
            if prof is None:
                raise ValueError(f"There is no profile called '{profile}'")
            prof["rules"] = rules
            return f"Rules for the profile “{profile}” saved ({len(rules)} rules)."

        return self._edit_config(change)

    def set_profiles(self, profiles: list, based_on_mtime: float | None = None) -> str:
        """Saves the list of profiles (names, aircraft names to match, mode, order and rules)."""
        if not isinstance(profiles, list):
            raise ValueError("'profiles' must be a list")
        clean = []
        for p in profiles:
            if not isinstance(p, dict):
                raise ValueError("Each profile must be an object")
            validate_rules_strict(p.get("rules", []))
            out = {"name": str(p.get("name", "")).strip(),
                   "match": [m.strip() for m in p.get("match", []) if isinstance(m, str) and m.strip()]}
            if p.get("mode", "extend") != "extend":
                out["mode"] = p["mode"]
            if p.get("enabled", True) is False:
                out["enabled"] = False
            out["rules"] = p.get("rules", [])
            clean.append(out)

        def change(cfg: dict) -> str:
            self._check_mtime(based_on_mtime)
            old = {p.get("name") for p in cfg.get("profiles", [])}
            cfg["profiles"] = clean
            if cfg.get("profile_select", "auto") not in ("auto", "default") and \
                    cfg["profile_select"] not in {p["name"] for p in clean}:
                cfg["profile_select"] = "auto"  # the profile chosen by hand is gone
            new = {p["name"] for p in clean}
            added, removed = new - old, old - new
            msg = f"Profiles saved ({len(clean)})"
            if len(added) == 1 and len(removed) == 1:
                return msg + f" – renamed {next(iter(removed))} → {next(iter(added))}."
            if added:
                msg += f" – added: {', '.join(sorted(added))}"
            if removed:
                msg += f" – removed: {', '.join(sorted(removed))}"
            return msg + "."

        return self._edit_config(change)

    def set_profiles_enabled(self, enabled: bool) -> str:
        def change(cfg: dict) -> str:
            cfg["profiles_enabled"] = bool(enabled)
            if enabled and not cfg.get("profiles"):
                return "Aircraft profiles are on. Make a profile in the Rules tab."
            return "Aircraft profiles are on." if enabled else \
                "Aircraft profiles are off – the rules for all aircraft are used. Your profiles are kept."

        return self._edit_config(change)

    def select_profile(self, select: str) -> str:
        """Chooses a profile by hand ('auto', 'default' or a profile name). Kept until 'auto' is chosen
        again, also after a restart. Saved without a backup and without reloading."""
        select = str(select or "auto")
        names = {p.get("name") for p in self.cfg.get("profiles", [])}
        if select not in ("auto", "default") and select not in names:
            raise ValueError(f"There is no profile called '{select}'")
        with self._edit_lock:
            cfg = load_config(self.config_path)
            cfg["profile_select"] = select
            save_config(self.config_path, cfg, backup_first=False)
            self.cfg["profile_select"] = select
            if self.rules:
                self.rules.set_select(select)
        st = self.profile_status()
        what = "automatic" if select == "auto" else "chosen by hand"
        return f"Profile: {st['active'] or 'all aircraft'} ({what})."

    def profile_status(self) -> dict:
        cfg = self.cfg
        if self.rules is not None and self.rules.cfg is cfg:
            active, why = self.rules.profile, self.rules.profile_why
        else:
            prof, why = pick_profile(cfg, None)
            active = prof.get("name") if prof else None
        return {"enabled": bool(cfg.get("profiles_enabled")), "select": cfg.get("profile_select", "auto"),
                "active": active, "why": why, "names": [p.get("name") for p in cfg.get("profiles", [])]}

    # ---- sharing profiles as files ----
    def export_profile(self, name: str) -> dict:
        cfg = self.cfg
        prof = next((p for p in cfg.get("profiles", []) if p.get("name") == name), None)
        if prof is None:
            raise ValueError(f"There is no profile called '{name}'")
        used_devs = {r["device"] for r in prof.get("rules", [])}
        colours = _colour_names(prof.get("rules", [])) & set(cfg.get("colors", {}))
        return {
            PROFILE_FILE_KEY: 1,
            "bridge_version": VERSION,
            "exported": time.strftime("%Y-%m-%d"),
            "profile": prof,
            "devices": {d: {"name": cfg["devices"][d].get("name", d), "pid": str(cfg["devices"][d]["pid"]).upper()}
                        for d in sorted(used_devs) if d in cfg["devices"]},
            "colors": {c: cfg["colors"][c] for c in sorted(colours)},
        }

    def import_profile(self, data: dict) -> str:
        """Adds a profile from an exported file. Devices are matched by name in config.json, PID or product
        name; rules for devices or LEDs you do not have are left out (the message says which)."""
        if _file_kind(data) != "profile" or not isinstance(data.get("profile"), dict):
            raise ValueError("This is not a profile file exported by CockpitLink")
        src = data["profile"]
        src_devs = data.get("devices") or {}

        def change(cfg: dict) -> str:
            devs = cfg["devices"]
            mapping: dict = {}
            for d in {r.get("device") for r in src.get("rules", []) if isinstance(r, dict)}:
                info = src_devs.get(d, {}) if isinstance(src_devs.get(d), dict) else {}
                pid, nm = str(info.get("pid", "")).upper(), _norm_name(info.get("name", ""))
                target = d if d in devs and (not pid or str(devs[d]["pid"]).upper() == pid) else None
                target = target or next((k for k, v in devs.items() if pid and str(v["pid"]).upper() == pid), None)
                target = target or next((k for k, v in devs.items() if nm and _norm_name(v.get("name", k)) == nm), None)
                mapping[d] = target
            rules, skipped = [], []
            for r in src.get("rules", []):
                if not isinstance(r, dict):
                    continue
                t = mapping.get(r.get("device"))
                if t is None or r.get("led") not in devs[t]["leds"]:
                    skipped.append(str(r.get("name", "?")))
                    continue
                rules.append({**r, "device": t})
            validate_rules_strict(rules)
            for c, v in (data.get("colors") or {}).items():
                cfg.setdefault("colors", {}).setdefault(c, v)
            names = {p.get("name", "").lower() for p in cfg.get("profiles", [])}
            base = str(src.get("name", "Imported profile")).strip()[:50] or "Imported profile"
            name, i = base, 2
            while name.lower() in names or name.lower() in RESERVED_PROFILE_NAMES:
                name, i = f"{base} ({i})", i + 1
            new = {"name": name, "match": [m for m in src.get("match", []) if isinstance(m, str)]}
            if src.get("mode", "extend") in PROFILE_MODES and src.get("mode", "extend") != "extend":
                new["mode"] = src["mode"]
            new["rules"] = rules
            cfg.setdefault("profiles", []).append(new)
            msg = f"Profile “{name}” imported with {len(rules)} rules"
            if skipped:
                msg += f"; left out (device or LED not found): {', '.join(skipped)}"
            if not cfg.get("profiles_enabled"):
                msg += ". Aircraft profiles are off – turn them on in Settings to use it"
            return msg + "."

        return self._edit_config(change)

    # ---- export and import of the whole setup, or of one device ----
    def export_config(self, device: str | None = None) -> dict:
        """The setup as a file: everything (devices, LED names, rules, profiles, base colours, own colours, your
        drawings and settings), or with device just that device and the rules and base colours that drive it.
        Paths and ports of this PC (vpc_led_control, api) are left out."""
        cfg = self.cfg
        devs = cfg["devices"]
        if device is not None and device not in devs:
            raise ValueError(f"Unknown device '{device}'")
        keys = [device] if device else list(devs)
        rules = [r for r in cfg.get("rules", []) if r.get("device") in keys]
        profiles = []
        for p in cfg.get("profiles", []):
            prules = [r for r in p.get("rules", []) if r.get("device") in keys]
            if device and not prules:
                continue
            out = {k: v for k, v in p.items() if k != "rules"}
            out["rules"] = prules
            profiles.append(out)
        startup = {k: v for k, v in cfg.get("startup", {}).items() if k in keys}
        used = _colour_names(rules) | _colour_names(profiles) | _colour_names(startup)
        colors = {c: v for c, v in cfg.get("colors", {}).items() if device is None or c in used}
        own, _ = read_own_layouts(self.config_path)
        own = {k: v for k, v in own.items() if not k.startswith("_") and isinstance(v, dict)}
        if device:
            want = {str(devs[device].get("layout") or devs[device]["pid"]).upper()}
            for k in list(want):
                src = next((v.get("same_as") for kk, v in own.items() if kk.upper() == k and v.get("same_as")), None)
                if src:
                    want.add(str(src).upper())
            own = {k: v for k, v in own.items() if k.upper() in want}
        data = {
            CONFIG_FILE_KEY: 1,
            "scope": "device" if device else "all",
            "bridge_version": VERSION,
            "exported": time.strftime("%Y-%m-%d %H:%M"),
            "led_numbers": cfg.get("led_numbers", LED_NUMBERS),
            "devices": {k: devs[k] for k in keys},
            "rules": rules,
            "profiles": profiles,
            "startup": startup,
            "colors": colors,
            "layouts": own,
        }
        if not device:
            data["settings"] = {k: cfg[k] for k in IMPORT_SETTINGS if k in cfg}
        return data

    def _read_import(self, data) -> dict:
        """Checks an exported file and returns a clean copy, with the LED numbers converted if it was made for the
        old firmware."""
        if _file_kind(data) != "config":
            if _file_kind(data) == "profile":
                raise ValueError("This is an aircraft profile file – import it in the Rules tab (Import …).")
            raise ValueError("This is not a setup file exported by CockpitLink")
        d = json.loads(json.dumps(data))
        devices = d.get("devices")
        if not isinstance(devices, dict) or not devices:
            raise ValueError("The file has no devices")
        for key, spec in devices.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("leds"), dict) or "pid" not in spec:
                raise ValueError(f"Device '{key}' in the file is incomplete")
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(key)):
                raise ValueError(f"Invalid device key '{key}' in the file")
            if not re.fullmatch(r"[0-9A-Fa-f]{4}", str(spec["pid"])):
                raise ValueError(f"Device '{key}' has an invalid PID '{spec['pid']}'")
            for n, c in spec["leds"].items():
                if not LED_NAME_RE.match(str(n)) or not re.fullmatch(r"\d{2}", str(c)) or not 1 <= int(c) <= 54:
                    raise ValueError(f"Device '{key}' has an invalid LED '{n}': '{c}'")
            if spec.get("layout") is not None and not LAYOUT_KEY_RE.match(str(spec["layout"]).upper()):
                raise ValueError(f"Device '{key}' has an invalid drawing key")
        for k in ("rules", "profiles"):
            if not isinstance(d.get(k, []), list):
                raise ValueError(f"'{k}' in the file must be a list")
        for k in ("startup", "colors", "layouts", "settings"):
            if not isinstance(d.get(k, {}), dict):
                raise ValueError(f"'{k}' in the file must be an object")
        d["converted"] = d.get("led_numbers") != LED_NUMBERS
        if d["converted"]:  # made for the old firmware (board LEDs 05-24)
            fake = {"devices": devices}
            migrate_led_numbers(fake)
        return d

    @staticmethod
    def _import_targets(cfg: dict, file_devices: dict) -> dict[str, str | None]:
        """For each device in the file, the device in config.json it is: same key and PID, else same PID, else same
        name. Each device in config.json is matched at most once."""
        devs = cfg["devices"]
        out: dict[str, str | None] = {k: None for k in file_devices}
        taken: set[str] = set()
        tests = [lambda k, s, dk, dv: dk == k and str(dv["pid"]).upper() == str(s["pid"]).upper(),
                 lambda k, s, dk, dv: str(dv["pid"]).upper() == str(s["pid"]).upper(),
                 lambda k, s, dk, dv: _norm_name(dv.get("name", dk)) == _norm_name(s.get("name", k))]
        for test in tests:
            for k, s in file_devices.items():
                if out[k] is None:
                    out[k] = next((dk for dk, dv in devs.items() if dk not in taken and test(k, s, dk, dv)), None)
                    if out[k]:
                        taken.add(out[k])
        return out

    def preview_import(self, data) -> dict:
        """What importing the file would do, for the control panel to show before anything is changed."""
        d = self._read_import(data)
        cfg = self.cfg
        rows = []
        try:
            present = set(connected_usb_devices())
        except Exception:  # noqa: BLE001 – only used to tell the user which devices are plugged in
            present = set()
        targets = self._import_targets(cfg, d["devices"])
        for key, spec in d["devices"].items():
            target = targets[key]
            n_prof = sum(1 for p in d.get("profiles", []) if isinstance(p, dict)
                         for r in p.get("rules", []) if isinstance(r, dict) and r.get("device") == key)
            lay_key = str(spec.get("layout") or spec["pid"]).upper()
            rows.append({
                "key": key, "name": spec.get("name", key), "pid": str(spec["pid"]).upper(),
                "n_leds": len(set(spec["leds"].values())),
                "n_rules": sum(1 for r in d.get("rules", []) if isinstance(r, dict) and r.get("device") == key),
                "n_profile_rules": n_prof,
                "drawing": any(k.upper() == lay_key for k in d.get("layouts", {})),
                "base_colour": key in d.get("startup", {}),
                "target": target,
                "target_name": cfg["devices"][target].get("name", target) if target else None,
                "plugged_in": str(spec["pid"]).upper() in present,
            })
        mine = {p.get("name", "").lower() for p in cfg.get("profiles", [])}
        profs = [{"name": p.get("name", "?"), "exists": str(p.get("name", "")).lower() in mine,
                  "n_rules": len(p.get("rules", []))} for p in d.get("profiles", []) if isinstance(p, dict)]
        return {"scope": d.get("scope", "all"), "exported": d.get("exported"), "bridge_version": d.get("bridge_version"),
                "converted": d["converted"], "devices": rows, "profiles": profs,
                "colors": sorted(d.get("colors", {})), "settings": sorted(d.get("settings", {}))}

    def import_config(self, data, choices: dict, settings: bool = False) -> str:
        """Imports a setup file. choices says per device in the file: 'replace' (the matching device in config.json
        gets the file's LED names, drawing, rules and base colour), 'add' (a new device) or 'skip'. config.json and
        layouts.json are backed up first."""
        d = self._read_import(data)
        if not isinstance(choices, dict):
            raise ValueError("'choices' must be an object")
        drawings_to_write: dict[str, dict] = {}

        def change(cfg: dict) -> str:
            devs = cfg["devices"]
            mapping: dict[str, str] = {}
            done, skipped = [], []
            targets = self._import_targets(cfg, d["devices"])
            for key, spec in d["devices"].items():
                act = choices.get(key, "skip")
                target = targets[key]
                if act == "replace" and not target:
                    act = "add"
                if act not in ("replace", "add"):
                    skipped.append(spec.get("name", key))
                    continue
                new = {k: v for k, v in spec.items() if k in ("name", "vid", "pid", "leds", "layout")}
                new["vid"] = str(new.get("vid", VIRPIL_VID))
                src_lay = str(spec.get("layout") or spec["pid"]).upper()
                if act == "replace":
                    dst = target
                    old_pid = str(devs[dst]["pid"]).upper()
                    new.update({"vid": devs[dst]["vid"], "pid": devs[dst]["pid"]})  # the real device keeps its IDs
                    if "layout" not in new and src_lay != old_pid:
                        new["layout"] = src_lay  # keep the drawing from the file
                    # Out with the old rules and base colour of this device.
                    cfg["rules"] = [r for r in cfg.get("rules", []) if r.get("device") != dst]
                    for p in cfg.get("profiles", []):
                        p["rules"] = [r for r in p.get("rules", []) if r.get("device") != dst]
                    cfg.get("startup", {}).pop(dst, None)
                    done.append(f"{devs[dst].get('name', dst)} replaced")
                else:
                    dst = key if key not in devs else _device_key(spec.get("name", key), set(devs))
                    done.append(f"{spec.get('name', key)} added" + (f" as “{dst}”" if dst != key else ""))
                if new.get("layout") and str(new["layout"]).upper() == str(new["pid"]).upper():
                    new.pop("layout")
                devs[dst] = new
                mapping[key] = dst
                for k, v in d.get("layouts", {}).items():
                    if k.upper() == src_lay and isinstance(v, dict):
                        drawings_to_write[k.upper()] = v
                        src = str(v.get("same_as", "")).upper()
                        if src:
                            for k2, v2 in d["layouts"].items():
                                if k2.upper() == src and isinstance(v2, dict):
                                    drawings_to_write[src] = v2
            if not mapping:
                raise ValueError("Nothing to import – every device was skipped")

            def moved(rules) -> list:
                return [{**r, "device": mapping[r["device"]]} for r in rules
                        if isinstance(r, dict) and r.get("device") in mapping]

            added_rules = moved(d.get("rules", []))
            validate_rules_strict(added_rules)
            cfg.setdefault("rules", []).extend(added_rules)
            n_prof_rules, new_profiles = 0, []
            for p in d.get("profiles", []):
                if not isinstance(p, dict):
                    continue
                prules = moved(p.get("rules", []))
                validate_rules_strict(prules)
                name = str(p.get("name", "")).strip()[:60]
                mine = next((q for q in cfg.get("profiles", []) if q.get("name", "").lower() == name.lower()), None)
                if mine is not None:
                    mine.setdefault("rules", []).extend(prules)
                elif name and name.lower() not in RESERVED_PROFILE_NAMES and (prules or d.get("scope") == "all"):
                    q = {"name": name, "match": [m for m in p.get("match", []) if isinstance(m, str)]}
                    if p.get("mode", "extend") in PROFILE_MODES and p.get("mode", "extend") != "extend":
                        q["mode"] = p["mode"]
                    if p.get("enabled", True) is False:
                        q["enabled"] = False
                    q["rules"] = prules
                    cfg.setdefault("profiles", []).append(q)
                    new_profiles.append(name)
                n_prof_rules += len(prules)
            for key, dst in mapping.items():
                if key in d.get("startup", {}):
                    cfg.setdefault("startup", {})[dst] = d["startup"][key]
            clash = []
            for c, v in d.get("colors", {}).items():
                mine_c = cfg.setdefault("colors", {})
                if c in mine_c and mine_c[c] != v:
                    clash.append(c)
                else:
                    mine_c[c] = v
            if settings:
                for k, v in d.get("settings", {}).items():
                    if k in IMPORT_SETTINGS:
                        cfg[k] = v
            msg = "Imported: " + "; ".join(done)
            msg += f". {len(added_rules)} rules" + (f", {n_prof_rules} profile rules" if n_prof_rules else "")
            if new_profiles:
                msg += f", new profiles: {', '.join(new_profiles)}"
            if skipped:
                msg += f". Skipped: {', '.join(skipped)}"
            if clash:
                msg += f". Kept your own colours with the same name: {', '.join(clash)}"
            if d["converted"]:
                msg += ". LED numbers converted from the old firmware (05–24 → 01–20)"
            if settings and d.get("settings"):
                msg += ". Settings imported"
            return msg + "."

        msg = self._edit_config(change)
        if drawings_to_write:
            try:
                path = layouts_path(self.config_path)
                ensure_layouts_file(self.config_path)
                own, err = read_own_layouts(self.config_path)
                if err:
                    raise ValueError(err)
                existing = {k.upper(): k for k in own}
                for k, v in drawings_to_write.items():
                    if v.get("same_as"):
                        own[existing.get(k, k)] = {"same_as": str(v["same_as"]).upper(),
                                                   **({"title": str(v["title"])[:80]} if v.get("title") else {})}
                    else:
                        own[existing.get(k, k)] = clean_layout(v)
                _write_own_layouts(path, own)
                msg += f" Drawings saved in layouts.json: {', '.join(sorted(drawings_to_write))}."
            except (OSError, ValueError) as e:
                msg += f" The drawings could not be saved: {e}"
                log.error("Import: the drawings could not be saved: %s", e)
        log.info("%s", msg)
        return msg

    def config_for_ui(self) -> dict:
        """The rules and colours as the rule editor needs them."""
        cfg = self.cfg
        colors = {**DEFAULT_COLORS, **{k: tuple(v) for k, v in cfg.get("colors", {}).items()}}
        return {
            "rules": cfg.get("rules", []),
            "profiles": cfg.get("profiles", []),
            "profiles_enabled": bool(cfg.get("profiles_enabled")),
            "profile_select": cfg.get("profile_select", "auto"),
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

    def set_device_layout(self, device: str, key: str | None, take_names: bool = False) -> str:
        """Chooses which drawing a device uses: a library drawing (by its key) or, with no key, the device's own
        (by its PID). With take_names, the library's LED names are added to the device as extra names for the same
        command numbers – existing names (and the rules that use them) are kept."""
        key = str(key or "").upper()
        lib_entry = None
        if key:
            if not LAYOUT_KEY_RE.match(key):
                raise ValueError(f"Invalid drawing key '{key}'")
            if key not in load_layouts(self.config_path)["layouts"]:
                raise ValueError(f"There is no drawing called '{key}'")
            lib_entry = load_library()["by_pid"].get(key)

        def change(cfg: dict) -> str:
            if device not in cfg["devices"]:
                raise ValueError(f"Unknown device '{device}'")
            spec = cfg["devices"][device]
            name = spec.get("name", device)
            if not key or key == str(spec["pid"]).upper():
                spec.pop("layout", None)
                msg = f"{name} uses its own drawing again (PID {str(spec['pid']).upper()})."
            else:
                spec["layout"] = key
                msg = f"{name} now uses the drawing “{lib_entry['title'] if lib_entry else key}”."
            if take_names and lib_entry and lib_entry["commands"]:
                leds: dict = spec["leds"]
                added, clash = [], []
                for n, c in lib_entry["commands"].items():
                    if n not in leds:
                        leds[n] = c
                        added.append(n)
                    elif leds[n] != c:
                        clash.append(n)
                if added:
                    msg += f" LED names added from the library: {', '.join(added)}."
                if clash:
                    msg += f" Already used for other LEDs, so not added: {', '.join(clash)}."
            return msg

        return self._edit_config(change)

    def find_devices(self) -> list[dict]:
        """The Virpil devices plugged in now, for 'Find new devices' (without the full config entries)."""
        return [{k: v for k, v in d.items() if k != "spec"} | {"source_text": SOURCE_TEXT.get(d["source"], "")}
                for d in detect_devices(self.config_path, self.cfg)]

    def add_devices(self, pids: list[str]) -> str:
        """Adds the given detected devices (by PID) to config.json, with example rules for known panels."""
        pids = [str(p).upper() for p in pids]
        if not pids:
            raise ValueError("Choose at least one device")

        def change(cfg: dict) -> str:
            example = load_example_config(self.config_path)
            new = {d["pid"]: d for d in detect_devices(self.config_path, cfg) if not d["in_config"]}
            missing = [p for p in pids if p not in new]
            if missing:
                raise ValueError(f"Not found or already added: {', '.join(missing)}. Press Find new devices again.")
            parts = []
            for p in pids:
                d = new[p]
                n = add_device_to_config(cfg, example, d)
                parts.append(d["name"] + (f" (with {n} example rules)" if n else ""))
            return "Added " + ", ".join(parts) + "."

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
        items = list(self.plan) if on_exit == "startup" else []
        covered = {d for d, _, _ in items}
        for dev, spec in self.cfg["devices"].items():
            if dev not in covered:  # devices without base colours are turned off, once per physical LED
                done = set()
                for led, cmd in spec["leds"].items():
                    if cmd not in done:
                        done.add(cmd)
                        items.append((dev, led, "off"))
        self._per_device(items, lambda d, l, c: self.leds.set(d, l, color=c, source="shutdown"), "shutdown")
        log.info("LEDs set. Done.")

    def led_status(self) -> dict:
        """VPC_LED_Control health plus which devices in config.json are plugged in (checked at most every 3 s)."""
        st = self.leds.health()
        now = time.monotonic()
        if now - getattr(self, "_usb_checked", 0.0) > 3.0:
            try:
                self._usb_present = set(connected_usb_devices())
            except Exception:  # noqa: BLE001 – not on Windows, or the device list could not be read
                self._usb_present = None
            self._usb_checked = now
        present = getattr(self, "_usb_present", None)
        devs = self.cfg["devices"]
        if present is None:
            st["devices"] = {"known": False, "total": len(devs), "connected": None, "missing": []}
        else:
            missing = [v.get("name", k) for k, v in devs.items() if str(v["pid"]).upper() not in present]
            st["devices"] = {"known": True, "total": len(devs), "connected": len(devs) - len(missing),
                             "missing": missing}
        st.update(self.requirement_status())
        return st

    def requirement_status(self) -> dict:
        """Is the VPC software VPC-JOY-20261027 (or newer) installed, and were the devices set up with it? A device
        whose newest VPC profile is in the old form most likely still has the old firmware (other LED numbers).
        Checked at most every 30 s."""
        now = time.monotonic()
        if now - getattr(self, "_req_checked", -1e9) > 30.0:
            version = vpc_software_version(self.leds.exe)
            try:
                profiles = vpc_profile_leds()
            except Exception:  # noqa: BLE001
                profiles = {}
            old = [v.get("name", k) for k, v in self.cfg["devices"].items()
                   if profiles.get(str(v["pid"]).upper(), {}).get("numbering") == "classic"]
            self._req = {"vpc_version": version, "vpc_required": REQUIRED_VPC_VERSION,
                         "vpc_ok": None if version is None else version >= REQUIRED_VPC_VERSION,
                         "old_firmware": old}
            if self._req["vpc_ok"] is False:
                log.warning("VPC Software Suite %s is older than the required %s – update it and the firmware of "
                            "your devices.", version, REQUIRED_VPC_VERSION)
            if old:
                log.warning("These devices seem to have firmware older than %s (their VPC profile is in the old "
                            "form): %s. Update their firmware in VPC Configuration Tool.", REQUIRED_VPC_VERSION,
                            ", ".join(old))
            self._req_checked = now
        return dict(self._req)

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
                            "layout": str(dev.get("layout", dev["pid"])).upper(), "leds": leds})
        rules = self.rules
        return {
            "version": VERSION,
            "started": self.started,
            "uptime_s": round(time.time() - self.started),
            "config_path": self.config_path,
            "data_dir": {"path": str(Path(self.config_path).parent), **DATA_INFO},
            "config_loaded_at": self.loaded_at,
            "dry_run": self.dry_run,
            "sim": {
                "enabled": rules is not None,
                "connected": bool(rules and rules.connected),
                "aircraft": rules.aircraft if rules else None,
                "connected_since": rules.connected_since if rules else None,
            },
            "led_control": self.led_status(),
            "n_rules": len(self.active_rules()),
            "profile": self.profile_status(),
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
                elif path in ("/api/layouts", "/api/layouts/reset") and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        if path == "/api/layouts/reset":
                            msg = reset_layout(bridge.config_path, p["key"], p.get("mtime"))
                        else:
                            msg = save_layout(bridge.config_path, p["key"], p["layout"], p.get("mtime"))
                        self._reply(200, {"ok": True, "message": msg})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not write layouts.json: {e}"})
                elif path == "/api/layouts":
                    self._reply(200, load_layouts(bridge.config_path))
                elif path == "/api/photo" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        self._reply(200, {"ok": True, "message": save_photo(bridge.config_path, p["key"], p.get("data"))})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not save the photo: {e}"})
                elif path == "/api/photo":
                    photo = find_photo(bridge.config_path, p.get("key", ""))
                    if not photo:
                        return self._reply(404, {"ok": False, "error": "No photo"})
                    ctype = {v: k for k, v in PHOTO_TYPES.items()}[photo.suffix.lower()]
                    self._send(200, photo.read_bytes(), ctype)
                elif path == "/api/config" and self.command == "GET":
                    self._reply(200, bridge.config_for_ui())
                elif path == "/api/rules" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        self._reply(200, {"ok": True, "message": bridge.set_rules(p["rules"], p.get("mtime"),
                                                                                   p.get("profile"))})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not write config.json: {e}"})
                elif path.startswith("/api/profiles") and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        if path == "/api/profiles":
                            msg = bridge.set_profiles(p["profiles"], p.get("mtime"))
                        elif path == "/api/profiles/enabled":
                            msg = bridge.set_profiles_enabled(bool(p["enabled"]))
                        elif path == "/api/profiles/select":
                            msg = bridge.select_profile(p["select"])
                        elif path == "/api/profiles/import":
                            msg = bridge.import_profile(p["data"])
                        else:
                            return self._reply(404, {"ok": False, "error": "Unknown endpoint"})
                        self._reply(200, {"ok": True, "message": msg, "profile": bridge.profile_status()})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not write config.json: {e}"})
                elif path in ("/api/config/import", "/api/config/import/preview") and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    if path.endswith("/preview"):
                        self._reply(200, {"ok": True, "preview": bridge.preview_import(p["data"])})
                    else:
                        try:
                            msg = bridge.import_config(p["data"], p.get("choices") or {}, bool(p.get("settings")))
                            self._reply(200, {"ok": True, "message": msg})
                        except OSError as e:
                            self._reply(500, {"ok": False, "error": f"Could not write config.json: {e}"})
                elif path == "/api/config/export":
                    dev = p.get("device") or None
                    data = bridge.export_config(dev)
                    if dev:
                        base = data["devices"][dev].get("name", dev)
                    else:
                        base = "CockpitLink-setup-" + time.strftime("%Y%m%d")
                    fname = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "setup"
                    body = dump_config(data).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}.cockpitlink-setup.json"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/profiles/export":
                    data = bridge.export_profile(p["name"])
                    fname = re.sub(r"[^A-Za-z0-9._-]+", "-", data["profile"]["name"]).strip("-") or "profile"
                    body = (dump_config(data)).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}.cockpitlink-profile.json"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
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
                elif path == "/api/devices/layout" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        msg = bridge.set_device_layout(p["device"], p.get("layout"), bool(p.get("take_names")))
                        self._reply(200, {"ok": True, "message": msg})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not write config.json: {e}"})
                elif path == "/api/devices/find":
                    self._reply(200, {"devices": bridge.find_devices()})
                elif path == "/api/devices/add" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        pids = p["pids"] if isinstance(p["pids"], list) else [p["pids"]]
                        self._reply(200, {"ok": True, "message": bridge.add_devices(pids)})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not write config.json: {e}"})
                elif path == "/api/autostart" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        self._reply(200, {"ok": True, "message": set_autostart(bool(p["enabled"]))})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not change the setting: {e}"})
                elif path == "/api/open-data-folder" and self.command == "POST":
                    if not self._from_ui():
                        return self._reply(403, {"ok": False, "error": "Missing X-Bridge-UI"})
                    try:
                        open_data_folder(bridge.config_path)
                        self._reply(200, {"ok": True, "message": "The data folder is open in Explorer."})
                    except OSError as e:
                        self._reply(500, {"ok": False, "error": f"Could not open the folder: {e}"})
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
_RUN_NAME = "CockpitLink"
_OLD_RUN_NAME = "VirpilLedBridge"  # before 3.0


def autostart_command() -> str | None:
    """The command Windows should run at sign-in, or None when not running as the exe."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    return f'"{sys.executable}"'


# The Microsoft Store version starts with Windows through a startup task in its package, not the Run key.
STORE_PACKAGE_FAMILY = "eirik75.CockpitLink_88e0d0m75ww4e"
STORE_STARTUP_TASK = "CockpitLinkStartup"


def store_autostart_enabled() -> bool:
    """True when CockpitLink from the Microsoft Store is installed and set to start with Windows."""
    if os.name != "nt":
        return False
    import winreg
    key = (r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\SystemAppData"
           f"\\{STORE_PACKAGE_FAMILY}\\{STORE_STARTUP_TASK}")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            state, _ = winreg.QueryValueEx(k, "State")
    except OSError:
        return False
    return state in (2, 4)  # enabled, enabled by policy


_PACKAGED: bool | None = None


def running_packaged() -> bool:
    """True when this copy runs as the Microsoft Store (MSIX) app."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = False
        if os.name == "nt":
            try:
                import ctypes
                length = ctypes.c_uint32(0)
                # APPMODEL_ERROR_NO_PACKAGE (15700) means: not running from a package
                _PACKAGED = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None) != 15700
            except (AttributeError, OSError):
                _PACKAGED = False
    return _PACKAGED


def _run_key_value() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            return winreg.QueryValueEx(k, _RUN_NAME)[0]
    except OSError:
        return None


def autostart_status() -> dict:
    if running_packaged():
        # The Store app starts with Windows through its startup task, switched in Windows Settings → Apps → Startup.
        enabled = store_autostart_enabled()
        return {"available": True, "enabled": enabled, "store_app": True,
                "both": enabled and _run_key_value() is not None}
    status = _autostart_status()
    status["store_enabled"] = store_autostart_enabled()
    status["both"] = bool(status.get("enabled")) and status["store_enabled"]
    return status


def _autostart_status() -> dict:
    cmd = autostart_command()
    if cmd is None:
        return {"available": False, "enabled": False, "reason": "Only available when running CockpitLink.exe"}
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


def migrate_autostart() -> None:
    """Before 3.0 the entry was called VirpilLedBridge and pointed at VirpilLedBridge.exe. If it is there, it is
    replaced by one for this exe, so Start with Windows stays on after the update."""
    cmd = autostart_command()
    if cmd is None or running_packaged():
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            try:
                winreg.QueryValueEx(k, _OLD_RUN_NAME)
            except OSError:
                return
            winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, cmd)
            winreg.DeleteValue(k, _OLD_RUN_NAME)
        log.info("Start with Windows moved from VirpilLedBridge to CockpitLink (%s).", cmd)
    except OSError as e:
        log.warning("Could not move the Start with Windows entry from VirpilLedBridge: %s", e)


def set_autostart(enabled: bool) -> str:
    if running_packaged():
        # A Store app cannot switch its own startup task on without asking; Windows Settings does it.
        os.startfile("ms-settings:startupapps")  # noqa: S606
        return ("Windows Settings → Apps → Startup is open: switch CockpitLink " + ("on" if enabled else "off")
                + " there.")
    cmd = autostart_command()
    if cmd is None:
        raise ValueError("Start with Windows is only available when running CockpitLink.exe")
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
def open_data_folder(config_path: str) -> None:
    folder = Path(config_path).parent
    if os.name == "nt":
        os.startfile(folder)  # noqa: S606 – opens the folder in Explorer
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def make_icon_image(size: int = 64):
    """The CockpitLink logo: an attitude indicator whose horizon is a link with two nodes (the same drawing as
    docs/images/cockpitlink-logo.svg, on a 64-unit grid). Drawn 8x larger and scaled down for smooth edges."""
    from PIL import Image, ImageDraw

    k = size * 8 / 64
    big = int(64 * k)

    def box(cx, cy, r):
        return (round((cx - r) * k), round((cy - r) * k), round((cx + r) * k), round((cy + r) * k))

    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse(box(32, 32, 29.5), fill=(0x15, 0x18, 0x1D, 255))
    # sky and ground inside the dial
    dial = Image.new("RGBA", (big, big), (0x2F, 0x7F, 0xD1, 255))
    ImageDraw.Draw(dial).rectangle((0, round(33 * k), big, big), fill=(0x8A, 0x5A, 0x2B, 255))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse(box(32, 32, 27), fill=255)
    img.paste(dial, (0, 0), mask)
    # light grey rim
    d.ellipse(box(32, 32, 29), outline=(0xC9, 0xCE, 0xD6, 255), width=round(3 * k))
    # the link: a white bar with yellow nodes at the ends
    d.rounded_rectangle((round(10.5 * k), round(30.5 * k), round(53.5 * k), round(35.5 * k)), radius=round(2.5 * k),
                        fill=(0xF5, 0xF6, 0xF7, 255))
    for cx in (13, 51):
        d.ellipse(box(cx, 33, 7), fill=(0x15, 0x18, 0x1D, 255))
        d.ellipse(box(cx, 33, 5), fill=(0xE8, 0xC2, 0x3A, 255))
    return img.resize((size, size), Image.LANCZOS)


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
            icon.notify(bridge.reload(), "CockpitLink")
        except ValueError as e:
            log.error("Could not read the config: %s", e)
            icon.notify(str(e), "CockpitLink – config error")

    def quit_app(icon, item):
        bridge.request_stop()

    def toggle_autostart(icon, item):
        try:
            icon.notify(set_autostart(not autostart_status()["enabled"]), "CockpitLink")
        except (ValueError, OSError) as e:
            log.error("Start with Windows: %s", e)
            icon.notify(str(e), "CockpitLink")

    def choose(select: str):
        def run(icon, item):
            try:
                icon.notify(bridge.select_profile(select), "CockpitLink")
            except (ValueError, OSError) as e:
                log.error("Profile: %s", e)
                icon.notify(str(e), "CockpitLink")
        return run

    def is_selected(select: str):
        return lambda item: bridge.cfg.get("profile_select", "auto") == select

    def profile_items():
        items = [pystray.MenuItem("Automatic (by aircraft)", choose("auto"), checked=is_selected("auto"), radio=True),
                 pystray.MenuItem("All aircraft", choose("default"), checked=is_selected("default"), radio=True),
                 pystray.Menu.SEPARATOR]
        for name in bridge.profile_status()["names"]:
            items.append(pystray.MenuItem(name, choose(name), checked=is_selected(name), radio=True))
        return items

    def profile_title(item):
        st = bridge.profile_status()
        return f"Profile: {st['active'] or 'all aircraft'}"

    menu = pystray.Menu(
        pystray.MenuItem(f"CockpitLink v{VERSION}", lambda icon, item: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open control panel", open_ui, default=True),
        pystray.MenuItem(profile_title, pystray.Menu(profile_items),
                         visible=lambda item: profiles_on(bridge.cfg)),
        pystray.MenuItem("Reload config", reload_cfg),
        pystray.MenuItem("Open data folder", lambda icon, item: open_data_folder(bridge.config_path)),
        pystray.MenuItem("Start with Windows", toggle_autostart,
                         checked=lambda item: autostart_status()["enabled"],
                         enabled=lambda item: autostart_status()["available"]),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("cockpitlink", make_icon_image(), f"CockpitLink v{VERSION}", menu)
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

    ap = argparse.ArgumentParser(description="CockpitLink for MSFS")
    ap.add_argument("--config", default=None, help="config.json to use (default: in the data folder)")
    ap.add_argument("--dry-run", action="store_true", help="Do not call VPC_LED_Control, log only")
    ap.add_argument("--no-sim", action="store_true", help="Do not connect to MSFS, API only")
    ap.add_argument("--no-tray", action="store_true", help="Do not show a tray icon")
    ap.add_argument("--open", action="store_true", help="Open the control panel in the browser on start")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()
    copied_from = None
    if args.config is None:
        folder = data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            copied_from = copy_old_setup(folder)
        except OSError as e:
            copied_from = None
            print(f"Could not copy the setup into {folder}: {e}", file=sys.stderr)
        args.config = str(folder / "config.json")
        DATA_INFO["portable"] = is_portable()
    DATA_INFO["copied_from"] = str(copied_from) if copied_from else None

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

    if not Path(args.config).exists():
        # First start: make config.json from the devices that are plugged in (config.example.json has the rest).
        log.info("No %s yet – making one from the connected Virpil devices.", args.config)
        try:
            Path(args.config).write_text(dump_config(make_starter_config(args.config)), encoding="utf-8")
        except (OSError, ValueError) as e:
            log.error("Could not make %s: %s", args.config, e)
            return 1
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

    log.info("CockpitLink %s. Config: %s", VERSION, args.config)
    if copied_from:
        log.info("Your setup was copied from %s to %s (the old files are left there and are no longer used).",
                 copied_from, Path(args.config).parent)
    migrate_autostart()
    if api.get("udp_port"):
        threading.Thread(target=udp_server, args=(bridge, host, api["udp_port"]), daemon=True, name="udp").start()
    bridge.start()
    log.info("Control panel and API at %s", url)

    icon = None if args.no_tray else start_tray(bridge, url)
    if icon is not None:
        time.sleep(0.5)
        try:
            icon.notify("Running. Double-click the icon for the control panel.", "CockpitLink")
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
