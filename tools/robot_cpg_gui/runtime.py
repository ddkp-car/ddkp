from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from .config import RuntimeConfig
from .mapping import HardwareMapper
from .emotion_stage import EmotionStageController, load_stage_settings
from .emotion_playback import EmotionPlayback


class RuntimeState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    ARMED = "ARMED"
    CENTERING = "CENTERING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAULT = "FAULT"
    EMERGENCY = "EMERGENCY"
    CLOSED = "CLOSED"


class RuntimeCommand(str, Enum):
    CONNECT = "connect"
    ARM = "arm"
    CENTER = "center"
    START_CPG = "start_cpg"
    STOP_CPG = "stop_cpg"
    DISCONNECT = "disconnect"
    CAMERA_AUTO_ON = "camera_auto_on"
    CAMERA_AUTO_OFF = "camera_auto_off"
    CAMERA_SEARCH = "camera_search"


@dataclass(frozen=True)
class RuntimeSnapshot:
    timestamp: float
    state: RuntimeState
    feedback_raw: dict[int, int]
    feedback_speed: dict[int, int]
    feedback_moving: dict[int, bool]
    feedback_torque: dict[int, bool]
    cpg_deg: tuple[float, float, float, float]
    target_raw: dict[int, int]
    hormones: dict[str, float]
    emotion: str
    input_status: str
    fault_text: str
    torque_off_verified: bool
    center_ready: bool = False
    camera_behavior: str = "OFF"
    face_x: float | None = None
    face_y: float | None = None
    light: dict[str, Any] | None = None
    personality_stage: str = "BASE"
    peak_held_s: float = 0.0
    observed_seconds: dict[str, float] | None = None


