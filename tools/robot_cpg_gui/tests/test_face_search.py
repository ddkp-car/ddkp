"""Search/track/standby behavior and motor safety regression tests (mock hardware)."""
import math
import unittest
from pathlib import Path
from dataclasses import replace

from fakes import FakeBus
from tools.robot_cpg_gui.camera import CameraMailbox
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.face_search import (
    FaceSearchController, FaceSearchSettings, joint_reachable_limits,
    load_face_search_settings,
)
from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand, RuntimeState

DIR = Path(__file__).resolve().parents[1]


class FaceSearchTests(unittest.TestCase):
    def setUp(self):
        self.config = load_runtime_config(DIR / 'robot_config.json')
        self.mailbox = CameraMailbox()
        self.settings = load_face_search_settings()
        self.controller = FaceSearchController(self.config, self.mailbox, settings=self.settings)

    def push(self, t, face=False, x=0.5):
        self.mailbox.publish(captured_at=t, observation={
            'detected': face, 'emotion': 'neutral', 'strength': 0.0,
            'face_x': x if face else None,
        })

    def test_never_exceeds_baseline_safe_hardware_envelope(self):
        limits = joint_reachable_limits(self.config)
        self.assertGreaterEqual(limits[0][0], 59.0)
        self.assertLessEqual(limits[0][1], 121.0)
        self.assertEqual(self.settings.standby_offsets_deg, (20, -45, 30))
        # J3 standby is nominal 45°, using only its CALIBRATED safe raw
        # envelope; ordinary 60–120° CPG range is unchanged.
        pose = self.controller.standby_pose
        self.assertEqual(tuple(round(v) for v in pose), (90, 110, 45, 120))
        self.assertGreaterEqual(pose[2], joint_reachable_limits(self.config, extended_j3=True)[2][0])
        self.assertTrue(self.controller.limits[2][0] <= pose[2] <= self.controller.limits[2][1])
        for i in (0, 1, 3):
            self.assertLessEqual(pose[i], self.controller.limits[i][1])
            self.assertGreaterEqual(pose[i], self.controller.limits[i][0])

    def test_standby_j3_moves_to_45_degrees_with_slew(self):
        self.controller.start(0)
        self.push(0.05)
        last = 90.0
        for tick in range(1, 70):
            t = 0.05 * tick
            self.push(t)
            result = self.controller.step(t, (90.0,) * 4)
            self.assertGreaterEqual(result.angles[2], 45.0)
            self.assertLessEqual(abs(result.angles[2] - last), 1.000001)
            last = result.angles[2]
        self.assertAlmostEqual(last, 45.0)
        mapper = HardwareMapper(self.config)
        target = mapper.targets_from_cpg(result.angles, allow_extended_j1=True,
                                         allow_extended_j3=True)
        self.assertAlmostEqual(target[2], 849, delta=1)
        # Normal CPG still clamps J3 to 60° unless camera standby asks for 45°.
        normal = mapper.targets_from_cpg(result.angles, allow_extended_j1=True)
        self.assertLess(normal[2], target[2])

    def test_standby_j3_never_exceeds_existing_raw_safe_max(self):
        narrower = dict(self.config.servos)
        narrower[2] = replace(narrower[2], safe_max=780)
        restricted = replace(self.config, servos=narrower)
        finder = FaceSearchController(restricted, self.mailbox, self.settings)
        self.assertAlmostEqual(finder.standby_pose[2], 90-(780-593)*180/1024)
        result = HardwareMapper(restricted).targets_from_cpg(
            finder.standby_pose, allow_extended_j1=True, allow_extended_j3=True
        )
        self.assertEqual(result[2], 780)

    def test_camera_pan_uses_entire_existing_safe_range_without_touching_cpg(self):
        limited = joint_reachable_limits(self.config)
        wide = joint_reachable_limits(self.config, extended_j1=True)
        self.assertLessEqual(limited[0][1] - limited[0][0], 60.2)
        self.assertAlmostEqual(wide[0][1] - wide[0][0], 870 * 180 / 1024, places=4)
        self.assertEqual(limited[1:], wide[1:])
        self.assertAlmostEqual(self.controller._scan_min, wide[0][0])
        self.assertAlmostEqual(self.controller._scan_max, wide[0][1])

    def test_full_180_calibrated_envelope_would_be_used_without_changing_code(self):
        # A certified safe_max=1023 is required to unlock the last 27 degrees.
        channels = dict(self.config.servos)
        channels[0] = replace(channels[0], safe_max=1023)
        extended_config = replace(self.config, servos=channels)
        finder = FaceSearchController(extended_config, self.mailbox, settings=self.settings)
        self.assertAlmostEqual(finder._scan_max - finder._scan_min,
                               1023 * 180 / 1024, places=4)
        self.assertLessEqual(finder._scan_max - finder._scan_min, 180)

    def test_camera_must_publish_after_start_and_never_moves_without_frames(self):
        self.push(0, face=False)
        self.controller.start(0.1)
        output = self.controller.step(0.15, (90, 90, 90, 90))
        self.assertEqual(output.behavior, 'WAIT_CAMERA')
        self.assertEqual(output.angles, (90, 90, 90, 90))
        self.push(0.2, face=False)
        output = self.controller.step(0.2, (90, 90, 90, 90))
        self.assertEqual(output.behavior, 'SEARCHING')

    def test_scans_both_limits_then_waits_20s_after_standby_reached(self):
        self.controller.start(0)
        self.push(0.05)
        state = []
        pans = []
        for tick in range(1, 2500):
            t = tick * 0.05
            self.push(t)
            # Servo follows last setpoint, as in FakeBus.
            previous = tuple(self.controller._pose)
            output = self.controller.step(t, (90, 90, 90, 90), {j: previous[j] for j in range(4)})
            state.append(output.behavior)
            pans.append(output.angles[0])
            if output.behavior == 'STANDBY' and self.controller._standby_settled_at is not None:
                break
        self.assertIn('SEARCHING', state)
        self.assertLessEqual(min(pans), self.controller._scan_min + 0.1)
        self.assertGreaterEqual(max(pans), self.controller._scan_max - 0.1)
        self.assertGreater(self.controller._scan_max - self.controller._scan_min, 150)
        self.assertEqual(output.behavior, 'STANDBY')
        settled_at = self.controller._standby_settled_at
        self.assertIsNotNone(settled_at)
        self.push(settled_at + 19.9)
        output = self.controller.step(settled_at + 19.9, (90, 90, 90, 90))
        self.assertEqual(output.behavior, 'STANDBY')
        self.push(settled_at + 20.1)
        output = self.controller.step(settled_at + 20.1, (90, 90, 90, 90))
        self.assertEqual(output.behavior, 'SEARCHING')

    def test_three_detected_frames_acquire_even_neutral_and_pan_by_face_x(self):
        self.controller.start(0)
        for i in range(1, 4):
            t = i * 0.05
            self.push(t, face=True, x=0.85)
            result = self.controller.step(t, (92, 95, 75, 103))
        self.assertEqual(result.behavior, 'TRACKING')
        self.assertTrue(result.just_acquired)
        last_pan = result.angles[0]
        self.push(0.20, face=True, x=0.85)
        result = self.controller.step(0.20, (92, 95, 75, 103))
        self.assertLess(result.angles[0], last_pan)
        self.assertTrue(all(60 <= j <= 120 for j in result.angles))

    def test_face_lost_enters_standby_and_camera_timeout_stops_search(self):
        self.controller.start(0)
        for i in range(1, 4):
            self.push(i * 0.05, face=True, x=0.4)
            self.controller.step(i * 0.05, (90,) * 4)
        self.assertEqual(self.controller.behavior, 'TRACKING')
        for i in range(4, 32):
            t = i * 0.05
            self.push(t, face=False)
            result = self.controller.step(t, (90,) * 4)
        self.assertEqual(result.behavior, 'PLAYBACK')
        result = self.controller.step(t + 1.1, (90,) * 4)
        self.assertEqual(result.camera_fault, 'camera_timeout')

    def test_scan_refuses_unreachable_endpoint(self):
        self.controller.start(0)
        failure = None
        for tick in range(1, 600):
            t = tick * 0.05
            self.push(t)
            # Simulated jam: actual J1 remains at center even after the
            # target reaches the requested endpoint.
            result = self.controller.step(t, (90,) * 4, {0: 90})
            if result.camera_fault:
                failure = result.camera_fault
                break
        self.assertEqual(failure, 'scan_endpoint_not_reached')


