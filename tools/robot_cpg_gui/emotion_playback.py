"""Face-exit personality playback: original SO(2) Stage 1, then distinct Stage 2.

The calibrated Servo mapping, motion limits, Emergency behavior and GPIO driver
remain external to this module. The FaceSearchController enforces speed limits
on all output trajectories and the hardware mapper clamps every raw command.
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from typing import Any

from .cpg_engine import LampController


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _smoothstep(progress: float) -> float:
    p = _clamp(progress, 0.0, 1.0)
    return p * p * (3.0 - 2.0 * p)


@dataclass(frozen=True)
class PlaybackFrame:
    q_deg: tuple[float, float, float, float]
    hormones: dict[str, float]
    emotion: str
    light: dict[str, Any]
    input_status: str = "post_face_personality"
    stage: str = "STAGE1"


class EmotionPlayback:
    """Replays the observed emotion, with a visually different second stage.

    Stage 1 follows the calibrated SO(2) CPG only. Stage 2 overlays an
    emotion-specific, multi-joint coordinated gesture after a short delay.
    Stage timing scales down for very brief observed expressions; the overall
    duration remains owned by FaceSearchController's observed-seconds timer.
    """

    def __init__(self, *, now: float, mode: str, profile: dict, dt: float,
                 dance_hz: float = 0.36, rainbow_cycle_s: float = 5.0,
                 duration_s: float | None = None):
        if mode not in ("Happy", "Sad", "Confused", "Neutral"):
            raise ValueError("unknown recorded emotion")
        if duration_s is not None and (not math.isfinite(duration_s) or duration_s <= 0):
            raise ValueError("playback duration must be positive and finite")
        self.mode = mode
        self.started = float(now)
        self.dance_hz = float(dance_hz)
        self.rainbow_cycle_s = float(rainbow_cycle_s)
        self.profile = profile
        # 1.2 seconds of recognizable original CPG, or 25% of a short clip.
        self.stage1_seconds = min(1.2, max(0.12, 0.25 * duration_s)) if duration_s is not None else 1.2
        self.stage2_ramp_seconds = 0.60
        self.cpg = LampController(start_time=now, profile=profile, dt=dt)
        self.cpg.begin_motion(now, override_mode=mode)

    def step(self, now: float) -> PlaybackFrame:
        t = max(0.0, float(now) - self.started)
        raw = self.cpg.step(now, override_mode=self.mode)
        angles = list(raw["q_deg"])
        base_light = dict(raw["light"])
        expressive = _smoothstep((t - self.stage1_seconds) / self.stage2_ramp_seconds)
        if expressive <= 0:
            return PlaybackFrame(tuple(angles), dict(raw["hormones"]), self.mode,
                                 base_light, stage="STAGE1")

        # Distinct full-body Stage 2 choreography; avoid exactly synchronized
        # joints so expressions do not resemble one stiff forward bow.
        if self.mode == "Happy":
            phase = 2.0 * math.pi * min(self.dance_hz, 0.32) * (t - self.stage1_seconds)
            offsets = (
                9.0 * math.sin(phase),
                11.0 * math.sin(phase + 0.9),
                -10.0 * math.sin(phase + 0.2),
                7.0 * math.sin(phase + 1.7),
            )
            rgb = colorsys.hsv_to_rgb((t / self.rainbow_cycle_s) % 1.0, 1.0, 1.0)
            light = {"color": "#" + "".join(f"{round(255*x):02x}" for x in rgb),
                     "brightness": 0.70, "pattern": "rainbow"}
        elif self.mode == "Confused":
            phase = 2.0 * math.pi * 0.19 * (t - self.stage1_seconds)
            offsets = (
                11.0 * math.sin(phase) + 3.0 * math.sin(2.0 * phase),
                -9.0 * math.sin(phase + math.pi / 3),
                10.0 * math.sin(phase + math.pi / 1.5),
                -8.0 * math.sin(phase + math.pi / 2),
            )
            light = {"color": "#b05cff" if math.sin(phase) >= 0 else "#18c5dc",
                     "brightness": 0.55}
        elif self.mode == "Sad":
            phase = 2.0 * math.pi * 0.13 * (t - self.stage1_seconds)
            offsets = (
                5.0 * math.sin(phase),
                -7.0 + 3.0 * math.sin(phase + 0.6),
                6.0 * math.sin(phase + math.pi),
                -5.0 * math.sin(phase + 0.7),
            )
            light = {"color": "#245ecb", "brightness": 0.12 + 0.16 * (1 + math.sin(phase)) / 2}
        else:  # Neutral Stage 2: deliberate slow, asymmetric full-body wave.
            phase = 2.0 * math.pi * 0.15 * (t - self.stage1_seconds)
            offsets = (
                7.0 * math.sin(phase),
                8.0 * math.sin(phase + math.pi / 2),
                -7.0 * math.sin(phase + math.pi / 4),
                6.0 * math.sin(phase + math.pi),
            )
            light = {"color": "#f0efff", "brightness": 0.30 + 0.12 * (1 + math.sin(phase)) / 2}
        for j, offset in enumerate(offsets):
            limit = self.profile["joint_limits"][j]
            angles[j] = _clamp(angles[j] + offset * expressive,
                               float(limit["min"]), float(limit["max"]))
        # Fade light into the second-stage expression without flashing on entry.
        light["brightness"] = (float(base_light.get("brightness", 0.0)) * (1 - expressive)
                               + float(light["brightness"]) * expressive)
        return PlaybackFrame(tuple(angles), dict(raw["hormones"]), self.mode,
                             light, stage="STAGE2")
