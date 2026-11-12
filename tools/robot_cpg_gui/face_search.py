"""Safe camera-driven face search / tracking, independent of Servo bus and GUI.

The camera gives normalized face coordinates. All positions here are commanded
*nominal CPG degrees*, subsequently restricted again by HardwareMapper.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RuntimeConfig

DEFAULT_FACE_SEARCH_PATH = Path(__file__).resolve().parent / "face_search_config.json"


@dataclass(frozen=True)
class FaceSearchSettings:
    standby_seconds: float = 20.0
    lost_grace_seconds: float = 1.0
    camera_timeout_seconds: float = 1.0
    face_confirm_frames: int = 3
    scan_speed_deg_s: float = 12.0
    scan_endpoint_timeout_seconds: float = 8.0
    scan_endpoint_tolerance_deg: float = 2.5
    track_deadband_x: float = 0.07
    track_gain_deg_s: float = 38.0
    track_max_speed_deg_s: float = 17.0
    track_invert_x: bool = False
    track_deadband_y: float = 0.07
    track_gain_y_deg_s: float = 24.0
    track_max_y_speed_deg_s: float = 12.0
    track_observation_max_age_s: float = 0.30
    track_invert_y: bool = False
    # Vertical camera correction is shared by J2, J3 and J4.  Directions
    # depend on the physical linkage and can be inverted individually.
    track_vertical_mix: tuple[float, float, float] = (0.30, 0.30, 0.40)
    track_vertical_invert: tuple[bool, bool, bool] = (False, True, False)
    playback_min_seconds: float = 0.5
    playback_max_seconds: float = 30.0
    track_personality_head_fraction: float = 0.08
    track_personality_head_max_deg: float = 1.2
    pose_max_speed_deg_s: float = 20.0
    standby_offsets_deg: tuple[float, float, float] = (20.0, -45.0, 30.0)
    scan_min_deg: float = 60.0
    scan_max_deg: float = 120.0
    scan_use_full_safe_range: bool = True
    scan_target_span_deg: float = 180.0


def load_face_search_settings(path: str | Path = DEFAULT_FACE_SEARCH_PATH) -> FaceSearchSettings:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("face_search_config.json: expected schema_version=1")
    keys = {name for name in FaceSearchSettings.__dataclass_fields__}
    if set(payload) - ({"schema_version"} | keys):
        raise ValueError("face search config contains unknown fields")
    data = {key: payload[key] for key in keys if key in payload}
    for field in ("track_vertical_mix", "track_vertical_invert"):
        if field in data:
            values = data[field]
            if not isinstance(values, list) or len(values) != 3:
                raise ValueError(f"{field} must have three entries for J2 J3 J4")
            data[field] = tuple(values)
    if "track_vertical_invert" in data and any(type(v) is not bool for v in data["track_vertical_invert"]):
        raise ValueError("track_vertical_invert must contain booleans")
    if "track_vertical_mix" in data:
        weights = data["track_vertical_mix"]
        if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 or x > 1 for x in weights):
            raise ValueError("track_vertical_mix weights must be finite numbers in [0,1]")
        if abs(sum(weights) - 1.0) > 1e-5:
            raise ValueError("track_vertical_mix weights must sum to 1.0")
    if "standby_offsets_deg" in data:
        offsets = data["standby_offsets_deg"]
        if not isinstance(offsets, list) or len(offsets) != 3:
            raise ValueError("standby_offsets_deg must be [J2,J3,J4] relative to 90 degrees")
        data["standby_offsets_deg"] = tuple(float(x) for x in offsets)
    for key in ("track_invert_x", "track_invert_y", "scan_use_full_safe_range"):
        if key in data and not isinstance(data[key], bool):
            raise ValueError(f"{key} must be true or false")
    settings = FaceSearchSettings(**data)
    for name, value in vars(settings).items():
        if isinstance(value, bool):
            continue
        if isinstance(value, tuple):
            if not all(math.isfinite(v) for v in value):
                raise ValueError(f"{name} must be finite")
        elif not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if not (0 < settings.standby_seconds <= 3600 and
            0 <= settings.lost_grace_seconds <= 10 and
            0.2 <= settings.camera_timeout_seconds <= 10 and
            isinstance(settings.face_confirm_frames, int) and 1 <= settings.face_confirm_frames <= 20 and
            0 < settings.scan_speed_deg_s <= 20 and
            1 <= settings.scan_endpoint_timeout_seconds <= 30 and
            0.1 <= settings.scan_endpoint_tolerance_deg <= 10 and
            0 <= settings.track_deadband_x <= 0.3 and
            0 < settings.track_gain_deg_s <= 120 and
            0 < settings.track_max_speed_deg_s <= 20 and
            0 <= settings.track_deadband_y <= 0.3 and
            0 < settings.track_gain_y_deg_s <= 120 and
            0 < settings.track_max_y_speed_deg_s <= 20 and
            0.1 <= settings.track_observation_max_age_s <= 1.0 and
            0 <= settings.track_personality_head_fraction <= 0.2 and
            0 <= settings.track_personality_head_max_deg <= 3.0 and
            0 < settings.pose_max_speed_deg_s <= 20 and
            0.2 <= settings.playback_min_seconds <= 5.0 and
            settings.playback_min_seconds <= settings.playback_max_seconds <= 120.0 and
            0 <= settings.scan_min_deg < settings.scan_max_deg <= 180 and
            5 <= settings.scan_target_span_deg <= 180):
        raise ValueError("face search settings outside validated ranges")
    return settings


def joint_reachable_limits(config: RuntimeConfig, joint_limits: list[dict[str, float]] | None = None, *,
                           extended_j1: bool = False,
                           extended_j3: bool = False) -> tuple[tuple[float, float], ...]:
    """Reachable nominal degrees from the calibrated raw safety envelope.

    During camera pan, J1 can use its saved raw safe envelope.
    The camera standby J3 helper can independently read its raw safe envelope
    to reach a requested lowered pose. The caller restores ordinary CPG
    limits for normal expression control; hardware safe_min/max always apply.
    """
    result = []
    for j, servo_id in enumerate(sorted(config.servos)):
        servo = config.servos[servo_id]
        if (j == 0 and extended_j1) or (j == 2 and extended_j3):
            raw_low, raw_high = servo.safe_min, servo.safe_max
        else:
            raw_low = max(servo.safe_min, servo.center - config.max_offset_raw)
            raw_high = min(servo.safe_max, servo.center + config.max_offset_raw)
        scale = config.nominal_degrees_per_count / config.cpg_hardware_scale
        a = config.cpg_center_deg + servo.direction * (raw_low - servo.center) * scale
        b = config.cpg_center_deg + servo.direction * (raw_high - servo.center) * scale
        lo, hi = min(a, b), max(a, b)
        if joint_limits is not None and not ((j == 0 and extended_j1) or (j == 2 and extended_j3)):
            lo = max(lo, float(joint_limits[j]["min"]))
            hi = min(hi, float(joint_limits[j]["max"]))
        if lo >= hi:
            raise ValueError(f"J{j + 1} has no reachable range")
        result.append((lo, hi))
    return tuple(result)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def approach(current: float, target: float, step: float) -> float:
    return clamp(target, current - step, current + step)


@dataclass(frozen=True)
class FaceMotion:
    angles: tuple[float, float, float, float]
    behavior: str
    face_x: float | None
    face_y: float | None = None
    just_acquired: bool = False
    camera_fault: str = ""
    lost_emotion: str | None = None
    lost_emotion_seconds: float | None = None


class FaceSearchController:
    """Camera search state machine; no direct motion/torque permissions.

    SEARCHING: J1 travels to left limit, then right limit, once.
    STANDBY: standby pose for 20 s, then repeat.
    TRACKING: J1 pans to keep face_x near 0.5; J2/J3/J4 share vertical
    gaze correction to keep face_y near 0.5. Personality motion begins only
    after the tracked face leaves the camera.
    Face acquisition occurs regardless of expression strength.
    """

    def __init__(self, config: RuntimeConfig, mailbox: Any,
                 settings: FaceSearchSettings | None = None,
                 joint_limits: list[dict[str, float]] | None = None) -> None:
        self.config = config
        self.mailbox = mailbox
        self.settings = settings or FaceSearchSettings()
        normal_limits = list(joint_reachable_limits(
            config, joint_limits, extended_j1=self.settings.scan_use_full_safe_range
        ))
        # J3 standby is -45 degrees relative to nominal center 90: target 45 degrees.
        # Keep the already-tested optional safe-envelope extension for legacy
        # user profiles requesting deeper poses, without changing CPG limits.
        safe_j3 = joint_reachable_limits(config, extended_j3=True)[2]
        requested_j3 = config.cpg_center_deg + self.settings.standby_offsets_deg[1]
        standby_j3 = clamp(requested_j3, *safe_j3)
        j3_low, j3_high = normal_limits[2]
        normal_limits[2] = (min(j3_low, standby_j3), j3_high)
        self.limits = tuple(normal_limits)
        if self.settings.scan_use_full_safe_range:
            physical_low, physical_high = self.limits[0]
            span = min(self.settings.scan_target_span_deg, physical_high - physical_low)
            # Prefer the center of the safe reachable envelope for a bounded scan.
            middle = (physical_low + physical_high) * 0.5
            self._scan_min = middle - span / 2
            self._scan_max = middle + span / 2
        else:
            self._scan_min = max(self.settings.scan_min_deg, self.limits[0][0])
            self._scan_max = min(self.settings.scan_max_deg, self.limits[0][1])
        if self._scan_max - self._scan_min < 5.0:
            raise ValueError("J1 scan range under 5 degrees after safe limits")
        self.standby_pose = (
            clamp(config.cpg_center_deg, *self.limits[0]),
            clamp(config.cpg_center_deg + self.settings.standby_offsets_deg[0], *self.limits[1]),
            clamp(config.cpg_center_deg + self.settings.standby_offsets_deg[1], *self.limits[2]),
            clamp(config.cpg_center_deg + self.settings.standby_offsets_deg[2], *self.limits[3]),
        )
        self._last_sequence = 0
        self._camera_seen_at: float | None = None
        self._last_face_at: float | None = None
        self._face_x: float | None = None
        self._face_y: float | None = None
        self._observed_emotion: str | None = None
        self._observed_strength: float = 0.0
        self._observation_at: float | None = None
        self._gaze_pan = config.cpg_center_deg
        self._gaze_tilt = config.cpg_center_deg
        self._gaze_vertical = [config.cpg_center_deg] * 3
        self._emotion_scores = {name: 0.0 for name in ("Happy", "Sad", "Confused", "Neutral")}
        self._emotion_samples = {name: 0 for name in self._emotion_scores}
        self._emotion_seconds = {name: 0.0 for name in self._emotion_scores}
        self._timed_last_sequence = 0
        self._timed_last_at: float | None = None
        self._timed_last_emotion: str | None = None
        self._playback_duration = 0.0
        self.playback_mode: str | None = None
        self._seen_count = 0
        self._pose = [config.cpg_center_deg] * 4
        self._last_time: float | None = None
        self._phase_since = 0.0
        self._endpoint_started: float | None = None
        self._standby_settled_at: float | None = None
        self._scan_leg = 0
        self._cooldown_emotion: str | None = None
        self._new_emotion: str | None = None
        self._new_emotion_frames = 0
        self._cooldown_last_sequence = 0
        self.behavior = "OFF"

    def start(self, now: float, initial_angles: tuple[float, ...] | None = None) -> None:
        self._cooldown_emotion = None
        self._new_emotion = None
        self._new_emotion_frames = 0
        self._cooldown_last_sequence = 0
        self._last_sequence = self.mailbox.latest_after(0).sequence if self.mailbox.latest_after(0) is not None else 0
        self._camera_seen_at = None
        self._last_face_at = None
        self._face_x = None
        self._face_y = None
        self._observed_emotion = None
        self._observed_strength = 0.0
        self._observation_at = None
        self._seen_count = 0
        self._scan_leg = 0
        self._endpoint_started = None
        self._standby_settled_at = None
        self._last_time = float(now)
        self._phase_since = float(now)
        source = initial_angles or (self.config.cpg_center_deg,) * 4
        self._pose = [clamp(float(a), *self.limits[j]) for j, a in enumerate(source)]
        self._gaze_pan, self._gaze_tilt = self._pose[0], self._pose[3]
        self._gaze_vertical = list(self._pose[1:4])
        self._emotion_scores = {name: 0.0 for name in self._emotion_scores}
        self._emotion_samples = {name: 0 for name in self._emotion_samples}
        self._emotion_seconds = {name: 0.0 for name in self._emotion_seconds}
        self._timed_last_sequence = 0
        self._timed_last_at = None
        self._timed_last_emotion = None
        self._playback_duration = 0.0
        self.playback_mode = None
        self.behavior = "WAIT_CAMERA"

    def stop(self) -> None:
        self._cooldown_emotion = None
        self._new_emotion = None
        self._new_emotion_frames = 0
        self._cooldown_last_sequence = 0
        self.behavior = "OFF"
        self.playback_mode = None
        self._camera_seen_at = None
        self._face_x = None
        self._face_y = None
        self._observed_emotion = None
        self._observation_at = None

    def latest_expression(self, now: float, max_age: float = 0.3) -> tuple[str | None, float]:
        """Only return a current, valid detected-face camera classification."""
        if (self._observation_at is not None and 0 <= now - self._observation_at <= max_age
                and self._face_x is not None):
            return self._observed_emotion, self._observed_strength
        return None, 0.0

    def _observation(self, now: float) -> str:
        packet = self.mailbox.latest_after(self._last_sequence)
        if packet is not None:
            self._last_sequence = packet.sequence
            if packet.error:
                return "camera_error:" + packet.error
            age = now - float(packet.captured_at)
            if age < -0.15 or age > self.settings.camera_timeout_seconds:
                return "camera_frame_stale"
            if packet.observation is not None:
                self._camera_seen_at = float(packet.captured_at)
                obs = packet.observation
                if bool(obs.get("detected")):
                    x = obs.get("face_x")
                    if x is not None and isinstance(x, (int, float)) and math.isfinite(float(x)) and 0 <= x <= 1:
                        self._face_x = float(x)
                        y = obs.get("face_y")
                        self._face_y = (float(y) if isinstance(y, (int, float)) and
                                        not isinstance(y, bool) and math.isfinite(float(y)) and 0 <= y <= 1 else None)
                        self._observed_emotion = str(obs.get("emotion", "unknown")).lower().strip()
                        strength = obs.get("strength", 0.0)
                        self._observed_strength = (float(strength) if isinstance(strength, (int, float))
                                                   and not isinstance(strength, bool) and math.isfinite(float(strength))
                                                   and 0 <= float(strength) <= 1 else 0.0)
                        self._observation_at = now
                        self._last_face_at = now
                        self._seen_count += 1
                    elif x is None:
                        # Old camera/mock observation can still detect a face,
                        # but cannot steer without a measured horizontal center.
                        self._face_x = 0.5
                        self._face_y = None
                        self._observed_emotion = str(obs.get("emotion", "unknown")).lower().strip()
                        self._observed_strength = float(obs.get("strength", 0.0) or 0.0)
                        self._observation_at = now
                        self._last_face_at = now
                        self._seen_count += 1
                    else:
                        self._seen_count = 0
                        self._face_x = None
                        self._face_y = None
                        self._observed_emotion = None
                        self._observation_at = None
                else:
                    self._seen_count = 0
                    self._face_x = None
                    self._face_y = None
                    self._observed_emotion = None
                    self._observation_at = None
        if (self.behavior == "WAIT_CAMERA" and self._camera_seen_at is None
                and now - self._phase_since > self.settings.camera_timeout_seconds):
            return "camera_timeout"
        if (self._camera_seen_at is not None and
                now - self._camera_seen_at > self.settings.camera_timeout_seconds):
            return "camera_timeout"
        return ""

    def _enter(self, mode: str, now: float) -> None:
        self.behavior = mode
        self._phase_since = now
        self._endpoint_started = None
        # Standby timer begins on entering STANDBY; not gated by perfect
        # feedback / settling, which may never happen with real load.
        self._standby_settled_at = now if mode == "STANDBY" else None
        if mode == "SEARCHING":
            self._scan_leg = 0
        if mode == "TRACKING":
            self._emotion_scores = {name: 0.0 for name in self._emotion_scores}
            self._emotion_samples = {name: 0 for name in self._emotion_samples}
            self._emotion_seconds = {name: 0.0 for name in self._emotion_seconds}
            self._timed_last_sequence = 0
            self._timed_last_at = None
            self._timed_last_emotion = None
            self._playback_duration = 0.0
            self._gaze_vertical = list(self._pose[1:4])
            self.playback_mode = None

    def finish_stage(self, now: float, emotion: str) -> None:
        """Stage 2 finished: rest and require a different face emotion.

        A new face (after loss) also releases the lock; a continued identical
        detection cannot immediately restart the same stage.
        """
        self._cooldown_emotion = str(emotion).lower().strip()
        self._new_emotion = None
        self._new_emotion_frames = 0
        self._cooldown_last_sequence = self._last_sequence
        self._seen_count = 0
        self._enter("STANDBY", float(now))

    def step(self, now: float, cpg_angles: tuple[float, ...],
             actual_angles: dict[int, float] | None = None,
             gaze_wiggles: tuple[float, float] = (0.0, 0.0)) -> FaceMotion:
        if self.behavior == "OFF":
            raise RuntimeError("face search has not been started")
        now = float(now)
        fault = self._observation(now)
        if fault:
            return FaceMotion(tuple(self._pose), self.behavior, self._face_x,
                              face_y=self._face_y, camera_fault=fault)
        if self.behavior == "WAIT_CAMERA":
            if self._camera_seen_at is not None:
                self._enter("SEARCHING", now)
            else:
                return FaceMotion(tuple(self._pose), "WAIT_CAMERA", None, face_y=None)
        previous = self.behavior
        face_fresh = (self._face_x is not None and self._last_face_at is not None
                      and now - self._last_face_at <= self.settings.track_observation_max_age_s)
        face_confirmed = face_fresh and self._seen_count >= self.settings.face_confirm_frames
        if self._cooldown_emotion is not None:
            # Unlock if this face disappears or a NEW expression is observed
            # consistently for face_confirm_frames fresh camera packets.
            if not face_fresh and (self._last_face_at is None or
                    now - self._last_face_at > self.settings.lost_grace_seconds):
                self._cooldown_emotion = None
                self._new_emotion, self._new_emotion_frames = None, 0
            elif (self._observation_at == now and
                  self._last_sequence != self._cooldown_last_sequence and
                  self._observed_emotion in ("happy", "sad", "confused", "neutral")):
                self._cooldown_last_sequence = self._last_sequence
                if self._observed_emotion != self._cooldown_emotion:
                    if self._new_emotion == self._observed_emotion:
                        self._new_emotion_frames += 1
                    else:
                        self._new_emotion = self._observed_emotion
                        self._new_emotion_frames = 1
                    if self._new_emotion_frames >= self.settings.face_confirm_frames:
                        self._cooldown_emotion = None
                        self._new_emotion, self._new_emotion_frames = None, 0
                else:
                    self._new_emotion, self._new_emotion_frames = None, 0
        lost_emotion = None
        lost_emotion_seconds = None
        # Face tracking takes precedence even if a new person appears mid-dance.
        if face_confirmed and self.behavior != "TRACKING":
            self._enter("TRACKING", now)
            self._gaze_pan, self._gaze_tilt = self._pose[0], self._pose[3]
        if self.behavior == "TRACKING":
            # Accumulate actual elapsed seconds per emotion (not per-frame scores).
            # Only adjacent, fresh, confidently classified face frames contribute.
            # Gaps without face or stale frames NEVER count as observed time.
            if (face_fresh and self._observation_at == now and
                    self._last_sequence != self._timed_last_sequence):
                self._timed_last_sequence = self._last_sequence
                name = self._observed_emotion.title() if self._observed_emotion else None
                valid = (name in self._emotion_seconds and
                         (name == "Neutral" or self._observed_strength >= 0.35))
                if (self._timed_last_at is not None and self._timed_last_emotion is not None and
                        0.0 < now - self._timed_last_at <= self.settings.camera_timeout_seconds):
                    elapsed = now - self._timed_last_at
                    self._emotion_seconds[self._timed_last_emotion] += elapsed
                self._timed_last_at = now if valid else None
                self._timed_last_emotion = name if valid else None
                if valid:
                    self._emotion_scores[name] += max(0.1, self._observed_strength)
                    self._emotion_samples[name] += 1
            if self._last_face_at is None or now - self._last_face_at > self.settings.lost_grace_seconds:
                # For very brief face encounters, still give a minimum safe
                # performance instead of requiring an arbitrary FPS/frame count.
                eligible = [name for name, count in self._emotion_samples.items() if count > 0]
                if eligible:
                    lost_emotion = max(eligible, key=lambda n: (self._emotion_seconds[n], self._emotion_scores[n]))
                    observed_seconds = self._emotion_seconds[lost_emotion]
                    lost_emotion_seconds = clamp(observed_seconds,
                        self.settings.playback_min_seconds, self.settings.playback_max_seconds)
                    self._playback_duration = lost_emotion_seconds
                    self.playback_mode = lost_emotion
                    self._enter("PLAYBACK", now)
                else:
                    self.playback_mode = None
                    self._enter("STANDBY", now)
        if self.behavior == "PLAYBACK" and now - self._phase_since >= self._playback_duration:
            self.playback_mode = None
            self._enter("STANDBY", now)
        if (self.behavior == "STANDBY" and
                now - self._phase_since >= self.settings.standby_seconds):
            self._enter("SEARCHING", now)
        dt = 0 if self._last_time is None else clamp(now - self._last_time, 0.0, 0.25)
        self._last_time = now
        if self.behavior == "TRACKING":
            pan = self._gaze_pan
            vertical = list(self._gaze_vertical)
            if self._last_face_at is not None and now - self._last_face_at <= self.settings.track_observation_max_age_s:
                if self._face_x is not None:
                    error_x = self._face_x - 0.5
                    if abs(error_x) > self.settings.track_deadband_x:
                        sign_x = 1 if self.settings.track_invert_x else -1
                        velocity = clamp(sign_x * error_x * self.settings.track_gain_deg_s,
                                         -self.settings.track_max_speed_deg_s, self.settings.track_max_speed_deg_s)
                        pan += velocity * dt
                if self._face_y is not None:
                    error_y = self._face_y - 0.5
                    if abs(error_y) > self.settings.track_deadband_y:
                        sign_y = 1 if self.settings.track_invert_y else -1
                        velocity = clamp(sign_y * error_y * self.settings.track_gain_y_deg_s,
                                         -self.settings.track_max_y_speed_deg_s, self.settings.track_max_y_speed_deg_s)
                        for index, fraction in enumerate(self.settings.track_vertical_mix):
                            direction = -1 if self.settings.track_vertical_invert[index] else 1
                            vertical[index] += direction * fraction * velocity * dt
            self._gaze_pan = clamp(pan, *self.limits[0])
            self._gaze_vertical = [clamp(v, *self.limits[j + 1]) for j, v in enumerate(vertical)]
            # No personality motion while face is in frame. Camera gaze alone
            # controls all four joints; CPG continues recording emotion only.
            target = (self._gaze_pan, *self._gaze_vertical)
        elif self.behavior == "PLAYBACK":
            target = tuple(clamp(float(a), *self.limits[j]) for j, a in enumerate(cpg_angles))
        else:
            target = list(self.standby_pose)
            if self.behavior == "SEARCHING":
                endpoint = self._scan_min if self._scan_leg == 0 else self._scan_max
                target[0] = endpoint
                actual = None if actual_angles is None else actual_angles.get(0)
                reached_cmd = abs(self._pose[0] - endpoint) <= 0.1
                reached_actual = actual is None or abs(actual - endpoint) <= self.settings.scan_endpoint_tolerance_deg
                if reached_cmd and reached_actual:
                    self._endpoint_started = None
                    if self._scan_leg == 0:
                        self._scan_leg = 1
                    else:
                        self._enter("STANDBY", now)
                        target[0] = self.standby_pose[0]
                elif reached_cmd and not reached_actual:
                    if self._endpoint_started is None:
                        self._endpoint_started = now
                    if now - self._endpoint_started > self.settings.scan_endpoint_timeout_seconds:
                        return FaceMotion(tuple(self._pose), self.behavior, self._face_x,
                                          face_y=self._face_y, camera_fault="scan_endpoint_not_reached")
            target = tuple(target)
        output = []
        for j, desired in enumerate(target):
            max_speed = (self.settings.scan_speed_deg_s if j == 0 and self.behavior == "SEARCHING"
                         else self.settings.pose_max_speed_deg_s)
            commanded = approach(self._pose[j], desired, max_speed * dt)
            output.append(clamp(commanded, *self.limits[j]))
        self._pose = output
        return FaceMotion(tuple(output), self.behavior, self._face_x, face_y=self._face_y,
                          just_acquired=(previous != "TRACKING" and self.behavior == "TRACKING"),
                          lost_emotion=lost_emotion,
                          lost_emotion_seconds=lost_emotion_seconds)