class RuntimeSearchTests(unittest.TestCase):
    def setUp(self):
        self.config = load_runtime_config(DIR / 'robot_config.json')
        self.mailbox = CameraMailbox()
        self.mapper = HardwareMapper(self.config)
        self.bus = FakeBus()
        self.adapter = MainCpgAdapter(start_time=0, mailbox=self.mailbox,
                                      profile_path=DIR / 'cpg_profile.json',
                                      loop_hz=self.config.loop_hz)
        self.finder = FaceSearchController(self.config, self.mailbox)
        self.runtime = RobotRuntime(self.config, self.mapper, self.bus, self.adapter,
                                    face_search=self.finder)

    def command(self, cmd, t):
        self.runtime.submit(cmd)
        self.runtime.process_once(t)

    def setup_center(self):
        self.command(RuntimeCommand.CONNECT, 0)
        self.command(RuntimeCommand.ARM, 0.05)
        self.command(RuntimeCommand.CENTER, 0.10)
        self.assertTrue(self.runtime.latest_snapshot().center_ready)

    def publish(self, t, detected=False, x=0.5):
        self.mailbox.publish(captured_at=t, observation={
            'schema': 1, 'stream_id': 'test', 'seq': int(t * 1000),
            'detected': detected, 'emotion': 'neutral', 'strength': 0.0,
            'face_x': x if detected else None,
        })

    def test_camera_j3_zero_offset_and_manual_keeps_tracking_enabled(self):
        self.setup_center()
        self.command(RuntimeCommand.CAMERA_AUTO_ON, 0.2)
        self.publish(0.3)
        self.runtime.process_once(0.35)
        for tick in range(8, 90):
            t = tick * 0.05
            self.publish(t)
            self.runtime.process_once(t + 0.001)
        searching = self.runtime.latest_snapshot()
        self.assertEqual(searching.camera_behavior, 'SEARCHING')
        self.assertAlmostEqual(searching.cpg_deg[2], 45.0)
        self.assertEqual(searching.target_raw[2], 849)
        self.assertEqual(self.bus.feedback[2].position, 849)
        self.adapter.select_manual_mode('Happy')
        self.runtime.process_once(4.55)
        switched = self.runtime.latest_snapshot()
        self.assertEqual(switched.camera_behavior, 'SEARCHING')
        self.assertLessEqual(abs(switched.target_raw[2] - 849), 8)
        last = switched.target_raw[2]
        for tick in range(92, 160):
            t = tick * 0.05
            self.publish(t)  # camera stream alive; no face, so keep searching
            self.runtime.process_once(t + 0.001)
            current = self.runtime.latest_snapshot().target_raw[2]
            self.assertLessEqual(abs(current - last), 8)
            last = current
        self.assertEqual(last, 849)  # new J3 standby 45°, still searching

    def test_start_camera_without_center_does_not_move_servo(self):
        self.command(RuntimeCommand.CAMERA_AUTO_ON, 0)
        self.publish(0.01)
        self.runtime.process_once(0.02)
        self.assertEqual(self.runtime.latest_snapshot().state, RuntimeState.DISCONNECTED)
        self.assertFalse(any(e[0] == 'sync' for e in self.bus.events))
        self.command(RuntimeCommand.CONNECT, 0.03)
        self.command(RuntimeCommand.ARM, 0.04)
        self.publish(0.05)
        self.runtime.process_once(0.06)
        self.assertEqual(self.runtime.latest_snapshot().state, RuntimeState.ARMED)
        self.assertFalse(self.runtime.latest_snapshot().center_ready)

    def test_camera_auto_start_after_center_and_live_frame(self):
        self.setup_center()
        self.command(RuntimeCommand.CAMERA_AUTO_ON, 0.2)
        self.runtime.process_once(0.25)
        self.assertEqual(self.runtime.latest_snapshot().state, RuntimeState.ARMED)
        self.publish(0.3)
        self.runtime.process_once(0.35)
        self.assertEqual(self.runtime.latest_snapshot().state, RuntimeState.RUNNING)
        self.publish(0.40)
        self.runtime.process_once(0.41)
        self.assertEqual(self.runtime.latest_snapshot().camera_behavior, 'SEARCHING')
        self.assertEqual(self.bus.events[-1][0], 'sync')

    def test_numpad_manual_preempts_search_while_retaining_cpg(self):
        self.setup_center()
        self.command(RuntimeCommand.CAMERA_AUTO_ON, 0.2)
        self.publish(0.3)
        self.runtime.process_once(0.35)
        self.publish(0.40)
        self.runtime.process_once(0.41)
        self.adapter.select_manual_mode('Happy')
        self.runtime.process_once(0.46)
        self.assertEqual(self.runtime.latest_snapshot().camera_behavior, 'SEARCHING')
        self.adapter.select_camera_auto()
        self.publish(0.50)
        self.runtime.process_once(0.51)
        self.assertEqual(self.runtime.latest_snapshot().camera_behavior, 'SEARCHING')

    def test_switch_numpad_during_wide_scan_has_no_j1_jump(self):
        self.setup_center()
        self.command(RuntimeCommand.CAMERA_AUTO_ON, 0.2)
        self.publish(0.3)
        self.runtime.process_once(0.35)
        for tick in range(8, 76):
            t = tick * 0.05
            self.publish(t)
            self.runtime.process_once(t + 0.001)
        original = self.runtime.latest_snapshot()
        self.assertEqual(original.camera_behavior, 'SEARCHING')
        self.assertLess(original.cpg_deg[0], 60.0)
        raw_before = original.target_raw[0]
        # Manual personality must return with the existing 20deg/s ramp
        # instead of being snapped into the normal +/-171 raw envelope.
        self.adapter.select_manual_mode('Happy')
        self.runtime.process_once(3.85)
        after = self.runtime.latest_snapshot()
        self.assertEqual(after.camera_behavior, 'SEARCHING')
        self.assertLessEqual(abs(after.target_raw[0] - raw_before), 8)

    def test_camera_stop_returns_center_without_restarting(self):
        self.setup_center()
        self.command(RuntimeCommand.CAMERA_AUTO_ON, 0.2)
        self.publish(0.3)
        self.runtime.process_once(0.35)
        self.command(RuntimeCommand.CAMERA_AUTO_OFF, 0.4)
        self.assertEqual(self.runtime.latest_snapshot().state, RuntimeState.ARMED)
        self.runtime.process_once(0.45)
        self.assertEqual(self.runtime.latest_snapshot().state, RuntimeState.ARMED)
        self.assertEqual(self.runtime.latest_snapshot().camera_behavior, 'OFF')

    def test_emergency_beats_scanning_write_and_disables_torque(self):
        self.setup_center()
        self.command(RuntimeCommand.CAMERA_AUTO_ON, 0.2)
        self.publish(0.3)
        self.runtime.process_once(0.35)
        before = len([event for event in self.bus.events if event[0] == 'sync'])
        self.runtime.request_emergency()
        self.publish(0.40)
        self.runtime.process_once(0.41)
        self.assertEqual(self.runtime.latest_snapshot().state, RuntimeState.EMERGENCY)
        self.assertEqual(len([e for e in self.bus.events if e[0] == 'sync']), before)
        self.assertEqual(self.runtime.latest_snapshot().camera_behavior, 'OFF')


if __name__ == '__main__':
    unittest.main()
