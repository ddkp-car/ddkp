import unittest
import threading
import time
from pathlib import Path

from fakes import FakeBus, FakeCpgSource


APP_DIR = Path(__file__).resolve().parents[1]


def runtime_api(testcase: unittest.TestCase):
    try:
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand, RuntimeState
    except ImportError as exc:
        testcase.fail(f"runtime API is not implemented: {exc}")
    config = load_runtime_config(APP_DIR / "robot_config.json")
    return config, HardwareMapper(config), RobotRuntime, RuntimeCommand, RuntimeState


class RobotRuntimeTests(unittest.TestCase):
    def make_runtime(self, *, bus=None, source=None):
        config, mapper, RobotRuntime, RuntimeCommand, RuntimeState = runtime_api(self)
        bus = bus or FakeBus()
        source = source or FakeCpgSource()
        runtime = RobotRuntime(config, mapper, bus, source)
        return runtime, bus, source, RuntimeCommand, RuntimeState

    def connect_and_arm(self, runtime, commands):
        runtime.submit(commands.CONNECT)
        runtime.process_once(0.0)
        runtime.submit(commands.ARM)
        runtime.process_once(0.05)

    def center_after_arm(self, runtime, commands, now=0.10):
        runtime.submit(commands.CENTER)
        runtime.process_once(now)

    def test_connect_pings_all_ids_and_reads_safe_feedback(self):
        runtime, bus, _, commands, states = self.make_runtime()

        runtime.submit(commands.CONNECT)
        runtime.process_once(0.0)

        self.assertEqual(runtime.latest_snapshot().state, states.CONNECTED)
        self.assertEqual(
            [event for event in bus.events if event[0] == "ping"],
            [("ping", 0), ("ping", 1), ("ping", 2), ("ping", 3)],
        )
        self.assertEqual(runtime.latest_snapshot().feedback_raw, {0: 410, 1: 566, 2: 593, 3: 413})

    def test_arm_syncs_present_positions_before_enabling_torque(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)

        sync_index = next(i for i, event in enumerate(bus.events) if event[0] == "sync")
        torque_indices = [i for i, event in enumerate(bus.events) if event[:1] == ("torque",) and event[2]]
        self.assertTrue(torque_indices)
        self.assertLess(sync_index, min(torque_indices))
        self.assertEqual(runtime.latest_snapshot().state, states.ARMED)

    def test_start_requires_armed_state_and_ready_cpg(self):
        runtime, bus, source, commands, states = self.make_runtime(source=FakeCpgSource(ready=False))
        runtime.submit(commands.START_CPG)
        runtime.process_once(0.0)
        self.assertEqual(runtime.latest_snapshot().state, states.DISCONNECTED)
        self.assertFalse(any(event[0] == "sync" for event in bus.events))

        self.connect_and_arm(runtime, commands)
        self.center_after_arm(runtime, commands)
        runtime.submit(commands.START_CPG)
        runtime.process_once(0.15)
        self.assertEqual(runtime.latest_snapshot().state, states.ARMED)
        self.assertIn("camera", runtime.latest_snapshot().fault_text.lower())

    def test_start_requires_successful_center_after_every_arm(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)
        before = len([event for event in bus.events if event[0] == "sync"])

        runtime.submit(commands.START_CPG)
        runtime.process_once(0.10)

        self.assertEqual(runtime.latest_snapshot().state, states.ARMED)
        self.assertIn("center", runtime.latest_snapshot().fault_text.lower())
        self.assertFalse(runtime.latest_snapshot().center_ready)
        self.assertEqual(len([event for event in bus.events if event[0] == "sync"]), before)

        self.center_after_arm(runtime, commands, now=0.15)
        self.assertTrue(runtime.latest_snapshot().center_ready)
        self.assertEqual(runtime.latest_snapshot().fault_text, "")

    def test_running_maps_cpg_to_bounded_sync_write(self):
        runtime, bus, _, commands, states = self.make_runtime(
            source=FakeCpgSource(q_deg=[120.0, 60.0, 120.0, 60.0])
        )
        self.connect_and_arm(runtime, commands)
        self.center_after_arm(runtime, commands)
        runtime.submit(commands.START_CPG)
        runtime.process_once(0.15)

        self.assertEqual(runtime.latest_snapshot().state, states.RUNNING)
        self.assertFalse(runtime.latest_snapshot().center_ready)
        self.assertEqual(runtime.latest_snapshot().target_raw, {0: 239, 1: 737, 2: 422, 3: 242})
        self.assertEqual([e for e in bus.events if e[0] == "sync"][-1][1], {0: 239, 1: 737, 2: 422, 3: 242})

    def test_stop_returns_to_center_and_holds_armed(self):
        runtime, bus, _, commands, states = self.make_runtime(
            source=FakeCpgSource(q_deg=[120.0, 60.0, 120.0, 60.0])
        )
        self.connect_and_arm(runtime, commands)
        self.center_after_arm(runtime, commands)
        runtime.submit(commands.START_CPG)
        runtime.process_once(0.15)
        runtime.submit(commands.STOP_CPG)
        runtime.process_once(0.20)

        self.assertEqual([e for e in bus.events if e[0] == "sync"][-1][1], {0: 410, 1: 566, 2: 593, 3: 413})
        self.assertEqual(runtime.latest_snapshot().state, states.ARMED)
        self.assertTrue(runtime.latest_snapshot().center_ready)

    def test_emergency_wins_before_queued_start_and_next_write(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)
        sync_count = len([e for e in bus.events if e[0] == "sync"])
        runtime.submit(commands.START_CPG)
        runtime.request_emergency()
        runtime.process_once(0.10)

        self.assertEqual(runtime.latest_snapshot().state, states.EMERGENCY)
        self.assertEqual(len([e for e in bus.events if e[0] == "sync"]), sync_count)
        self.assertEqual(
            [e for e in bus.events if e[0] == "torque" and not e[2]][-4:],
            [("torque", 0, False), ("torque", 1, False), ("torque", 2, False), ("torque", 3, False)],
        )

    def test_emergency_raised_inside_cpg_step_prevents_sync_write(self):
        holder = {"armed": False}

        def emergency_when_running(_now):
            if holder["armed"]:
                holder["runtime"].request_emergency()

        source = FakeCpgSource(on_step=emergency_when_running)
        runtime, bus, _, commands, states = self.make_runtime(source=source)
        holder["runtime"] = runtime
        self.connect_and_arm(runtime, commands)
        holder["armed"] = True
        runtime.submit(commands.START_CPG)
        before = len([event for event in bus.events if event[0] == "sync"])

        runtime.process_once(0.10)

        self.assertEqual(runtime.latest_snapshot().state, states.EMERGENCY)
        self.assertEqual(len([event for event in bus.events if event[0] == "sync"]), before)

    def test_emergency_during_arm_read_prevents_sync_and_torque_on(self):
        runtime, bus, _, commands, states = self.make_runtime()
        runtime.submit(commands.CONNECT)
        runtime.process_once(0.0)
        bus.on_read = lambda _servo_id: runtime.request_emergency()
        before = len([event for event in bus.events if event[0] == "sync"])

        runtime.submit(commands.ARM)
        runtime.process_once(0.05)

        self.assertEqual(runtime.latest_snapshot().state, states.EMERGENCY)
        self.assertEqual(len([event for event in bus.events if event[0] == "sync"]), before)
        self.assertFalse(any(event[0] == "torque" and event[2] for event in bus.events))

    def test_emergency_during_torque_enable_stops_further_enables(self):
        runtime, bus, _, commands, states = self.make_runtime()
        runtime.submit(commands.CONNECT)
        runtime.process_once(0.0)
        bus.on_set_torque = lambda servo_id, enabled: (
            runtime.request_emergency() if enabled and servo_id == 0 else None
        )

        runtime.submit(commands.ARM)
        runtime.process_once(0.05)

        enabled_ids = [event[1] for event in bus.events if event[0] == "torque" and event[2]]
        self.assertEqual(enabled_ids, [0])
        self.assertEqual(runtime.latest_snapshot().state, states.EMERGENCY)

    def test_emergency_request_interlocks_with_active_motion_transaction(self):
        runtime, bus, _, commands, states = self.make_runtime(
            source=FakeCpgSource(q_deg=[120.0, 60.0, 120.0, 60.0])
        )
        self.connect_and_arm(runtime, commands)
        self.center_after_arm(runtime, commands)
        entered = threading.Event()
        release = threading.Event()
        request_returned = threading.Event()
        written_targets = []

        def hold_sync(targets, _speed):
            written_targets.append(dict(targets))
            entered.set()
            release.wait(1.0)

        bus.on_sync = hold_sync
        runtime.submit(commands.START_CPG)
        tick = threading.Thread(target=lambda: runtime.process_once(0.15))
        tick.start()
        self.assertTrue(entered.wait(0.5))
        requester = threading.Thread(
            target=lambda: (runtime.request_emergency(), request_returned.set())
        )
        requester.start()
        self.assertFalse(request_returned.wait(0.05))
        release.set()
        tick.join(1.0)
        requester.join(1.0)

        self.assertTrue(request_returned.is_set())
        self.assertEqual(runtime.latest_snapshot().state, states.EMERGENCY)
        self.assertEqual(written_targets, [{0: 239, 1: 737, 2: 422, 3: 242}])

    def test_emergency_during_center_sync_prevents_settled_ready_state(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)
        centers = {0: 410, 1: 566, 2: 593, 3: 413}

        def emergency_during_center(targets, _speed):
            if targets == centers:
                runtime.request_emergency()

        bus.on_sync = emergency_during_center
        runtime.submit(commands.CENTER)
        runtime.process_once(0.10)

        snapshot = runtime.latest_snapshot()
        self.assertEqual(snapshot.state, states.EMERGENCY)
        self.assertFalse(snapshot.center_ready)
        self.assertEqual(
            [event for event in bus.events if event[0] == "torque" and not event[2]][-4:],
            [("torque", 0, False), ("torque", 1, False),
             ("torque", 2, False), ("torque", 3, False)],
        )

    def test_serial_failure_latches_fault_and_attempts_torque_off(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)
        self.center_after_arm(runtime, commands)
        bus.fail_sync = True
        runtime.submit(commands.START_CPG)
        runtime.process_once(0.15)

        snapshot = runtime.latest_snapshot()
        self.assertEqual(snapshot.state, states.FAULT)
        self.assertIn("simulated sync failure", snapshot.fault_text)
        self.assertEqual(len([e for e in bus.events if e[0] == "torque" and not e[2]]), 4)

    def test_nonfinite_cpg_output_faults_without_writing_motion(self):
        source = FakeCpgSource()
        runtime, bus, _, commands, states = self.make_runtime(source=source)
        self.connect_and_arm(runtime, commands)
        before = len([event for event in bus.events if event[0] == "sync"])
        source.q_deg[1] = float("nan")

        runtime.submit(commands.START_CPG)
        runtime.process_once(0.10)

        self.assertEqual(runtime.latest_snapshot().state, states.FAULT)
        self.assertEqual(len([event for event in bus.events if event[0] == "sync"]), before)

    def test_latched_cpg_fault_does_not_repeat_or_starve_disconnect(self):
        source = FakeCpgSource()
        runtime, bus, _, commands, states = self.make_runtime(source=source)
        self.connect_and_arm(runtime, commands)
        source.q_deg[0] = float("nan")
        runtime.process_once(0.10)
        off_count = len(
            [event for event in bus.events if event[0] == "torque" and not event[2]]
        )

        runtime.process_once(0.15)
        self.assertEqual(
            len([event for event in bus.events if event[0] == "torque" and not event[2]]),
            off_count,
        )
        runtime.submit(commands.DISCONNECT)
        runtime.process_once(0.20)

        self.assertEqual(runtime.latest_snapshot().state, states.DISCONNECTED)
        self.assertEqual(bus.events[-1], ("close",))

    def test_incomplete_emergency_torque_off_remains_visible(self):
        bus = FakeBus()
        bus.fail_torque_off_ids.add(2)
        runtime, _, _, commands, states = self.make_runtime(bus=bus)
        self.connect_and_arm(runtime, commands)
        runtime.request_emergency()
        runtime.process_once(0.10)

        snapshot = runtime.latest_snapshot()
        self.assertEqual(snapshot.state, states.EMERGENCY)
        self.assertIn("ID 2", snapshot.fault_text)
        self.assertFalse(snapshot.torque_off_verified)

    def test_close_uses_emergency_path_then_closes_bus(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)

        self.assertTrue(runtime.close())

        self.assertEqual(runtime.latest_snapshot().state, states.CLOSED)
        self.assertEqual(bus.events[-1], ("close",))
        self.assertEqual(len([e for e in bus.events if e[0] == "torque" and not e[2]]), 4)

    def test_close_waits_for_blocked_tick_and_never_writes_after_request(self):
        entered = threading.Event()
        release = threading.Event()

        def block_step(_now):
            entered.set()
            release.wait(1.0)

        runtime, bus, _, commands, states = self.make_runtime(
            source=FakeCpgSource(on_step=block_step)
        )
        runtime.start()
        runtime.submit(commands.CONNECT)
        self.assertTrue(entered.wait(0.5))
        before = len([event for event in bus.events if event[0] == "sync"])

        self.assertFalse(runtime.close(timeout=0.01))
        self.assertFalse(runtime.is_stopped)
        release.set()
        self.assertTrue(runtime.close(timeout=1.0))

        self.assertEqual(runtime.latest_snapshot().state, states.CLOSED)
        self.assertEqual(len([event for event in bus.events if event[0] == "sync"]), before)

    def test_close_failure_is_visible_and_retried(self):
        bus = FakeBus()
        runtime, _, _, commands, states = self.make_runtime(bus=bus)
        self.connect_and_arm(runtime, commands)
        bus.fail_close = True

        self.assertFalse(runtime.close(timeout=0.0))
        snapshot = runtime.latest_snapshot()
        self.assertEqual(snapshot.state, states.EMERGENCY)
        self.assertIn("close", snapshot.fault_text.lower())
        bus.fail_close = False
        self.assertTrue(runtime.close(timeout=0.0))
        self.assertEqual(runtime.latest_snapshot().state, states.CLOSED)

    def test_disconnect_keeps_runtime_available_for_reconnect(self):
        runtime, bus, _, commands, states = self.make_runtime()
        runtime.start()

        def wait_for(expected):
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if runtime.latest_snapshot().state is expected:
                    return True
                time.sleep(0.005)
            return False

        runtime.submit(commands.CONNECT)
        self.assertTrue(wait_for(states.CONNECTED))
        runtime.submit(commands.DISCONNECT)
        self.assertTrue(wait_for(states.DISCONNECTED))
        self.assertFalse(runtime.is_stopped)
        runtime.submit(commands.CONNECT)
        self.assertTrue(wait_for(states.CONNECTED))
        self.assertTrue(runtime.close(timeout=1.0))

    def test_center_is_supervised_and_stall_faults_with_torque_off(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)
        bus.hold_positions = True
        bus.feedback[0] = type(bus.feedback[0])(
            position=500, speed=0, moving=True, torque_enabled=True
        )

        runtime.submit(commands.CENTER)
        runtime.process_once(0.10)
        self.assertEqual(runtime.latest_snapshot().state, states.CENTERING)
        runtime.process_once(6.0)

        self.assertEqual(runtime.latest_snapshot().state, states.FAULT)
        self.assertIn("timeout", runtime.latest_snapshot().fault_text.lower())
        self.assertEqual(len([e for e in bus.events if e[0] == "torque" and not e[2]]), 4)

    def test_latched_fault_reason_survives_rejected_commands(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)
        self.center_after_arm(runtime, commands)
        bus.fail_sync = True
        runtime.submit(commands.START_CPG)
        runtime.process_once(0.15)
        original = runtime.latest_snapshot().fault_text

        runtime.submit(commands.ARM)
        runtime.process_once(0.20)

        self.assertEqual(runtime.latest_snapshot().state, states.FAULT)
        self.assertEqual(runtime.latest_snapshot().fault_text, original)

    def test_armed_monitor_faults_if_any_servo_loses_torque(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)
        old = bus.feedback[1]
        bus.feedback[1] = type(old)(
            position=old.position, speed=old.speed, moving=old.moving,
            torque_enabled=False,
        )

        runtime.process_once(0.10)

        self.assertEqual(runtime.latest_snapshot().state, states.FAULT)
        self.assertIn("torque", runtime.latest_snapshot().fault_text.lower())

    def test_start_rechecks_all_torque_before_first_cpg_write(self):
        runtime, bus, _, commands, states = self.make_runtime()
        self.connect_and_arm(runtime, commands)
        self.center_after_arm(runtime, commands)
        old = bus.feedback[2]
        bus.feedback[2] = type(old)(
            position=old.position, speed=old.speed, moving=old.moving,
            torque_enabled=False,
        )
        before = len([event for event in bus.events if event[0] == "sync"])

        runtime.submit(commands.START_CPG)
        runtime.process_once(0.15)

        self.assertEqual(runtime.latest_snapshot().state, states.FAULT)
        self.assertEqual(len([event for event in bus.events if event[0] == "sync"]), before)

    def test_failed_emergency_torque_off_cannot_be_cleared_by_reconnect(self):
        bus = FakeBus()
        bus.fail_torque_off_ids.add(2)
        runtime, _, _, commands, states = self.make_runtime(bus=bus)
        self.connect_and_arm(runtime, commands)
        runtime.request_emergency()
        runtime.process_once(0.10)
        original = runtime.latest_snapshot().fault_text

        runtime.submit(commands.CONNECT)
        runtime.process_once(0.15)

        self.assertEqual(runtime.latest_snapshot().state, states.EMERGENCY)
        self.assertIn("ID 2", runtime.latest_snapshot().fault_text)
        self.assertIn(original, runtime.latest_snapshot().fault_text)


if __name__ == "__main__":
    unittest.main()
