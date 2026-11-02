"""Talking to the running Rainmeter skin.

Two ways to change a variable, and they are not interchangeable:

  bang("!SetVariable", ...)  takes effect instantly but lives only until the
                             next refresh -- right for dragging a slider
  write_vars({...})          edits AsciiClock.ini, so it survives a refresh,
                             a Rainmeter restart and a reboot -- right for
                             committing a setting

The GUI uses the first for live preview and the second when you hit Apply.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
SKIN_INI = ROOT / "rainmeter" / "AsciiClock" / "AsciiClock.ini"
CONFIG_NAME = "AsciiClock"

EXE_CANDIDATES = (
    Path(r"C:\Program Files\Rainmeter\Rainmeter.exe"),
    Path(r"C:\Program Files (x86)\Rainmeter\Rainmeter.exe"),
)

# Variables the GUI is allowed to drive, with their sane bounds.
TUNABLES = {
    "TrimOne": (0, 33, "Kerning claw-back on the digit 1 (px per side)"),
    "Tracking": (-20, 40, "Constant gap between digit cells (px)"),
    "SepWidth": (0, 120, "Gap standing in for the colon (px)"),
    "Scale": (1, 4, "Uniform scale of the whole clock"),
    "DateSize": (8, 40, "Date line font size (pt)"),
    "DateGap": (0, 60, "Space between digits and date (px)"),
    # Lower bound is 2, not 1: the skin's frame formula divides by MorphSecs-1.
    "MorphSecs": (2, 60, "Sweep runs over the last N seconds of the minute"),
    "DebugSecPerMin": (2, 60, "Real seconds per simulated minute"),
}

# Which tunables the GUI lays out somewhere other than the spacing block.
ELSEWHERE = ("MorphSecs", "DebugSecPerMin")

# Tick intervals in ms the GUI will offer. Restricted to exact divisors of
# 1000: the sweep counts ticks within a wall-clock second, so a rate that does
# not divide evenly would leave a short tick at every second boundary.
RATES = (1000, 500, 250, 200, 125, 100)

# Variables the GUI drives with something other than a slider.
CHOICES = {
    "MorphCell": (("8", "4"), "Sweep step: 8px block or 4px character"),
    "Morph": (("1", "0"), "Sweep each digit as it changes"),
}


def rainmeter_exe():
    for p in EXE_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Rainmeter.exe not found in " + " or ".join(str(p) for p in EXE_CANDIDATES)
    )


def bang(*args):
    """Fire a Rainmeter bang at the skin. Runtime only -- lost on refresh."""
    cmd = [str(rainmeter_exe()), *args]
    if args and args[0] not in ("!Refresh", "!RefreshApp"):
        cmd.append(CONFIG_NAME)
    return subprocess.run(cmd, capture_output=True, text=True).returncode == 0


def refresh():
    return subprocess.run(
        [str(rainmeter_exe()), "!Refresh", CONFIG_NAME], capture_output=True
    ).returncode == 0


def set_var(name, value, live=True):
    """Push a variable to the running skin without touching the file."""
    return bang("!SetVariable", name, str(value)) and (not live or bang("!Redraw"))


def read_vars(section="Variables"):
    """Current values from one section of the skin file."""
    want = f"[{section.lower()}]"
    out, in_section = {}, False
    for line in SKIN_INI.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_section = s.lower() == want
            continue
        if not in_section or not s or s.startswith(";"):
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def write_vars(values, section="Variables"):
    """Persist settings into the skin file, leaving comments and order alone."""
    want = f"[{section.lower()}]"
    text = SKIN_INI.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_section = False
    remaining = dict(values)

    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_section = s.lower() == want
            continue
        if not in_section or "=" not in s or s.startswith(";"):
            continue
        key = s.split("=", 1)[0].strip()
        if key in remaining:
            eol = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f"{key}={remaining.pop(key)}{eol}"

    if remaining:
        raise KeyError(
            f"not found in [{section}]: " + ", ".join(sorted(remaining))
        )
    SKIN_INI.write_text("".join(lines), encoding="utf-8")
    return True


def read_update_ms():
    """The skin's tick interval in ms."""
    return int(read_vars("Rainmeter")["Update"])


def write_update_ms(ms):
    """Set the tick interval, keeping MorphTPS in step with it.

    The sweep counts ticks within a second to get sub-second resolution, so it
    needs to know how many there are -- and Rainmeter will not evaluate a
    formula in [Rainmeter]Update, so the number cannot simply be derived there.
    Writing both from one place is what keeps them honest.

    There is no live equivalent: !SetVariable cannot reach [Rainmeter], so the
    caller has to refresh.
    """
    ms = int(ms)
    assert ms in RATES, f"rate {ms} is not one of {RATES}"
    write_vars({"Update": ms}, section="Rainmeter")
    write_vars({"MorphTPS": 1000 // ms})
    return 1000 // ms


def window_rect():
    """Rect of the skin window, or None if it is not loaded.

    Units follow the *calling* process's DPI awareness: an unaware process
    (the normal case here, and what tkinter wants) gets logical pixels, so at
    175% scaling a 630x140 window reads as 360x80. Deliberately not forcing
    awareness -- that is process-wide and would shrink the GUI's own widgets.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value == "RainmeterMeterWindow":
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            found.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else None


def is_running():
    try:
        rainmeter_exe()
    except FileNotFoundError:
        return False
    return window_rect() is not None
