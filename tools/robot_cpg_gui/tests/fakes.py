from __future__ import annotations

from types import SimpleNamespace

from tools.servo_calibration_gui.bus import BusError, ServoFeedback


class FakeBus:
    def __init__(self, positions=None):
        initial = positions or {0: 410, 1: 566, 2: 593, 3: 413}
        self.feedback = {
            servo_id: ServoFeedback(position=position, speed=0, moving=False, torque_enabled=False)
            for servo_id, position in initial.items()
        }
        self.events = []
        self.opened = False
        self.missing = set()
        self.fail_sync = False
        self.fail_close = False
        self.fail_torque_off_ids = set()
        self.hold_positions = False
        self.on_read = None
        self.on_set_torque = None
        self.on_sync = None

    def open(self):
        self.opened = True
        self.events.append(("open",))

    def close(self):
        self.events.append(("close",))
        if self.fail_close:
            raise BusError("simulated close failure")
        self.opened = False

    def ping(self, servo_id):
        self.events.append(("ping", servo_id))
        return servo_id not in self.missing

    def read_feedback(self, servo_id):
        self.events.append(("read", servo_id))
        if self.on_read is not None:
            self.on_read(servo_id)
        return self.feedback[servo_id]

    def sync_write_positions(self, targets, speed):
        self.events.append(("sync", dict(targets), speed))
        if self.on_sync is not None:
            self.on_sync(dict(targets), speed)
        if self.fail_sync:
            self.fail_sync = False
            raise BusError("simulated sync failure")
        for servo_id, target in targets.items():
            old = self.feedback[servo_id]
            self.feedback[servo_id] = ServoFeedback(
                position=old.position if self.hold_positions else int(target),
                speed=int(speed), moving=self.hold_positions,
                torque_enabled=old.torque_enabled,
            )

    def set_torque(self, servo_id, enabled):
        self.events.append(("torque", servo_id, bool(enabled)))
        if self.on_set_torque is not None:
            self.on_set_torque(servo_id, bool(enabled))
        if not enabled and servo_id in self.fail_torque_off_ids:
            raise BusError(f"simulated torque-off failure ID {servo_id}")
        old = self.feedback[servo_id]
        self.feedback[servo_id] = ServoFeedback(
            position=old.position, speed=old.speed, moving=old.moving,
            torque_enabled=bool(enabled),
        )


class FakeCpgSource:
    def __init__(self, *, ready=True, q_deg=None, on_step=None):
        self.ready = ready
        self.q_deg = list(q_deg or [90.0, 90.0, 90.0, 90.0])
        self.on_step = on_step

    def step(self, now):
        if self.on_step is not None:
            self.on_step(now)
        return SimpleNamespace(
            q_deg=self.q_deg.copy(),
            hormones={"Happy": 0.2, "Sad": 0.0, "Confused": 0.0},
            emotion="Happy",
            input_status="accepted",
        )
