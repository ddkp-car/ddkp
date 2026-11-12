"""Hardware-independent, local 4-joint SO(2)/hormone robotic-lamp controller."""
from __future__ import annotations

import math
from typing import Any

from .cpg_profile import EMOTIONS, MODES, pasted_defaults, validate_profile


def lerp_keyframes(rows: list[dict], seconds: float, field: str) -> Any:
    """Piecewise linear interpolation; clamp before first/after last keyframe."""
    if seconds <= rows[0]["time"]:
        return rows[0][field]
    for a, b in zip(rows, rows[1:]):
        if seconds <= b["time"]:
            blend = (seconds - a["time"]) / (b["time"] - a["time"])
            if field == "angles":
                return [float(v) + blend * (float(w) - float(v)) for v, w in zip(a[field], b[field])]
            return float(a[field]) + blend * (float(b[field]) - float(a[field]))
    return rows[-1][field]


def mix_hex(colors: list[tuple[str, float]]) -> str:
    rgb = [0., 0., 0.]
    for hex_color, weight in colors:
        for channel in range(3):
            rgb[channel] += int(hex_color[1 + channel * 2:3 + channel * 2], 16) * weight
    return "#" + "".join(f"{max(0, min(255, round(value))):02x}" for value in rgb)


def so2_step(o1: float, o2: float, alpha: float, phi: float) -> tuple[float, float]:
    """The SO(2) recurrence supplied by the user.

    Both new activations must be computed from the *same previous* (o1, o2):
        a1 = alpha * (cos(phi) * o1 + sin(phi) * o2)
        a2 = alpha * (-sin(phi) * o1 + cos(phi) * o2)
        o1', o2' = tanh(a1), tanh(a2)
    """
    cosine, sine = math.cos(phi), math.sin(phi)
    a1 = alpha * (cosine * o1 + sine * o2)
    a2 = alpha * (-sine * o1 + cosine * o2)
    return math.tanh(a1), math.tanh(a2)


class ExpressionGate:
    """Debounced camera emotion with timeout, stream/sequence guards."""
    def __init__(self, config: dict[str, Any]):
        self.minimum = float(config["min_strength"])
        self.stable_s = float(config["stable_s"])
        self.timeout_s = float(config["timeout_s"])
        self.samples_required = int(config["min_samples"])
        self.current = "Neutral"
        self.status = "not_started"
        self._candidate: str | None = None
        self._candidate_since: float | None = None
        self._samples = 0
        self._last_observation: float | None = None
        self._stream: str | None = None
        self._sequence: int | None = None

    def invalidate(self, reason: str) -> None:
        self.current = "Neutral"
        self.status = reason
        self._candidate = None
        self._candidate_since = None
        self._samples = 0
        self._last_observation = None

    def observe(self, value: dict, timestamp: float) -> bool:
        if not isinstance(value, dict) or value.get("schema") != 1:
            self.invalidate("invalid_observation")
            return False
        stream = value.get("stream_id")
        sequence = value.get("seq")
        if not isinstance(stream, str) or not stream or type(sequence) is not int or sequence < 0:
            self.invalidate("invalid_observation")
            return False
        if self._stream == stream and self._sequence is not None and sequence <= self._sequence:
            self.invalidate("out_of_order")
            return False
        if stream != self._stream:
            self._candidate = None
            self._candidate_since = None
            self._samples = 0
            self.current = "Neutral"
        self._stream, self._sequence = stream, sequence
        detected = value.get("detected") is True
        emotion_value = str(value.get("emotion", "")).lower().strip()
        emotion = {"happy": "Happy", "sad": "Sad", "confused": "Confused"}.get(emotion_value)
        strength = value.get("strength", 0.0)
        if not isinstance(strength, (float, int)) or isinstance(strength, bool) or not math.isfinite(strength):
            self.invalidate("invalid_observation")
            return False
        strength = float(strength)
        if not 0 <= strength <= 1:
            self.invalidate("invalid_observation")
            return False
        self._last_observation = timestamp
        if not detected or emotion is None or strength < self.minimum:
            self._candidate = None
            self._candidate_since = None
            self._samples = 0
            self.current = "Neutral"
            self.status = "no_face" if not detected else "below_strength"
            return True
        if emotion != self._candidate:
            self._candidate = emotion
            self._candidate_since = timestamp
            self._samples = 1
        else:
            self._samples += 1
        if (self._samples >= self.samples_required
                and timestamp - float(self._candidate_since) >= self.stable_s):
            self.current = emotion
            self.status = "accepted"
        else:
            self.status = "stabilizing"
        return True

    def tick(self, now: float) -> None:
        if self._last_observation is not None and now - self._last_observation > self.timeout_s:
            self.invalidate("input_timeout")


