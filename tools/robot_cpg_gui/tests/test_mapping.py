import json
import math
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]


def mapping_api(testcase: unittest.TestCase):
    try:
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
    except ImportError as exc:
        testcase.fail(f"mapping API is not implemented: {exc}")
    return load_runtime_config, HardwareMapper


class HardwareMappingTests(unittest.TestCase):
    # J2 direction is deliberately reversed per latest user request; others retain baseline.
    def test_loads_direct_drive_degree_mapping_defaults(self):
        load_runtime_config, _ = mapping_api(self)

        config = load_runtime_config(APP_DIR / "robot_config.json")

        self.assertEqual([config.servos[i].center for i in range(4)], [410, 566, 593, 413])
        self.assertEqual([config.servos[i].direction for i in range(4)], [-1, -1, -1, 1])
        self.assertEqual(config.max_offset_raw, 171)
        self.assertEqual(config.cpg_hardware_scale, 1.0)
        self.assertEqual(config.nominal_degrees_per_count, 180.0 / 1024.0)
        self.assertEqual(config.speed, 150)  # User baseline robot_cpg_gui(3).zip
        self.assertEqual(config.loop_hz, 20.0)

    def test_cpg_targets_follow_direct_drive_degrees_one_to_one(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        config = load_runtime_config(APP_DIR / "robot_config.json")
        mapper = HardwareMapper(config)

        modest = mapper.targets_from_cpg([100.0, 80.0, 95.0, 85.0])
        extreme = mapper.targets_from_cpg([120.0, 60.0, 120.0, 60.0])

        self.assertEqual(modest, {0: 353, 1: 623, 2: 565, 3: 385})
        self.assertEqual(extreme, {0: 239, 1: 737, 2: 422, 3: 242})

    def test_each_servo_matches_five_absolute_angle_checkpoints(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        config = load_runtime_config(APP_DIR / "robot_config.json")
        mapper = HardwareMapper(config)
        expected = {
            60.0: {0: 581, 1: 737, 2: 764, 3: 242},
            80.0: {0: 467, 1: 623, 2: 650, 3: 356},
            90.0: {0: 410, 1: 566, 2: 593, 3: 413},
            100.0: {0: 353, 1: 509, 2: 536, 3: 470},
            120.0: {0: 239, 1: 395, 2: 422, 3: 584},
        }

        for angle, expected_targets in expected.items():
            with self.subTest(angle=angle):
                targets = mapper.targets_from_cpg([angle] * 4)
                self.assertEqual(targets, expected_targets)
                for servo_id, raw in targets.items():
                    servo = config.servos[servo_id]
                    realized = (
                        config.cpg_center_deg
                        + servo.direction
                        * (raw - servo.center)
                        * config.nominal_degrees_per_count
                    )
                    self.assertLessEqual(
                        abs(realized - angle),
                        config.nominal_degrees_per_count / 2.0 + 1e-12,
                    )

    def test_cpg_targets_retain_independent_171_raw_envelope(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        config = load_runtime_config(APP_DIR / "robot_config.json")
        mapper = HardwareMapper(config)

        targets = mapper.targets_from_cpg([200.0, -20.0, 200.0, -20.0])

        self.assertEqual(targets, {0: 239, 1: 737, 2: 422, 3: 242})

    def test_safe_limits_are_applied_after_working_envelope(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        payload = json.loads((APP_DIR / "robot_config.json").read_text(encoding="utf-8"))
        payload["servos"][0].update({"center": 2, "safe_min": 0, "safe_max": 20})
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            mapper = HardwareMapper(load_runtime_config(path))

        self.assertEqual(mapper.targets_from_cpg([120.0, 90.0, 90.0, 90.0])[0], 0)

    def test_wide_j1_camera_pan_uses_only_calibrated_safe_range(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        config = load_runtime_config(APP_DIR / "robot_config.json")
        mapper = HardwareMapper(config)
        normal = mapper.targets_from_cpg([1000, 1000, 90, 90])
        wide = mapper.targets_from_cpg([1000, 1000, 90, 90], allow_extended_j1=True)
        self.assertEqual(normal[0], config.servos[0].center - config.max_offset_raw)
        self.assertEqual(wide[0], config.servos[0].safe_min)
        for servo_id in (1, 2, 3):
            self.assertEqual(wide[servo_id], normal[servo_id])
        opposite = mapper.targets_from_cpg([-1000, 90, 90, 90], allow_extended_j1=True)
        self.assertEqual(opposite[0], config.servos[0].safe_max)

    def test_camera_standby_j3_can_reach_minus45_offset_without_changing_cpg(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        config = load_runtime_config(APP_DIR / 'robot_config.json')
        mapper = HardwareMapper(config)
        q = (90.0, 90.0, 45.0, 90.0)
        self.assertEqual(mapper.targets_from_cpg(q)[2], 764)  # normal J3: 60°
        self.assertEqual(mapper.targets_from_cpg(q, allow_extended_j3=True)[2], 849)
        # Even in standby, an unreasonable target must respect stored raw limits.
        self.assertEqual(mapper.targets_from_cpg((90, 90, -999, 90),
                          allow_extended_j3=True)[2], config.servos[2].safe_max)
        self.assertEqual(mapper.targets_from_cpg(q, allow_extended_j3=True)[0],
                         config.servos[0].center)

    def test_non_finite_or_wrong_length_cpg_is_rejected(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        mapper = HardwareMapper(load_runtime_config(APP_DIR / "robot_config.json"))

        for bad in ([90.0] * 3, [90.0, 90.0, math.nan, 90.0], [90.0, math.inf, 90.0, 90.0]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    mapper.targets_from_cpg(bad)

    def test_feedback_is_reported_as_nominal_relative_degrees(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        mapper = HardwareMapper(load_runtime_config(APP_DIR / "robot_config.json"))

        angles = mapper.feedback_relative_deg({0: 407, 1: 569, 2: 596, 3: 410})

        self.assertAlmostEqual(angles[0], 0.5273, places=3)
        self.assertAlmostEqual(angles[1], -0.5273, places=3)
        self.assertAlmostEqual(angles[2], -0.5273, places=3)
        self.assertAlmostEqual(angles[3], -0.5273, places=3)

    def test_feedback_is_reported_as_absolute_joint_degrees(self):
        load_runtime_config, HardwareMapper = mapping_api(self)
        mapper = HardwareMapper(load_runtime_config(APP_DIR / "robot_config.json"))

        angles = mapper.feedback_absolute_deg({0: 407, 1: 569, 2: 596, 3: 410})

        self.assertAlmostEqual(angles[0], 90.5273, places=3)
        self.assertAlmostEqual(angles[1], 89.4727, places=3)
        self.assertAlmostEqual(angles[2], 89.4727, places=3)
        self.assertAlmostEqual(angles[3], 89.4727, places=3)


if __name__ == "__main__":
    unittest.main()
