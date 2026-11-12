"""Thread-safe camera-mailbox adapter for the local, editable CPG."""
from __future__ import annotations

import math
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cpg_engine import LampController
from .cpg_profile import DEFAULT_PROFILE_PATH, MODES, load_profile, pasted_defaults, validate_profile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAINCPG_ROOT = PROJECT_ROOT / "face_vision(maincpg)"


def ensure_maincpg_importable() -> None:
    """Only camera perception still needs the external face_vision directory."""
    root = str(MAINCPG_ROOT)
    if MAINCPG_ROOT.is_dir() and root not in sys.path:
        sys.path.insert(0, root)


@dataclass(frozen=True)
class CpgSnapshot:
    q_deg: tuple[float, float, float, float]
    hormones: dict[str, float]
    emotion: str
    input_status: str
    light: dict[str, Any]
    scheduler_fault: bool


class MainCpgAdapter:
    def __init__(self, *, start_time: float, mailbox: Any | None = None,
                 profile: dict | None = None, profile_path: str | Path = DEFAULT_PROFILE_PATH,
                 loop_hz: float = 20., allow_defaults: bool = False) -> None:
        self.dt = 1. / float(loop_hz)
        self.profile_path = Path(profile_path)
        try:
            selected = load_profile(self.profile_path, dt=self.dt) if profile is None else validate_profile(profile, dt=self.dt)
        except ValueError:
            if not allow_defaults or profile is not None:
                raise
            selected = validate_profile(pasted_defaults(), dt=self.dt)
        self._lock = threading.RLock()
        self._controller = LampController(start_time=float(start_time), profile=selected, dt=self.dt)
        self._mailbox = mailbox
        self._last_camera_sequence = 0
        self._last_valid_capture: float | None = None
        self._capture_age_enforced = False
        self._input_override: str | None = None
        self._ready = False
        self._manual_mode: str | None = None

    @property
    def manual_mode(self) -> str | None:
        """Current manual mode; None uses live camera emotion."""
        with self._lock:
            return self._manual_mode

    def select_manual_mode(self, mode: str) -> None:
        """Select one of four existing profiles; never commands hardware."""
        if mode not in MODES:
            raise ValueError(f"Unknown manual personality: {mode}")
        with self._lock:
            self._manual_mode = mode

    def select_camera_auto(self) -> None:
        """Release manual override; camera watchdog/debounce apply again."""
        with self._lock:
            self._manual_mode = None

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._manual_mode is not None or self._ready

    def profile_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return validate_profile(self._controller.profile, dt=self.dt)

    def prepare_profile(self, profile: dict[str, Any], *, start_time: float) -> LampController:
        """Fully validate/build before updating the live controller."""
        clean = validate_profile(profile, dt=self.dt)
        return LampController(start_time=start_time, profile=clean, dt=self.dt)

    def install_prepared(self, controller: LampController) -> None:
        """Swap the whole controller; require a fresh camera observation."""
        if not isinstance(controller, LampController):
            raise TypeError("expected a local LampController")
        with self._lock:
            # Discard even an unconsumed PRE-APPLY camera packet: the new model
            # may start only after an observation published after this swap.
            if self._mailbox is not None:
                packet = self._mailbox.latest_after(self._last_camera_sequence)
                if packet is not None:
                    self._last_camera_sequence = packet.sequence
            self._controller = controller
            self._last_valid_capture = None
            self._capture_age_enforced = False
            self._input_override = None
            self._ready = False

    def apply_profile(self, profile: dict[str, Any], *, start_time: float | None = None) -> None:
        import time
        prepared = self.prepare_profile(profile, start_time=time.monotonic() if start_time is None else start_time)
        self.install_prepared(prepared)

    def begin_motion(self, now: float) -> None:
        with self._lock:
            self._controller.begin_motion(now, override_mode=self._manual_mode)

    def attach_mailbox(self, mailbox: Any) -> None:
        with self._lock:
            self._mailbox = mailbox
            self._last_camera_sequence = 0
            self._last_valid_capture = None
            self._capture_age_enforced = False
            self._input_override = None
            self._ready = False
            self._controller.gate.invalidate("camera_attached")

    def observe(self, observation: dict[str, Any], received_at: float) -> bool:
        with self._lock:
            accepted = bool(self._controller.observe(observation, float(received_at)))
            self._ready = accepted
            self._last_valid_capture = float(received_at) if accepted else None
            self._capture_age_enforced = False
            self._input_override = None
            return accepted

    def step(self, now: float) -> CpgSnapshot:
        with self._lock:
            timestamp = float(now)
            self._consume_camera(timestamp)
            capture_expired = bool(
                self._capture_age_enforced
                and self._last_valid_capture is not None
                and timestamp - self._last_valid_capture > self._controller.gate.timeout_s
            )
            if capture_expired:
                self._ready = False
                self._last_valid_capture = None
                self._input_override = "camera_processing_timeout"
                self._controller.gate.invalidate("camera_processing_timeout")
            raw = self._controller.step(timestamp, override_mode=self._manual_mode)
            if (self._last_valid_capture is None or
                    timestamp - self._last_valid_capture > self._controller.gate.timeout_s):
                self._ready = False
            q_values = tuple(float(value) for value in raw["q_deg"])
            if len(q_values) != 4 or not all(math.isfinite(value) for value in q_values):
                raise ValueError("local CPG returned invalid four-joint output")
            hormone_values = {name: float(raw["hormones"][name]) for name in ("Happy", "Sad", "Confused")}
            if not all(math.isfinite(value) and 0 <= value <= 1 for value in hormone_values.values()):
                raise ValueError("local CPG returned invalid hormone values")
            return CpgSnapshot(
                q_deg=q_values,
                hormones=hormone_values,
                emotion=str(raw["mode"]),
                input_status=(str(raw["input_status"]) if self._manual_mode is not None
                              else self._input_override or str(raw["input_status"])),
                light=dict(raw["light"]),
                scheduler_fault=bool(raw["scheduler_fault"]),
            )

    def _consume_camera(self, now: float) -> None:
        if self._mailbox is None:
            return
        packet = self._mailbox.latest_after(self._last_camera_sequence)
        if packet is None:
            return
        self._last_camera_sequence = packet.sequence
        if packet.error:
            self._ready = False
            self._last_valid_capture = None
            self._capture_age_enforced = False
            self._input_override = "camera_error"
            self._controller.gate.invalidate("camera_error")
            return
        if packet.observation is not None:
            age = now - float(packet.captured_at)
            if age < -0.1 or age > self._controller.gate.timeout_s:
                self._ready = False
                self._last_valid_capture = None
                self._capture_age_enforced = True
                self._input_override = "camera_processing_timeout"
                self._controller.gate.invalidate("camera_processing_timeout")
                return
            accepted = bool(self._controller.observe(packet.observation, now))
            self._ready = accepted
            self._last_valid_capture = float(packet.captured_at) if accepted else None
            self._capture_age_enforced = accepted
            self._input_override = None
