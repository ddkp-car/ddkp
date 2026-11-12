"""Regression tests for the exact user-supplied SO(2) and decoder equations."""
import copy
import math
import unittest

from tools.robot_cpg_gui.cpg_engine import LampController, so2_step
from tools.robot_cpg_gui.cpg_profile import pasted_defaults


class SuppliedEquationTests(unittest.TestCase):
    def test_so2_uses_previous_o1_and_o2_and_user_signs(self):
        o1, o2, alpha, phi = 0.35, -0.27, 1.10, 0.33
        expected_o1 = math.tanh(alpha * (math.cos(phi) * o1 + math.sin(phi) * o2))
        expected_o2 = math.tanh(alpha * (-math.sin(phi) * o1 + math.cos(phi) * o2))
        actual = so2_step(o1, o2, alpha, phi)
        self.assertAlmostEqual(actual[0], expected_o1, places=13)
        self.assertAlmostEqual(actual[1], expected_o2, places=13)
        # A sign swap on either rotation term is an observable regression.
        old_first = math.tanh(alpha * (math.cos(phi) * o1 - math.sin(phi) * o2))
        self.assertNotAlmostEqual(actual[0], old_first, places=5)

    def test_initial_seed_and_neutral_warmup_match_simulation(self):
        p = pasted_defaults()
        c = LampController(start_time=0.0, profile=p)
        o1, o2 = 0.10, 0.00
        for _ in range(200):
            o1, o2 = so2_step(o1, o2, p["global"]["so2_alpha"], p["modes"]["Neutral"]["phi"])
        self.assertAlmostEqual(c._osc[0], o1)
        self.assertAlmostEqual(c._osc[1], o2)
        self.assertEqual(c._hormones, {"Happy": 0.0, "Sad": 0.0, "Confused": 0.0})

    def test_four_joint_decoder_uses_o1_o2_minus_o1_o2(self):
        p = copy.deepcopy(pasted_defaults())
        p["global"]["warmup_steps"] = 0
        p["modes"]["Neutral"]["poses"] = [
            {"time": t, "angles": [90., 90., 90., 90.]}
            for t in (0, 1, 5)
        ]
        p["modes"]["Neutral"]["gains"] = [2., 1., 2., 1.]
        c = LampController(start_time=0, profile=p)
        c._so2_step = lambda phase: (0.20, -0.60)
        q = c.step(1.0)["q_deg"]
        # 1 s fade complete; each diff < 1 degree/tick slew bound.
        self.assertEqual(tuple(round(v, 8) for v in q), (90.4, 89.4, 89.6, 89.4))

    def test_neutral_oscillation_fades_in_even_without_detected_emotion(self):
        p = pasted_defaults()
        p["global"]["warmup_steps"] = 0
        p["modes"]["Neutral"]["poses"] = [{"time": 0, "angles": [90] * 4}]
        c = LampController(start_time=0, profile=p)
        c._so2_step = lambda phase: (0.25, -0.25)
        self.assertEqual(c.step(0.0)["q_deg"], (90.,) * 4)
        self.assertNotEqual(c.step(1.0)["q_deg"], (90.,) * 4)

    def test_forward_euler_hormones_use_correct_up_and_down_tau(self):
        p = pasted_defaults()
        p["global"]["warmup_steps"] = 0
        c = LampController(start_time=0, profile=p)
        c.gate.current = "Happy"
        first = c.step(.05)
        self.assertAlmostEqual(first["hormones"]["Happy"], 0.1)  # dt/.5
        c.gate.current = "Sad"
        second = c.step(.10)
        self.assertAlmostEqual(second["hormones"]["Happy"], .1 + .05 * (0. - .1) / 1.5)
        self.assertAlmostEqual(second["hormones"]["Sad"], .05 / 2.)

    def test_head_fade_removes_entire_mixed_j4_oscillation_by_end(self):
        p = pasted_defaults()
        p["global"]["warmup_steps"] = 0
        c = LampController(start_time=0.0, profile=p)
        c.gate.current = "Sad"
        c._mode = "Sad"
        c._mode_onset["Sad"] = 0.0
        c._hormones = {"Happy": .2, "Sad": .8, "Confused": 0.0}
        c._so2_step = lambda phi: (0.2, .8)
        c._last_time = 10.0
        c._last_command = [80., 80., 60., 69.]  # near mixed J4 center
        out = c.step(10.0)
        # Mixed center = .2*105 + .8*60 = 69; j4 oscillation fades to zero
        # despite the still-positive Happy hormone.
        self.assertAlmostEqual(out["q_deg"][3], 69.0, places=12)

    def test_residual_hormone_keeps_old_emotion_keyframe_timeline(self):
        p = pasted_defaults()
        p["global"]["warmup_steps"] = 0
        for emotion in ("Happy", "Confused"):
            p["modes"][emotion]["gains"] = [0] * 4
        c = LampController(start_time=0.0, profile=p)
        c._so2_step = lambda phi: (0., 0.)
        c._hormones = {"Happy": .5, "Sad": 0., "Confused": .5}
        c._mode = "Confused"
        c.gate.current = "Confused"
        c._mode_onset["Happy"] = 0.0
        c._mode_onset["Confused"] = 3.0
        c._last_time = 4.0
        c._last_command = [90.] * 4
        c.step(4.0)
        self.assertAlmostEqual(c._mode_onset["Happy"], 0.0)
        self.assertAlmostEqual(c._mode_onset["Confused"], 3.0)
        self.assertTrue(all(0 <= h <= 1 for h in c._hormones.values()))


if __name__ == "__main__":
    unittest.main()