class LampController:
    """Compute joint commands, hormone memory and light output at the control-loop DT.

    CPG uses a bounded recurrent SO(2) oscillator, with phase increment phi
    (radians/tick), recurrence gain alpha and per-joint angular amplitudes.
    The camera/perception implementation is intentionally not imported here.
    """

    def __init__(self, *, start_time: float, profile: dict | None = None, dt: float = 0.05):
        self.dt = float(dt)
        self.profile = validate_profile(profile if profile is not None else pasted_defaults(), dt=self.dt)
        self.gate = ExpressionGate(self.profile["input_gate"])
        self._last_time = float(start_time)
        self._mode = "Neutral"
        # Track each mode's onset separately. A fading hormone retains its
        # own pose/light timeline while another mode becomes active.
        self._mode_onset: dict[str, float | None] = {name: None for name in MODES}
        self._mode_onset["Neutral"] = float(start_time)
        self._head_start = 90.0
        self._last_command = [90.0] * 4
        self._hormones = {name: 0.0 for name in EMOTIONS}
        # Same initial oscillator state as the supplied simulation.
        self._osc = [0.10, 0.00]
        self._active_since = float(start_time)
        self._scheduler_fault = False
        for _ in range(self.profile["global"]["warmup_steps"]):
            self._so2_step(self.profile["modes"]["Neutral"]["phi"])

    def begin_motion(self, now: float, *, override_mode: str | None = None) -> None:
        """Reset output ramp at physical START so idle ticks cannot preload a jump."""
        if override_mode is not None and override_mode not in MODES:
            raise ValueError(f"Unknown manual personality: {override_mode}")
        self._last_command = [90.0] * 4
        self._last_time = float(now)
        self._active_since = float(now)
        self._mode = override_mode if override_mode is not None else self.gate.current
        self._mode_onset = {name: None for name in MODES}
        self._mode_onset[self._mode] = float(now)
        self._head_start = 90.0

    def observe(self, observation: dict, received_at: float) -> bool:
        timestamp = float(received_at)
        if not math.isfinite(timestamp) or timestamp < 0:
            self.gate.invalidate("invalid_time")
            return False
        return self.gate.observe(observation, timestamp)

    def _so2_step(self, phi: float) -> tuple[float, float]:
        """Apply the exact supplied SO(2) sign convention to both old outputs."""
        alpha = float(self.profile["global"]["so2_alpha"])
        o1, o2 = self._osc
        self._osc = list(so2_step(o1, o2, alpha, phi))
        return self._osc[0], self._osc[1]

    def step(self, timestamp: float, *, override_mode: str | None = None) -> dict[str, Any]:
        """Compute CPG; a valid manual override bypasses only camera emotion selection."""
        if override_mode is not None and override_mode not in MODES:
            raise ValueError(f"Unknown manual personality: {override_mode}")
        now = float(timestamp)
        if not math.isfinite(now) or now < 0:
            raise ValueError("invalid controller timestamp")
        elapsed = now - self._last_time
        if elapsed < -1e-6:
            self._scheduler_fault = True
            elapsed = 0.0
        else:
            self._scheduler_fault = elapsed > 1.0
        # Never let a delayed GUI/runtime tick produce a large jump in target.
        dt = min(self.dt, max(0., elapsed))
        self._last_time = max(self._last_time, now)
        self.gate.tick(now)
        selected = override_mode if override_mode is not None else self.gate.current
        if selected != self._mode:
            self._mode = selected
            self._mode_onset[selected] = now
            if selected == "Sad":
                self._head_start = self._last_command[3]
        for emotion in EMOTIONS:
            target = 1.0 if emotion == selected else 0.0
            tau_field = "tau_up" if target > self._hormones[emotion] else "tau_down"
            tau = float(self.profile["hormones"][emotion][tau_field])
            self._hormones[emotion] = max(0., min(1., self._hormones[emotion] + (target - self._hormones[emotion]) * dt / tau))

        # Neutral makes up the residual weight. When traces sum above one,
        # normalize them so that mixed joint targets are always bounded.
        active = sum(self._hormones.values())
        denom = max(1., active)
        weights = {name: self._hormones[name] / denom for name in EMOTIONS}
        weights["Neutral"] = max(0., 1. - sum(weights.values()))
        phase = sum(weights[name] * self.profile["modes"][name]["phi"] for name in MODES)
        o1, o2 = self._so2_step(phase)
        blend_s = float(self.profile["global"]["oscillation_blend_s"])
        oscillation_blend = (1. if blend_s <= 0 else min(1., max(0., (now - self._active_since) / blend_s)))
        centers = [0.] * 4
        gains = [0.] * 4
        brightness = 0.
        colors = []
        for mode in MODES:
            weight = weights[mode]
            m = self.profile["modes"][mode]
            onset = self._mode_onset[mode]
            mode_t = max(0.0, now - onset) if onset is not None else 0.0
            pose = list(lerp_keyframes(m["poses"], mode_t, "angles"))
            if mode == "Sad" and selected == "Sad":
                # Start from actual J4 position; follow later edited keyframes.
                if len(m["poses"]) > 1 and mode_t < m["poses"][1]["time"]:
                    next_frame = m["poses"][1]
                    fraction = mode_t / next_frame["time"]
                    pose[3] = self._head_start + fraction * (float(next_frame["angles"][3]) - self._head_start)
                elif len(m["poses"]) == 1:
                    pose[3] = self._head_start
            for j in range(4):
                centers[j] += weight * pose[j]
                gains[j] += weight * m["gains"][j]
            brightness += weight * float(lerp_keyframes(m["brightness"], mode_t, "value"))
            colors.append((m["light_color"], weight))
        maximum_delta = float(self.profile["global"]["max_slew_deg_s"]) * dt
        next_q = []
        # Exact supplied decoder: J1:+o1, J2:+o2, J3:-o1, J4:+o2.
        # Fade the *whole* J4 oscillation after hormone gain mixing, as in
        # the supplied decode_angles(head_fade=...) formula.
        head_blend = oscillation_blend
        fade = self.profile["modes"][selected]["head_fade"]
        if fade is not None:
            mode_started = self._mode_onset[selected]
            mode_elapsed = max(0.0, now - mode_started) if mode_started is not None else 0.0
            progress = max(0., min(1., (mode_elapsed - fade["start"]) / (fade["end"] - fade["start"])))
            head_blend *= 1. - progress
        oscillator_components = (o1, o2, -o1, o2)
        oscillator_blends = (oscillation_blend, oscillation_blend, oscillation_blend, head_blend)
        for j, limit in enumerate(self.profile["joint_limits"]):
            target = centers[j] + oscillator_blends[j] * gains[j] * oscillator_components[j]
            target = max(limit["min"], min(limit["max"], target))
            previous = self._last_command[j]
            command = max(previous - maximum_delta, min(previous + maximum_delta, target))
            next_q.append(max(limit["min"], min(limit["max"], command)))
        self._last_command = next_q
        return {
            "q_deg": tuple(next_q),
            "hormones": dict(self._hormones),
            "mode": selected,
            "input_status": ("manual_numpad" if override_mode is not None else self.gate.status),
            "light": {"color": mix_hex(colors), "brightness": max(0., min(1., brightness))},
            "scheduler_fault": self._scheduler_fault,
        }
