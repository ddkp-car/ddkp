"""Validated, versioned profile for the local robotic-lamp CPG.

This module is deliberately independent of Tk, camera and Servo hardware.
"""
from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MODES = ("Neutral", "Happy", "Sad", "Confused")
EMOTIONS = MODES[1:]
DEFAULT_PROFILE_PATH = Path(__file__).resolve().with_name("cpg_profile.json")


def pasted_defaults() -> dict[str, Any]:
    """Canonical defaults recorded in local-editable-cpg-design.md."""
    return {
        "schema_version": SCHEMA_VERSION,
        "global": {
            "so2_alpha": 1.10,
            "warmup_steps": 200,
            "oscillation_blend_s": 1.0,
            "max_slew_deg_s": 20.0,
        },
        "input_gate": {
            "min_strength": 0.35,
            "stable_s": 0.30,
            "timeout_s": 0.50,
            "min_samples": 3,
        },
        "joint_limits": [
            {"min": 60.0, "max": 120.0} for _ in range(4)
        ],
        "hormones": {
            "Happy": {"tau_up": 0.5, "tau_down": 1.5},
            "Sad": {"tau_up": 2.0, "tau_down": 3.0},
            "Confused": {"tau_up": 0.4, "tau_down": 1.0},
        },
        "modes": {
            "Neutral": {
                "phi": 0.20,
                "gains": [2.0, 2.0, 2.0, 2.0],
                "poses": [{"time": t, "angles": [80, 90, 60, 90]} for t in (0, 1, 5)],
                "light_color": "#ffffff",
                "brightness": [{"time": t, "value": .6} for t in (0, 1, 5, 10)],
                "head_fade": None,
            },
            "Happy": {
                "phi": .35,
                "gains": [6.0, 8.0, 6.0, 8.0],
                "poses": [
                    {"time": 0, "angles": [80, 90, 70, 90]},
                    {"time": 1, "angles": [80, 95, 73, 98]},
                    {"time": 5, "angles": [80, 100, 75, 105]},
                ],
                "light_color": "#ffd43b",
                "brightness": [{"time": t, "value": v} for t, v in ((0, .6), (1, .8), (5, 1), (10, 1))],
                "head_fade": None,
            },
            "Sad": {
                "phi": .12,
                "gains": [2.0, 2.0, 2.0, 8.0],
                "poses": [
                    {"time": 0, "angles": [80, 90, 70, 105]},
                    {"time": 1, "angles": [80, 85, 65, 100]},
                    {"time": 5, "angles": [80, 80, 60, 85]},
                    {"time": 10, "angles": [80, 80, 60, 60]},
                ],
                "light_color": "#377dff",
                "brightness": [{"time": t, "value": v} for t, v in ((0, .6), (1, .45), (5, .25), (10, .1))],
                "head_fade": {"start": 5.0, "end": 10.0},
            },
            "Confused": {
                "phi": .25,
                "gains": [20.0, 2.0, 3.0, 5.0],
                "poses": [
                    {"time": 0, "angles": [80, 90, 70, 90]},
                    {"time": 1, "angles": [85, 90, 68, 85]},
                    {"time": 5, "angles": [120, 90, 65, 80]},
                ],
                "light_color": "#b05cff",
                "brightness": [{"time": t, "value": v} for t, v in ((0, .6), (1, .75), (5, .45), (10, .65))],
                "head_fade": None,
            },
        },
    }


def _object(value: Any, path: str, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    if set(value) != expected:
        raise ValueError(f"{path}: keys must be {', '.join(sorted(expected))}")
    return value


def _number(value: Any, path: str, lo: float, hi: float, *, inclusive_hi: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{path}: expected numeric value")
    number = float(value)
    if not math.isfinite(number) or number < lo or (number > hi if inclusive_hi else number >= hi):
        raise ValueError(f"{path}: expected finite value in {lo}..{hi}")
    return number


def _integer(value: Any, path: str, lo: int, hi: int) -> int:
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"{path}: expected integer {lo}..{hi}")
    return value


def _keyframes(value: Any, path: str, *, poses: bool, limits: list[tuple[float, float]]) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path}: at least one keyframe required")
    last_time = -1.0
    for i, row in enumerate(value):
        item = _object(row, f"{path}[{i}]", {"time", "angles"} if poses else {"time", "value"})
        t = _number(item["time"], f"{path}[{i}].time", 0.0, 60.0)
        if (i == 0 and t != 0) or t <= last_time:
            raise ValueError(f"{path}: first time must be 0; later times must increase")
        last_time = t
        if poses:
            angles = item["angles"]
            if not isinstance(angles, list) or len(angles) != 4:
                raise ValueError(f"{path}[{i}].angles: exactly J1-J4 required")
            for j, angle in enumerate(angles):
                low, high = limits[j]
                _number(angle, f"{path}[{i}].J{j + 1}", low, high)
        else:
            _number(item["value"], f"{path}[{i}].value", 0., 1.)
    return value


