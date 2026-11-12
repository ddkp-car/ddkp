"""Optional expressive stage after sustained *camera-confirmed* hormone peak.

This is an overlay on the calibrated SO(2) CPG, never a motor driver.
It must not replace the original CPG formula, servo mapping, or safety gates.
J1/J4 expressive wiggles are fed separately into the gaze tracker so that
neither personality nor dancing steals control of the gaze axes.
"""
from __future__ import annotations

import colorsys
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_STAGE_PATH = Path(__file__).resolve().parent / "emotion_stage_config.json"
EMOTIONS = ("Happy", "Sad", "Confused", "Neutral")


@dataclass(frozen=True)
class StageSettings:
    enabled: bool = True
    hormone_peak_threshold: float = 0.90
    happy_peak_hold_s: float = 3.0
    other_peak_hold_s: float = 5.0
    stage_ramp_s: float = 1.0
    stage2_duration_s: float = 5.0
    camera_emotion_max_age_s: float = 0.30
    rgb_rainbow_cycle_s: float = 5.0
    happy_dance_hz: float = 0.36
    max_j1_gaze_wiggle_deg: float = 1.5
    max_j4_gaze_wiggle_deg: float = 1.5


def load_stage_settings(path: str | Path = DEFAULT_STAGE_PATH) -> StageSettings:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("emotion stage config must have schema_version=1")
    names = set(StageSettings.__dataclass_fields__)
    if set(raw) - (names | {"schema_version"}):
        raise ValueError("unknown emotion-stage config field")
    cfg = StageSettings(**{key: raw[key] for key in names if key in raw})
    if type(cfg.enabled) is not bool:
        raise ValueError("enabled must be boolean")
    for key in names - {"enabled"}:
        v = getattr(cfg, key)
        if type(v) not in (int, float) or not math.isfinite(v):
            raise ValueError(f"{key} must be a finite number")
    if not (0.5 <= cfg.hormone_peak_threshold < 1.0
            and 0.5 <= cfg.happy_peak_hold_s <= 60
            and 0.5 <= cfg.other_peak_hold_s <= 60
            and 0.2 <= cfg.stage_ramp_s <= 10
            and 0.5 <= cfg.stage2_duration_s <= 30
            and 0.05 <= cfg.camera_emotion_max_age_s <= 1.0
            and 1.0 <= cfg.rgb_rainbow_cycle_s <= 30
            and 0.05 <= cfg.happy_dance_hz <= 0.5
            and 0 <= cfg.max_j1_gaze_wiggle_deg <= 3.0
            and 0 <= cfg.max_j4_gaze_wiggle_deg <= 3.0):
        raise ValueError("emotion stage config outside safe ranges")
    return cfg


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class StageMotion:
    cpg_angles: tuple[float, float, float, float]
    gaze_wiggles: tuple[float, float]
    light: dict[str, Any] | None
    label: str
    peak_held_s: float


