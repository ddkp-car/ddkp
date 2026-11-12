import math
import unittest
from pathlib import Path

from fakes import FakeBus


APP_DIR = Path(__file__).resolve().parents[1]


def adapter_api(testcase: unittest.TestCase):
    try:
        from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
    except ImportError as exc:
        testcase.fail(f"main CPG adapter is not implemented: {exc}")
    return MainCpgAdapter


def observation(seq, emotion="happy", strength=0.9, detected=True):
    return {
        "schema": 1,
        "stream_id": "camera-a",
        "seq": seq,
        "detected": detected,
        "emotion": emotion,
        "strength": strength,
    }


class MainCpgAdapterTests(unittest.TestCase):
    def test_project_main_cpg_loads_without_ros(self):
        MainCpgAdapter = adapter_api(self)

        adapter = MainCpgAdapter(start_time=0.0)
        frame = adapter.step(0.0)

        self.assertEqual(frame.emotion, "Neutral")
        self.assertEqual(len(frame.q_deg), 4)
        self.assertTrue(all(math.isfinite(value) for value in frame.q_deg))

    def test_consistent_observations_drive_existing_hormone_cpg(self):
        MainCpgAdapter = adapter_api(self)
        adapter = MainCpgAdapter(start_time=0.0)
        adapter.observe(observation(0), 0.00)
        adapter.observe(observation(1), 0.15)
        adapter.observe(observation(2), 0.31)

        frame = adapter.step(0.36)

        self.assertTrue(adapter.ready)
        self.assertEqual(frame.emotion, "Happy")
        self.assertGreater(frame.hormones["Happy"], 0.0)
        self.assertEqual(len(frame.q_deg), 4)

    def test_stale_camera_input_returns_neutral(self):
        MainCpgAdapter = adapter_api(self)
        adapter = MainCpgAdapter(start_time=0.0)
        adapter.observe(observation(0), 0.00)
        adapter.observe(observation(1), 0.15)
        adapter.observe(observation(2), 0.31)
        frame = None
        for tick in (0.36, 0.41, 0.46, 0.51, 0.56, 0.61, 0.66, 0.71, 0.76, 0.81, 0.86):
            frame = adapter.step(tick)

        self.assertIsNotNone(frame)
        self.assertEqual(frame.emotion, "Neutral")
        self.assertEqual(frame.input_status, "input_timeout")
        self.assertFalse(adapter.ready)

    def test_mailbox_packet_makes_runtime_startable_without_manual_observe(self):
        MainCpgAdapter = adapter_api(self)
        from tools.robot_cpg_gui.camera import CameraMailbox
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand, RuntimeState

        mailbox = CameraMailbox()
        mailbox.publish(observation=observation(0), captured_at=0.01)
        adapter = MainCpgAdapter(start_time=0.0, mailbox=mailbox)
        config = load_runtime_config(APP_DIR / "robot_config.json")
        runtime = RobotRuntime(config, HardwareMapper(config), FakeBus(), adapter)
        runtime.submit(RuntimeCommand.CONNECT)
        runtime.process_once(0.02)
        runtime.submit(RuntimeCommand.ARM)
        runtime.process_once(0.03)
        runtime.submit(RuntimeCommand.CENTER)
        runtime.process_once(0.04)
        runtime.submit(RuntimeCommand.START_CPG)
        runtime.process_once(0.05)

        self.assertTrue(adapter.ready)
        self.assertEqual(runtime.latest_snapshot().state, RuntimeState.RUNNING)

    def test_stale_mailbox_capture_is_rejected_without_refreshing_ready(self):
        MainCpgAdapter = adapter_api(self)
        from tools.robot_cpg_gui.camera import CameraMailbox

        mailbox = CameraMailbox()
        mailbox.publish(observation=observation(0), captured_at=1.0)
        adapter = MainCpgAdapter(start_time=10.9, mailbox=mailbox)

        frame = adapter.step(11.0)

        self.assertFalse(adapter.ready)
        self.assertEqual(frame.emotion, "Neutral")
        self.assertIn("camera", frame.input_status)

    def test_camera_error_clears_readiness(self):
        MainCpgAdapter = adapter_api(self)
        from tools.robot_cpg_gui.camera import CameraMailbox

        mailbox = CameraMailbox()
        adapter = MainCpgAdapter(start_time=0.0, mailbox=mailbox)
        mailbox.publish(observation=observation(0), captured_at=0.0)
        adapter.step(0.1)
        self.assertTrue(adapter.ready)
        mailbox.publish(error="camera stopped", captured_at=0.2)

        adapter.step(0.2)

        self.assertFalse(adapter.ready)
        self.assertEqual(adapter.step(0.3).input_status, "camera_error")

    def test_capture_age_timeout_forces_neutral_even_if_receive_watchdog_is_fresh(self):
        MainCpgAdapter = adapter_api(self)
        from tools.robot_cpg_gui.camera import CameraMailbox

        mailbox = CameraMailbox()
        adapter = MainCpgAdapter(start_time=0.49, mailbox=mailbox)
        for seq, captured, received in (
            (0, 0.10, 0.49),
            (1, 0.25, 0.64),
            (2, 0.41, 0.80),
        ):
            mailbox.publish(observation=observation(seq), captured_at=captured)
            frame = adapter.step(received)
        active = adapter.step(0.85)
        self.assertEqual(active.emotion, "Happy")

        stale = adapter.step(0.92)

        self.assertFalse(adapter.ready)
        self.assertEqual(stale.emotion, "Neutral")
        self.assertIn("camera", stale.input_status)

    def test_observation_reaches_runtime_as_bounded_raw_targets(self):
        MainCpgAdapter = adapter_api(self)
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand

        config = load_runtime_config(APP_DIR / "robot_config.json")
        adapter = MainCpgAdapter(start_time=0.0)
        adapter.observe(observation(0), 0.00)
        adapter.observe(observation(1), 0.15)
        adapter.observe(observation(2), 0.31)
        bus = FakeBus()
        runtime = RobotRuntime(config, HardwareMapper(config), bus, adapter)
        runtime.submit(RuntimeCommand.CONNECT)
        runtime.process_once(0.32)
        runtime.submit(RuntimeCommand.ARM)
        runtime.process_once(0.33)
        runtime.submit(RuntimeCommand.START_CPG)
        runtime.process_once(0.36)

        targets = runtime.latest_snapshot().target_raw
        for servo_id, target in targets.items():
            self.assertLessEqual(abs(target - config.servos[servo_id].center), 5)


if __name__ == "__main__":
    unittest.main()