class RobotRuntime:
    POSITION_TOLERANCE = 20
    POSITION_TIMEOUT = 5.0
    CENTER_STABLE_SECONDS = 0.4
    CENTER_STABLE_RAW = 2

    def __init__(self, config: RuntimeConfig, mapper: HardwareMapper, bus: Any, cpg_source: Any,
                 face_search: Any | None = None) -> None:
        self.config = config
        self.mapper = mapper
        self.bus = bus
        self.cpg_source = cpg_source
        self.face_search = face_search
        profile = (cpg_source.profile_snapshot() if callable(getattr(cpg_source, "profile_snapshot", None)) else {})
        self.emotion_stage = (EmotionStageController(
            load_stage_settings(), joint_limits=profile.get("joint_limits"),
            minimum_expression_strength=profile.get("input_gate", {}).get("min_strength", 0.35)
        ) if face_search is not None else None)
        self._camera_auto_enabled = False
        self._face_search_driving = False
        self._playback: EmotionPlayback | None = None
        self._manual_transition_pose: list[float] | None = None
        self._manual_transition_last_time: float | None = None
        self._commands: queue.Queue[RuntimeCommand] = queue.Queue()
        self._emergency = threading.Event()
        self._emergency_pending = threading.Event()
        self._motion_gate = threading.RLock()
        self._control_lock = threading.RLock()  # serialize profile swap with control steps
        self._close_requested = threading.Event()
        self._worker_stopped = threading.Event()
        self._snapshot_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._bus_open = False
        self._poll_id = 0
        self._motion_deadline: float | None = None
        self._center_stable_since: float | None = None
        self._center_stable_reference: dict[int, int] | None = None
        centers = {servo_id: servo.center for servo_id, servo in config.servos.items()}
        self._snapshot = RuntimeSnapshot(
            timestamp=0.0,
            state=RuntimeState.DISCONNECTED,
            feedback_raw={},
            feedback_speed={},
            feedback_moving={},
            feedback_torque={},
            cpg_deg=(90.0, 90.0, 90.0, 90.0),
            target_raw=centers,
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral",
            input_status="not_started",
            fault_text="",
            torque_off_verified=False,
            center_ready=False,
            camera_behavior="OFF",
            light={"color": "#000000", "brightness": 0.0},
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._worker_stopped.clear()
        self._thread = threading.Thread(target=self._run_loop, name="robot-runtime", daemon=True)
        self._thread.start()

    def submit(self, command: RuntimeCommand | str) -> None:
        selected = command if isinstance(command, RuntimeCommand) else RuntimeCommand(command)
        self._commands.put(selected)

    def request_emergency(self) -> None:
        self._emergency_pending.set()
        with self._motion_gate:
            self._emergency.set()

    @property
    def is_stopped(self) -> bool:
        if self._thread is None:
            return self.latest_snapshot().state is RuntimeState.CLOSED
        return not self._thread.is_alive()

    def latest_snapshot(self) -> RuntimeSnapshot:
        with self._snapshot_lock:
            snapshot = self._snapshot
            return replace(
                snapshot,
                feedback_raw=dict(snapshot.feedback_raw),
                feedback_speed=dict(snapshot.feedback_speed),
                feedback_moving=dict(snapshot.feedback_moving),
                feedback_torque=dict(snapshot.feedback_torque),
                target_raw=dict(snapshot.target_raw),
                hormones=dict(snapshot.hormones),
            )

    def apply_cpg_profile(self, profile: dict) -> None:
        """Prepare, validate, persist and swap only with motion safely stopped.

        The control lock prevents a queued START/STOP/CENTER command from
        interleaving with the transaction. It never writes to a Servo bus.
        """
        import os
        import tempfile
        from .cpg_profile import save_profile_atomic
        with self._control_lock:
            if self._close_requested.is_set() or self.latest_snapshot().state not in (
                RuntimeState.DISCONNECTED, RuntimeState.CONNECTED, RuntimeState.ARMED
            ):
                raise RuntimeError("Stop CPG and wait until motion is settled before applying a profile")
            if not hasattr(self.cpg_source, "prepare_profile"):
                raise RuntimeError("CPG source does not support profiles")
            now = time.monotonic()
            ready = self.cpg_source.prepare_profile(profile, start_time=now)
            path = self.cpg_source.profile_path
            prior_bytes = path.read_bytes() if path.exists() else None
            # Source validation & controller construction finish BEFORE writing.
            save_profile_atomic(profile, path, dt=self.cpg_source.dt)
            try:
                self.cpg_source.install_prepared(ready)
            except Exception:
                # Unexpected swap failure: restore previous bytes atomically.
                if prior_bytes is None:
                    path.unlink(missing_ok=True)
                else:
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".cpg-restore-", delete=False) as handle:
                            temporary = handle.name
                            handle.write(prior_bytes)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temporary, path)
                        temporary = None
                    finally:
                        if temporary:
                            os.unlink(temporary)
                raise

    def process_once(self, now: float | None = None) -> None:
        with self._control_lock:
            self._process_once_locked(now)

    def _process_once_locked(self, now: float | None = None) -> None:
        timestamp = time.monotonic() if now is None else float(now)
        if self.latest_snapshot().state is RuntimeState.CLOSED:
            return
        if self._emergency_pending.is_set() or self._emergency.is_set():
            self._process_emergency(timestamp)
            return

        try:
            state = self.latest_snapshot().state
            if state in (RuntimeState.FAULT, RuntimeState.EMERGENCY):
                try:
                    command = self._commands.get_nowait()
                except queue.Empty:
                    return
                self._handle_command(command, timestamp)
                return
            frame = self._sample_cpg(timestamp)
            self._checkpoint_emergency(timestamp)
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                command = None
            if command is not None:
                before = self.latest_snapshot().state
                self._handle_command(command, timestamp)
                if (command is RuntimeCommand.START_CPG
                    and before is RuntimeState.ARMED
                    and self.latest_snapshot().state is RuntimeState.RUNNING
                    and hasattr(self.cpg_source, "begin_motion")):
                    self.cpg_source.begin_motion(timestamp)
                    frame = self._sample_cpg(timestamp)

            self._maybe_begin_camera_autonomy(timestamp)
            state = self.latest_snapshot().state
            if state is RuntimeState.RUNNING:
                self._run_cpg_tick(frame, timestamp)
            elif state in (RuntimeState.CENTERING, RuntimeState.STOPPING):
                self._finish_positioning(timestamp)
            elif state is RuntimeState.ARMED:
                self._monitor_armed(timestamp)
        except _EmergencyAbort:
            return
        except Exception as exc:
            self._enter_fault(timestamp, str(exc))

    def close(self, timeout: float = 3.0) -> bool:
        if self.latest_snapshot().state is RuntimeState.CLOSED:
            return self.is_stopped
        self._close_requested.set()
        self.request_emergency()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive() and self.latest_snapshot().state is RuntimeState.CLOSED
        self.process_once(time.monotonic())
        return self.latest_snapshot().state is RuntimeState.CLOSED

    def _run_loop(self) -> None:
        period = 1.0 / self.config.loop_hz
        next_tick = time.monotonic()
        try:
            while not self._worker_stopped.is_set():
                now = time.monotonic()
                self.process_once(now)
                if self.latest_snapshot().state is RuntimeState.CLOSED:
                    break
                next_tick += period
                if next_tick < time.monotonic():
                    next_tick = time.monotonic()
                self._worker_stopped.wait(max(0.0, next_tick - time.monotonic()))
        finally:
            self._worker_stopped.set()

    def _handle_command(self, command: RuntimeCommand, now: float) -> None:
        state = self.latest_snapshot().state
        if state in (RuntimeState.FAULT, RuntimeState.EMERGENCY) and command not in (
            RuntimeCommand.CONNECT,
            RuntimeCommand.DISCONNECT,
        ):
            return
        if command is RuntimeCommand.CAMERA_AUTO_ON:
            self._camera_auto_enabled = self.face_search is not None
            self._update_snapshot(timestamp=now, camera_behavior=("WAIT_CENTER" if self._camera_auto_enabled else "OFF"))
            return
        if command is RuntimeCommand.CAMERA_SEARCH:
            self._camera_auto_enabled = self.face_search is not None
            if self._face_search_driving and state is RuntimeState.RUNNING:
                # Explicit AUTO button/Num0 restarts the scan without jumping
                # to Center or bypassing any hardware safety checks.
                last_raw = self.latest_snapshot().target_raw
                angles = self.mapper.feedback_absolute_deg(last_raw)
                self.face_search.start(now, tuple(angles[i] for i in sorted(angles)))
                if self.emotion_stage is not None:
                    self.emotion_stage.reset()
            self._update_snapshot(timestamp=now,
                                  camera_behavior="WAIT_CAMERA" if self._camera_auto_enabled else "OFF",
                                  personality_stage="BASE", peak_held_s=0.0)
            return
        if command is RuntimeCommand.CAMERA_AUTO_OFF:
            self._camera_auto_enabled = False
            last_raw = self.latest_snapshot().target_raw
            self._stop_face_search()
            # If camera tracking was active during a manual Numpad expression,
            # return to the manual pose with a rate-limited handoff.
            if state is RuntimeState.RUNNING and getattr(self.cpg_source, "manual_mode", None) is not None:
                relative = self.mapper.feedback_absolute_deg(last_raw)
                self._manual_transition_pose = [relative[i] for i in sorted(relative)]
                self._manual_transition_last_time = now
            self._update_snapshot(timestamp=now, camera_behavior="OFF", face_x=None, face_y=None,
                                  personality_stage="BASE", peak_held_s=0.0)
            # Preserve manually selected motion without a camera.
            if state is RuntimeState.RUNNING and getattr(self.cpg_source, "manual_mode", None) is None:
                self._command_center(now, stopping=True)
            return
        if command is RuntimeCommand.CONNECT:
            if state in (RuntimeState.FAULT, RuntimeState.EMERGENCY):
                errors = self._attempt_torque_off()
                if errors:
                    previous = self.latest_snapshot().fault_text
                    current = "; ".join(errors)
                    self._update_snapshot(
                        timestamp=now,
                        state=state,
                        fault_text=(
                            previous + ("; " + current if current else "")
                            if previous else current
                        ),
                        torque_off_verified=False,
                    )
                    return
            self._connect(now)
        elif command is RuntimeCommand.ARM:
            if state is not RuntimeState.CONNECTED:
                self._update_snapshot(timestamp=now, fault_text="Connect before Enable Motion")
                return
            self._arm(now)
        elif command is RuntimeCommand.CENTER:
            if state is not RuntimeState.ARMED:
                self._update_snapshot(timestamp=now, fault_text="Center requires ARMED state")
                return
            self._command_center(now)
        elif command is RuntimeCommand.START_CPG:
            if state is not RuntimeState.ARMED:
                self._update_snapshot(timestamp=now, fault_text="Enable Motion before Start CPG")
                return
            if not self.latest_snapshot().center_ready:
                self._update_snapshot(
                    timestamp=now,
                    fault_text="Go Center successfully after Enable Motion before Start CPG",
                )
                return
            if self._camera_auto_enabled and self.face_search is not None and getattr(self.cpg_source, "manual_mode", None) is None:
                packet = self.face_search.mailbox.latest_after(0)
                if (packet is None or packet.error or packet.observation is None
                        or now - packet.captured_at > self.face_search.settings.camera_timeout_seconds
                        or packet.captured_at > now + 0.15):
                    self._update_snapshot(timestamp=now, fault_text="Camera stream has no fresh observations")
                    return
            elif not bool(getattr(self.cpg_source, "ready", False)):
                self._update_snapshot(timestamp=now, fault_text="Camera is not ready")
                return
            readings = self._read_all(now)
            self._validate_feedback(readings)
            self._require_torque_enabled(readings)
            self._store_feedback(readings, now)
            self._update_snapshot(
                timestamp=now, state=RuntimeState.RUNNING,
                fault_text="", center_ready=False,
            )
        elif command is RuntimeCommand.STOP_CPG:
            self._camera_auto_enabled = False
            self._stop_face_search()
            if state in (RuntimeState.RUNNING, RuntimeState.STOPPING):
                self._command_center(now, stopping=True)
        elif command is RuntimeCommand.DISCONNECT:
            self._camera_auto_enabled = False
            self._stop_face_search()
            errors = self._attempt_torque_off()
            if errors:
                self._update_snapshot(
                    timestamp=now, state=RuntimeState.FAULT,
                    fault_text="; ".join(errors), torque_off_verified=False,
                    center_ready=False,
                )
            else:
                self._close_bus(now, final_state=RuntimeState.DISCONNECTED)

    def _connect(self, now: float) -> None:
        if self._bus_open:
            self.bus.close()
            self._bus_open = False
        self.bus.open()
        self._bus_open = True
        missing = [servo_id for servo_id in sorted(self.config.servos) if not self.bus.ping(servo_id)]
        if missing:
            raise RuntimeError("missing Servo IDs: " + ", ".join(map(str, missing)))
        readings = self._read_all(now)
        self._validate_feedback(readings)
        if any(item.torque_enabled for item in readings.values()):
            raise RuntimeError("Torque is enabled at connect; use Emergency Torque Off and reconnect")
        self._store_feedback(readings, now)
        self._update_snapshot(
            timestamp=now, state=RuntimeState.CONNECTED, fault_text="",
            torque_off_verified=not any(item.torque_enabled for item in readings.values()),
            center_ready=False,
        )

    def _arm(self, now: float) -> None:
        readings = self._read_all(now)
        self._validate_feedback(readings)
        if any(item.torque_enabled for item in readings.values()):
            raise RuntimeError("Torque is already enabled; use Emergency Torque Off first")
        current = {servo_id: item.position for servo_id, item in readings.items()}
        self._checkpoint_emergency(now)
        self._motion_transaction(
            now, lambda: self.bus.sync_write_positions(current, self.config.speed)
        )
        for servo_id in sorted(self.config.servos):
            self._motion_transaction(
                now, lambda servo_id=servo_id: self.bus.set_torque(servo_id, True)
            )
        readings = self._read_all(now)
        self._require_torque_enabled(readings)
        self._store_feedback(readings, now)
        self._update_snapshot(
            timestamp=now, state=RuntimeState.ARMED, target_raw=current,
            fault_text="", torque_off_verified=False, center_ready=False,
        )

    def _sample_cpg(self, now: float) -> Any:
        frame = self.cpg_source.step(now)
        q_deg = tuple(float(value) for value in frame.q_deg)
        if len(q_deg) != 4 or not all(math.isfinite(value) for value in q_deg):
            raise ValueError("CPG returned invalid four-joint output")
        self._update_snapshot(
            timestamp=now,
            cpg_deg=q_deg,
            hormones=dict(frame.hormones),
            emotion=str(frame.emotion),
            input_status=str(frame.input_status),
            light=dict(getattr(frame, "light", {}) or {"color": "#000000", "brightness": 0.0}),
        )
        return frame

    def _stop_face_search(self) -> None:
        self._face_search_driving = False
        self._playback = None
        if self.emotion_stage is not None:
            self.emotion_stage.reset()
        self._manual_transition_pose = None
        self._manual_transition_last_time = None
        if self.face_search is not None:
            self.face_search.stop()

    def _maybe_begin_camera_autonomy(self, now: float) -> None:
        snap = self.latest_snapshot()
        if (not self._camera_auto_enabled or self.face_search is None
                or snap.state is not RuntimeState.ARMED or not snap.center_ready):
            return
        # A valid, CURRENT camera frame must arrive after START CAMERA; no
        # automatic blind motion for a missing camera.
        packet = self.face_search.mailbox.latest_after(0)
        if (packet is None or packet.error or packet.observation is None
                or now - packet.captured_at > self.face_search.settings.camera_timeout_seconds
                or packet.captured_at > now + 0.15):
            return
        readings = self._read_all(now)
        self._validate_feedback(readings)
        self._require_torque_enabled(readings)
        self._store_feedback(readings, now)
        initial = self.mapper.feedback_absolute_deg({i: v.position for i, v in readings.items()})
        self.face_search.start(now, tuple(initial[i] for i in sorted(initial)))
        self._face_search_driving = True
        self._manual_transition_pose = None
        self._update_snapshot(timestamp=now, state=RuntimeState.RUNNING,
                              center_ready=False, camera_behavior="WAIT_CAMERA", fault_text="")

    def _manual_motion_ramp(self, desired: tuple[float, ...], now: float) -> tuple[float, ...]:
        if self._manual_transition_pose is None:
            return desired
        dt = min(0.25, max(0.0, now - (self._manual_transition_last_time or now)))
        self._manual_transition_last_time = now
        max_delta = 20.0 * dt
        values = [max(prev - max_delta, min(prev + max_delta, float(target)))
                  for prev, target in zip(self._manual_transition_pose, desired)]
        self._manual_transition_pose = values
        if all(abs(a - b) < 0.05 for a, b in zip(values, desired)):
            self._manual_transition_pose = None
        return tuple(values)

    def _run_cpg_tick(self, frame: Any, now: float) -> None:
        angles = tuple(float(v) for v in frame.q_deg)
        behavior = "OFF"
        face_x = None
        face_y = None
        light = dict(getattr(frame, "light", {}) or {"color": "#000000", "brightness": 0.0})
        manual = getattr(self.cpg_source, "manual_mode", None)
        # Camera gaze runs independently of AUTO/MANUAL personality.
        if self._camera_auto_enabled and self.face_search is not None:
            if not self._face_search_driving:
                last_sent = self.latest_snapshot().target_raw
                previous_by_id = self.mapper.feedback_absolute_deg(last_sent)
                previous = tuple(previous_by_id[i] for i in sorted(previous_by_id))
                self.face_search.start(now, previous)
                self._face_search_driving = True
                self._manual_transition_pose = None
            snap = self.latest_snapshot()
            measured = self.mapper.feedback_absolute_deg(snap.feedback_raw) if set(snap.feedback_raw) == set(self.config.servos) else None
            stage_label, peak_held = "BASE", 0.0
            performance = None
            target_pose = (90.0, 90.0, 90.0, 90.0)
            if self.face_search.behavior == "PLAYBACK" and self._playback is not None:
                performance = self._playback.step(now)
                target_pose = performance.q_deg
            # One state-machine step per tick: its slew limiter uses the
            # actual loop dt, never a second zero-dt call.
            result = self.face_search.step(now, target_pose, measured)
            if result.lost_emotion is not None:
                profile = self.cpg_source.profile_snapshot()
                stage_settings = self.emotion_stage.settings if self.emotion_stage is not None else load_stage_settings()
                self._playback = EmotionPlayback(
                    now=now, mode=result.lost_emotion, profile=profile,
                    dt=1.0 / self.config.loop_hz,
                    dance_hz=stage_settings.happy_dance_hz,
                    rainbow_cycle_s=stage_settings.rgb_rainbow_cycle_s,
                    duration_s=result.lost_emotion_seconds,
                )
            if result.behavior == "TRACKING":
                # No personality motor output while face is visible.
                self._playback = None
                stage_label = "OBSERVING"
                # Face recognized: solid yellow for every configured Pi5Neo pixel.
                light = {"color": "#ffd43b", "brightness": 0.60}
            elif result.behavior == "PLAYBACK" and performance is not None:
                stage_label = f"{performance.stage} {performance.emotion}"
                light = performance.light
                self._update_snapshot(timestamp=now, hormones=performance.hormones,
                                      emotion=performance.emotion,
                                      input_status=performance.input_status)
            elif result.behavior != "PLAYBACK":
                self._playback = None
            if result.camera_fault:
                self._camera_auto_enabled = False
                self._stop_face_search()
                self._update_snapshot(timestamp=now, camera_behavior="CAMERA_LOST", face_x=None,
                                      fault_text=f"Camera search stopped: {result.camera_fault}")
                if result.camera_fault == "scan_endpoint_not_reached":
                    raise RuntimeError(result.camera_fault)
                self._command_center(now, stopping=True)
                self._update_snapshot(fault_text=f"Camera search stopped: {result.camera_fault}",
                                      camera_behavior="CAMERA_LOST")
                return
            if result.just_acquired and hasattr(self.cpg_source, "begin_motion"):
                # Reset only the original CPG timebase for the next face cycle.
                self.cpg_source.begin_motion(now)
            angles = result.angles
            behavior = result.behavior
            face_x = result.face_x
            face_y = result.face_y
            if behavior == "SEARCHING":
                light = {"color": "#ffb000", "brightness": 0.4}
            elif behavior in ("STANDBY", "WAIT_CAMERA"):
                light = {"color": "#5276af", "brightness": 0.12}
            if behavior in ("STANDBY", "WAIT_CAMERA", "SEARCHING"):
                stage_label = "BASE"
                peak_held = 0.0
        else:
            stage_label, peak_held = "BASE", 0.0
            if self.emotion_stage is not None:
                self.emotion_stage.reset()
            if self._face_search_driving:
                # _sample_cpg() has already overwritten snapshot.cpg_deg
                # with the new manual target. Use the *last raw command* to
                # reconstruct the actual prior setpoint, including wide pan.
                previous_raw = self.latest_snapshot().target_raw
                previous_by_id = self.mapper.feedback_absolute_deg(previous_raw)
                previous = [previous_by_id[i] for i in sorted(previous_by_id)]
                self._stop_face_search()
                self._manual_transition_pose = previous
                self._manual_transition_last_time = now
                if hasattr(self.cpg_source, "begin_motion"):
                    self.cpg_source.begin_motion(now)
                    frame = self._sample_cpg(now)
                    angles = tuple(float(v) for v in frame.q_deg)
            angles = self._manual_motion_ramp(angles, now)
            if manual is not None:
                behavior = "MANUAL"
        # Wide J1 mapping is authorized only by camera search/tracking, or
        # by its speed-limited handoff back to normal/manual CPG. This avoids
        # a large jump when a numpad personality interrupts a wide scan.
        wide_j1_motion = (behavior in ("SEARCHING", "TRACKING", "PLAYBACK", "STANDBY", "WAIT_CAMERA")
                          or self._manual_transition_pose is not None)
        # Camera standby J3 is nominal 45 degrees (offset -45 from center).
        # Preserve the optional extended mapping for old user profiles and
        # safe rate-limited handoffs, never bypass hardware safe min/max.
        extended_j3_motion = (behavior in ("SEARCHING", "TRACKING", "PLAYBACK", "STANDBY", "WAIT_CAMERA")
                              or self._manual_transition_pose is not None)
        targets = self.mapper.targets_from_cpg(
            angles, allow_extended_j1=wide_j1_motion,
            allow_extended_j3=extended_j3_motion
        )
        readings = self._read_all(now)
        self._validate_feedback(readings)
        self._require_torque_enabled(readings)
        self._store_feedback(readings, now)
        self._motion_transaction(now, lambda: self.bus.sync_write_positions(targets, self.config.speed))
        self._update_snapshot(timestamp=now, target_raw=targets, cpg_deg=angles,
                              camera_behavior=behavior, face_x=face_x, face_y=face_y,
                              light=light, fault_text="",
                              personality_stage=stage_label, peak_held_s=peak_held,
                              observed_seconds=(dict(self.face_search._emotion_seconds)
                                                if self._face_search_driving and self.face_search is not None
                                                else None))

    def _command_center(self, now: float, *, stopping: bool = False) -> None:
        self._stop_face_search()
        centers = {servo_id: servo.center for servo_id, servo in self.config.servos.items()}
        self._motion_transaction(
            now, lambda: self.bus.sync_write_positions(centers, self.config.speed)
        )
        self._motion_deadline = now + self.POSITION_TIMEOUT
        self._center_stable_since = None
        self._center_stable_reference = None
        self._update_snapshot(
            timestamp=now,
            state=RuntimeState.STOPPING if stopping else RuntimeState.CENTERING,
            target_raw=centers, cpg_deg=(90.0, 90.0, 90.0, 90.0),
            emotion="Neutral", input_status="stopped" if stopping else "center",
            fault_text="", center_ready=False, camera_behavior="OFF", face_x=None, face_y=None,
            personality_stage="BASE", peak_held_s=0.0,
        )
        self._finish_positioning(now)

    def _finish_positioning(self, now: float) -> None:
        readings = self._read_all(now)
        self._validate_feedback(readings)
        self._require_torque_enabled(readings)
        self._store_feedback(readings, now)
        # Some Servo firmwares keep the Moving register set even after the
        # measured position has reached and held Center. Keep the original
        # strict check, but also accept stable, in-tolerance position feedback.
        # Never consider torque or safe-range checks optional (see above).
        positions = {servo_id: int(readings[servo_id].position)
                     for servo_id in self.config.servos}
        inside_tolerance = all(
            abs(positions[servo_id] - servo.center) <= self.POSITION_TOLERANCE
            for servo_id, servo in self.config.servos.items()
        )
        moving_cleared = all(not readings[servo_id].moving
                             for servo_id in self.config.servos)
        if not inside_tolerance:
            self._center_stable_since = None
            self._center_stable_reference = None
        elif self._center_stable_reference is None or any(
            abs(positions[servo_id] - self._center_stable_reference[servo_id])
            > self.CENTER_STABLE_RAW for servo_id in self.config.servos
        ):
            self._center_stable_reference = positions
            self._center_stable_since = now

        stable_at_center = (
            inside_tolerance and self._center_stable_since is not None
            and now - self._center_stable_since >= self.CENTER_STABLE_SECONDS
        )
        if inside_tolerance and (moving_cleared or stable_at_center):
            self._motion_deadline = None
            self._center_stable_since = None
            self._center_stable_reference = None
            self._update_snapshot(
                timestamp=now, state=RuntimeState.ARMED,
                fault_text="", center_ready=True,
            )
        elif self._motion_deadline is not None and now >= self._motion_deadline:
            details = "; ".join(
                f"ID{servo_id} actual={positions[servo_id]} "
                f"center={servo.center} "
                f"delta={positions[servo_id] - servo.center:+d} "
                f"moving={int(bool(readings[servo_id].moving))}"
                for servo_id, servo in self.config.servos.items()
            )
            raise RuntimeError(f"center positioning timeout: {details}")

    def _monitor_armed(self, now: float) -> None:
        servo_ids = sorted(self.config.servos)
        servo_id = servo_ids[self._poll_id % len(servo_ids)]
        self._poll_id += 1
        feedback = self.bus.read_feedback(servo_id)
        self._checkpoint_emergency(now)
        self._validate_feedback({servo_id: feedback})
        self._require_torque_enabled({servo_id: feedback})
        self._store_feedback({servo_id: feedback}, now)

    def _enter_fault(self, now: float, message: str) -> None:
        self._camera_auto_enabled = False
        self._stop_face_search()
        self._drain_commands()
        errors = self._attempt_torque_off()
        combined = message if not errors else message + "; " + "; ".join(errors)
        self._update_snapshot(
            timestamp=now, state=RuntimeState.FAULT, fault_text=combined,
            torque_off_verified=not errors, center_ready=False,
        )

    def _attempt_torque_off(self) -> list[str]:
        errors: list[str] = []
        if not self._bus_open:
            return errors
        for servo_id in sorted(self.config.servos):
            try:
                self.bus.set_torque(servo_id, False)
            except Exception as exc:
                errors.append(f"ID {servo_id}: {exc}")
        readings: dict[int, Any] = {}
        for servo_id in sorted(self.config.servos):
            try:
                feedback = self.bus.read_feedback(servo_id)
                readings[servo_id] = feedback
                if feedback.torque_enabled:
                    errors.append(f"ID {servo_id}: torque still enabled")
            except Exception as exc:
                errors.append(f"ID {servo_id}: verify failed: {exc}")
        if readings:
            self._store_feedback(readings, self.latest_snapshot().timestamp)
        return errors

    def _close_bus(
        self, now: float, *, final_state: RuntimeState = RuntimeState.CLOSED
    ) -> None:
        if self._bus_open:
            self.bus.close()
            self._bus_open = False
        if final_state is RuntimeState.CLOSED:
            self._worker_stopped.set()
        self._update_snapshot(timestamp=now, state=final_state, center_ready=False)

    def _read_all(self, now: float | None = None) -> dict[int, Any]:
        readings: dict[int, Any] = {}
        for servo_id in sorted(self.config.servos):
            readings[servo_id] = self.bus.read_feedback(servo_id)
            if now is not None:
                self._checkpoint_emergency(now)
        return readings

    def _validate_feedback(self, readings: dict[int, Any]) -> None:
        for servo_id, feedback in readings.items():
            servo = self.config.servos[servo_id]
            if not servo.safe_min <= int(feedback.position) <= servo.safe_max:
                raise RuntimeError(
                    f"ID {servo_id} position {feedback.position} outside safe range "
                    f"{servo.safe_min}..{servo.safe_max}"
                )

    @staticmethod
    def _require_torque_enabled(readings: dict[int, Any]) -> None:
        disabled = [
            servo_id for servo_id, feedback in readings.items()
            if not bool(feedback.torque_enabled)
        ]
        if disabled:
            raise RuntimeError(
                "torque disabled unexpectedly for ID " + ", ".join(map(str, disabled))
            )

    def _store_feedback(self, readings: dict[int, Any], now: float) -> None:
        snapshot = self.latest_snapshot()
        raw = dict(snapshot.feedback_raw)
        speed = dict(snapshot.feedback_speed)
        moving = dict(snapshot.feedback_moving)
        torque = dict(snapshot.feedback_torque)
        for servo_id, item in readings.items():
            raw[servo_id] = int(item.position)
            speed[servo_id] = int(item.speed)
            moving[servo_id] = bool(item.moving)
            torque[servo_id] = bool(item.torque_enabled)
        self._update_snapshot(
            timestamp=now, feedback_raw=raw, feedback_speed=speed,
            feedback_moving=moving, feedback_torque=torque,
        )

    def _update_snapshot(self, **changes: Any) -> None:
        with self._snapshot_lock:
            self._snapshot = replace(self._snapshot, **changes)

    def _drain_commands(self) -> None:
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                return

    def _checkpoint_emergency(self, now: float) -> None:
        if not (self._emergency_pending.is_set() or self._emergency.is_set()):
            return
        self._process_emergency(now)
        raise _EmergencyAbort

    def _motion_transaction(self, now: float, operation: Any) -> None:
        should_abort = False
        with self._motion_gate:
            if self._emergency_pending.is_set() or self._emergency.is_set():
                should_abort = True
            else:
                operation()
        if should_abort or self._emergency_pending.is_set() or self._emergency.is_set():
            self._checkpoint_emergency(now)

    def _process_emergency(self, now: float) -> None:
        self._camera_auto_enabled = False
        self._stop_face_search()
        self._emergency_pending.clear()
        self._emergency.clear()
        self._drain_commands()
        errors = self._attempt_torque_off()
        if self._close_requested.is_set() and not errors:
            try:
                self._close_bus(now, final_state=RuntimeState.CLOSED)
                return
            except Exception as exc:
                errors.append(f"serial close failed: {exc}")
        if self._close_requested.is_set() and errors:
            self._emergency_pending.set()
            self._emergency.set()
        previous = self.latest_snapshot().fault_text
        fault_text = "; ".join(errors)
        if previous and previous not in fault_text:
            fault_text = previous + ("; " + fault_text if fault_text else "")
        self._update_snapshot(
            timestamp=now,
            state=RuntimeState.EMERGENCY,
            fault_text=fault_text,
            torque_off_verified=not errors,
            center_ready=False, camera_behavior="OFF", face_x=None, face_y=None,
            light={"color": "#000000", "brightness": 0.0},
        )


class _EmergencyAbort(Exception):
    pass