def validate_profile(profile: Any, *, dt: float = 0.05) -> dict[str, Any]:
    """Return a deep-copied, fully validated profile; raise ValueError otherwise."""
    if not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt <= 0 or dt > 1:
        raise ValueError("control loop DT must be finite in (0,1]")
    p = _object(profile, "profile", {"schema_version", "global", "input_gate", "joint_limits", "hormones", "modes"})
    if type(p["schema_version"]) is not int or p["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"Unsupported profile schema_version: {p['schema_version']!r}")
    g = _object(p["global"], "global", {"so2_alpha", "warmup_steps", "oscillation_blend_s", "max_slew_deg_s"})
    _number(g["so2_alpha"], "global.so2_alpha", .01, 2.)
    _integer(g["warmup_steps"], "global.warmup_steps", 0, 5000)
    _number(g["oscillation_blend_s"], "global.oscillation_blend_s", 0, 10)
    _number(g["max_slew_deg_s"], "global.max_slew_deg_s", .1, 20)
    gate = _object(p["input_gate"], "input_gate", {"min_strength", "stable_s", "timeout_s", "min_samples"})
    _number(gate["min_strength"], "input_gate.min_strength", 0, 1)
    stable = _number(gate["stable_s"], "input_gate.stable_s", 0, 10)
    timeout = _number(gate["timeout_s"], "input_gate.timeout_s", 0, 10)
    if timeout <= stable:
        raise ValueError("input_gate.timeout_s must exceed stable_s")
    _integer(gate["min_samples"], "input_gate.min_samples", 1, 100)
    limits_raw = p["joint_limits"]
    if not isinstance(limits_raw, list) or len(limits_raw) != 4:
        raise ValueError("joint_limits must contain exactly four joint ranges")
    limits = []
    for j, limit in enumerate(limits_raw, start=1):
        item = _object(limit, f"joint_limits.J{j}", {"min", "max"})
        low = _number(item["min"], f"joint_limits.J{j}.min", 60., 90., inclusive_hi=False)
        high = _number(item["max"], f"joint_limits.J{j}.max", 90., 120.)
        if high <= 90:
            raise ValueError(f"joint_limits.J{j}.max must exceed 90")
        limits.append((low, high))
    hormones = _object(p["hormones"], "hormones", set(EMOTIONS))
    for emotion, value in hormones.items():
        h = _object(value, f"hormones.{emotion}", {"tau_up", "tau_down"})
        for field in ("tau_up", "tau_down"):
            _number(h[field], f"hormones.{emotion}.{field}", dt, 60)
    modes = _object(p["modes"], "modes", set(MODES))
    for mode, value in modes.items():
        m = _object(value, f"modes.{mode}", {"phi", "gains", "poses", "light_color", "brightness", "head_fade"})
        _number(m["phi"], f"modes.{mode}.phi", -math.pi, math.pi)
        if not isinstance(m["gains"], list) or len(m["gains"]) != 4:
            raise ValueError(f"modes.{mode}.gains must contain exactly four numbers")
        for j, gain in enumerate(m["gains"], 1):
            _number(gain, f"modes.{mode}.gains.J{j}", 0, 30)
        _keyframes(m["poses"], f"modes.{mode}.poses", poses=True, limits=limits)
        _keyframes(m["brightness"], f"modes.{mode}.brightness", poses=False, limits=limits)
        color = m["light_color"]
        if not isinstance(color, str) or len(color) != 7 or color[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in color[1:]):
            raise ValueError(f"modes.{mode}.light_color: expected #RRGGBB")
        fade = m["head_fade"]
        if fade is not None:
            fade = _object(fade, f"modes.{mode}.head_fade", {"start", "end"})
            start = _number(fade["start"], f"modes.{mode}.head_fade.start", 0, 60)
            end = _number(fade["end"], f"modes.{mode}.head_fade.end", 0, 60)
            if end <= start:
                raise ValueError(f"modes.{mode}.head_fade.end must exceed start")
    return copy.deepcopy(p)


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH, *, dt: float = .05) -> dict[str, Any]:
    path = Path(path)
    try:
        return validate_profile(json.loads(path.read_text(encoding="utf-8")), dt=dt)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"Cannot load CPG profile {path}: {exc}") from exc


def save_profile_atomic(profile: dict[str, Any], path: str | Path = DEFAULT_PROFILE_PATH, *, dt: float = .05) -> None:
    """Validate before writing, then replace in same directory (no partial JSON)."""
    clean = validate_profile(profile, dt=dt)
    path = Path(path)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(clean, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