class EmotionStageController:
    """Advance on runtime monotonic time only while valid face and emotion remain.

    Peak is 90%, not exactly 1.0 (an Euler hormone approaches 1 asymptotically).
    A Numpad mode alone can *never* trigger a stage: real camera expression
    must match the selected emotion. Neutral uses residual hormone weight.
    """

    def __init__(self, settings: StageSettings | None = None,
                 joint_limits: list[dict[str, float]] | None = None,
                 minimum_expression_strength: float = 0.35) -> None:
        self.settings = settings or StageSettings()
        self.limits = joint_limits or [{"min": 60.0, "max": 120.0} for _ in range(4)]
        self.minimum_strength = float(minimum_expression_strength)
        self._hold_mode: str | None = None
        self._peak_since: float | None = None
        self._stage_since: float | None = None
        self._stage_finished = False
        self._last_time: float | None = None

    def reset(self) -> None:
        self._hold_mode = None
        self._peak_since = None
        self._stage_since = None
        self._stage_finished = False
        self._last_time = None

    def step(self, now: float, frame: Any, *, tracking: bool,
             camera_emotion: str | None, camera_strength: float = 0.0) -> StageMotion:
        now = float(now)
        angles = tuple(float(v) for v in frame.q_deg)
        light = dict(getattr(frame, "light", {}) or {"color": "#000000", "brightness": 0.0})
        if len(angles) != 4 or not all(math.isfinite(v) for v in angles):
            raise ValueError("invalid input pose for emotion stage")
        base = StageMotion(angles, (0.0, 0.0), light, "BASE", 0.0)
        selected = str(frame.emotion)
        raw = (camera_emotion or "").lower()
        matching = (raw == selected.lower() if selected != "Neutral"
                    else raw in ("neutral", "unknown"))
        valid = (self.settings.enabled and tracking and matching and selected in EMOTIONS
                 and (selected == "Neutral" or camera_strength >= self.minimum_strength)
                 and math.isfinite(camera_strength))
        if not valid or (self._last_time is not None and (now < self._last_time or now - self._last_time > 1)):
            self.reset()
            return base
        self._last_time = now
        if self._stage_finished:
            return StageMotion(angles, (0.0, 0.0), light, "WAIT_NEW_EMOTION", 0.0)
        hormones = frame.hormones
        concentration = (float(hormones.get(selected, 0.0)) if selected != "Neutral"
                         else clamp(1.0 - sum(float(hormones.get(k, 0.0)) for k in ("Happy", "Sad", "Confused")), 0.0, 1.0))
        if not math.isfinite(concentration) or concentration < self.settings.hormone_peak_threshold:
            self.reset()
            return base
        if selected != self._hold_mode:
            self._hold_mode = selected
            self._peak_since = now
            self._stage_since = None
        held = max(0.0, now - float(self._peak_since))
        required = (self.settings.happy_peak_hold_s if selected == "Happy"
                    else self.settings.other_peak_hold_s)
        if held < required:
            return StageMotion(angles, (0.0, 0.0), light, f"PEAK {selected}", held)
        if self._stage_since is None:
            self._stage_since = now
        t = now - self._stage_since
        if t >= self.settings.stage2_duration_s:
            self._stage_finished = True
            return StageMotion(angles, (0.0, 0.0), light, f"STAGE2_DONE {selected}", held)
        ramp = clamp(t / self.settings.stage_ramp_s, 0.0, 1.0)
        ramp = ramp * ramp * (3.0 - 2.0 * ramp)  # smoothstep
        j1 = j2 = j3 = j4 = 0.0
        if selected == "Happy":
            phase = 2.0 * math.pi * self.settings.happy_dance_hz * t
            # Four coordinated sways; gaze owns J1/J4, dance owns J2/J3.
            j1 = 1.5 * math.sin(phase)
            j2 = 6.0 * math.sin(phase)
            j3 = 6.0 * math.sin(phase + math.pi / 1.6)
            j4 = 1.5 * math.sin(phase + math.pi / 2)
            hue = (t / self.settings.rgb_rainbow_cycle_s) % 1.0
            channels = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            color = "#" + "".join(f"{round(c * 255):02x}" for c in channels)
            brightness = 0.45 + 0.40 * ramp
        elif selected == "Sad":
            phase = 2 * math.pi * 0.12 * t
            j1 = 0.6 * math.sin(phase)
            j2 = -5.0 + 1.0 * math.sin(phase)
            j3 = 3.0 + 2.0 * math.sin(phase + math.pi / 2)
            j4 = -0.6 * math.sin(phase)
            color = "#245ecb"
            brightness = 0.12 + 0.20 * (1.0 + math.sin(phase)) / 2.0
        elif selected == "Confused":
            phase = 2 * math.pi * 0.24 * t
            j1 = 1.2 * math.sin(phase)
            j2 = 5.0 * math.sin(phase)
            j3 = -4.0 * math.sin(phase + math.pi / 3)
            j4 = 1.2 * math.sin(phase + math.pi / 2)
            color = "#aa55ff" if math.sin(phase) >= 0 else "#20bcd5"
            brightness = 0.50
        else:  # Neutral: quiet breathing, not a dramatic dance.
            phase = 2 * math.pi * 0.12 * t
            j1 = 0.3 * math.sin(phase)
            j2 = 1.5 * math.sin(phase)
            j3 = -1.5 * math.sin(phase)
            j4 = 0.5 * math.sin(phase)
            color = "#f0efff"
            brightness = 0.22 + 0.10 * (1.0 + math.sin(phase)) / 2.0
        j1 = clamp(j1 * ramp, -self.settings.max_j1_gaze_wiggle_deg,
                   self.settings.max_j1_gaze_wiggle_deg)
        j4 = clamp(j4 * ramp, -self.settings.max_j4_gaze_wiggle_deg,
                   self.settings.max_j4_gaze_wiggle_deg)
        values = list(angles)
        for index, offset in ((1, j2 * ramp), (2, j3 * ramp)):
            limits = self.limits[index]
            values[index] = clamp(values[index] + offset, float(limits["min"]), float(limits["max"]))
        led_light = {"color": color, "brightness": clamp(brightness * ramp +
                     (1 - ramp) * float(light.get("brightness", 0.0)), 0.0, 1.0)}
        if selected == "Happy":
            # Addressable LED drivers render a spatial rainbow across multiple
            # pixels. Ordinary RGB PWM drivers ignore this extra metadata.
            led_light["pattern"] = "rainbow"
        return StageMotion(tuple(values), (j1, j4), led_light,
                           f"STAGE2 {selected}", held)
