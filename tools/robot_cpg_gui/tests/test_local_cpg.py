"""Regression coverage for the editable local CPG and save/apply safety."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from fakes import FakeBus

from tools.robot_cpg_gui.cpg_profile import (
    EMOTIONS, MODES, load_profile, pasted_defaults, save_profile_atomic, validate_profile,
)
from tools.robot_cpg_gui.cpg_engine import LampController, lerp_keyframes
from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
from tools.robot_cpg_gui.camera import CameraMailbox
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand, RuntimeState

APP_DIR = Path(__file__).resolve().parents[1]


def happy(seq, timestamp):
    return {"schema": 1, "stream_id": "camera-1", "seq": seq,
            "detected": True, "emotion": "happy", "strength": .92}


class LocalCpgTests(unittest.TestCase):
    def test_default_profile_matches_design_keyframes(self):
        p = pasted_defaults()
        validate_profile(p)
        self.assertEqual(p["global"]["so2_alpha"], 1.10)
        self.assertEqual(p["modes"]["Confused"]["gains"], [20, 2, 3, 5])
        self.assertEqual(p["modes"]["Happy"]["poses"][-1]["angles"], [80, 100, 75, 105])
        self.assertEqual(p["modes"]["Sad"]["head_fade"], {"start": 5, "end": 10})
        self.assertEqual(p["modes"]["Neutral"]["light_color"], "#ffffff")
        self.assertEqual({k: p["hormones"][k]["tau_up"] for k in EMOTIONS},
                         {"Happy": .5, "Sad": 2., "Confused": .4})
        self.assertEqual(set(p["modes"]), set(MODES))

    def test_invalid_profile_rejected_without_mutation(self):
        base = pasted_defaults()
        for corrupt in (
            lambda p: p["global"].__setitem__("max_slew_deg_s", 100),
            lambda p: p["global"].__setitem__("so2_alpha", float("nan")),
            lambda p: p["input_gate"].__setitem__("timeout_s", 0.2),
            lambda p: p["modes"]["Sad"]["poses"][1].__setitem__("time", 0),
            lambda p: p["modes"]["Happy"].__setitem__("light_color", "blue"),
            lambda p: p["modes"]["Neutral"].__setitem__("gains", [2.0] * 3),
            lambda p: p["joint_limits"][0].__setitem__("min", 91),
            lambda p: p["modes"]["Neutral"]["poses"][0].__setitem__("angles", [0,90,60,90]),
            lambda p: p["hormones"]["Sad"].__setitem__("tau_up", 0.01),
            lambda p: p.__setitem__("unknown", 123),
        ):
            p = copy.deepcopy(base)
            corrupt(p)
            with self.subTest(corrupt=corrupt):
                with self.assertRaises(ValueError):
                    validate_profile(p, dt=.05)
        self.assertEqual(base, pasted_defaults())

    def test_profile_roundtrip_and_atomic_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "my_cpg.json"
            p = pasted_defaults()
            save_profile_atomic(p, path)
            before = path.read_bytes()
            modified = copy.deepcopy(p)
            modified["modes"]["Happy"]["phi"] = 0.75
            save_profile_atomic(modified, path)
            self.assertEqual(load_profile(path)["modes"]["Happy"]["phi"], .75)
            modified["global"]["so2_alpha"] = float("inf")
            with self.assertRaises(ValueError):
                save_profile_atomic(modified, path)
            self.assertEqual(load_profile(path)["modes"]["Happy"]["phi"], .75)
            self.assertNotEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(dirname).iterdir()), [path])

    def test_bad_profile_fails_real_startup_mock_can_use_defaults(self):
        with tempfile.TemporaryDirectory() as dirname:
            bad = Path(dirname) / "missing.json"
            with self.assertRaisesRegex(ValueError, "Cannot load CPG profile"):
                MainCpgAdapter(start_time=0, profile_path=bad)
            adapter = MainCpgAdapter(start_time=0, profile_path=bad, allow_defaults=True)
            self.assertEqual(adapter.profile_snapshot(), pasted_defaults())

    def test_interpolation_clamps_and_is_linear(self):
        poses = pasted_defaults()["modes"]["Happy"]["poses"]
        self.assertEqual(lerp_keyframes(poses, 3, "angles"), [80., 97.5, 74., 101.5])
        self.assertEqual(lerp_keyframes(poses, 500, "angles"), poses[-1]["angles"])

    def test_debounce_hormones_and_slew_output(self):
        c = LampController(start_time=0.0)
        for i, t in enumerate((0., .15, .31)):
            self.assertTrue(c.observe(happy(i, t), t))
        out = c.step(.36)
        self.assertEqual(out["mode"], "Happy")
        self.assertGreater(out["hormones"]["Happy"], 0)
        self.assertTrue(all(89 <= v <= 91 for v in out["q_deg"]))
        last = out["q_deg"]
        for i in range(1, 25):
            current = c.step(.36 + .05 * i)["q_deg"]
            self.assertTrue(all(abs(a-b) <= 1.000001 for a, b in zip(current, last)))
            self.assertTrue(all(60 <= a <= 120 for a in current))
            last = current
        self.assertEqual(c.step(2.0)["mode"], "Neutral")

    def test_begin_motion_resets_an_idle_preloaded_command(self):
        c = LampController(start_time=0)
        for i in range(200):
            c.step(.05 * (i + 1))
        self.assertLess(c.step(10.05)["q_deg"][2], 75)
        c.begin_motion(10.05)
        self.assertEqual(c.step(10.05)["q_deg"], (90.,) * 4)
        self.assertLessEqual(abs(c.step(10.10)["q_deg"][2] - 90), 1.000001)

    def test_adapter_camera_timeout_and_apply_clears_readiness(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "profile.json"
            save_profile_atomic(pasted_defaults(), path)
            a = MainCpgAdapter(start_time=0, profile_path=path)
            self.assertTrue(a.observe(happy(0, 0), 0))
            self.assertTrue(a.ready)
            updated = a.profile_snapshot()
            updated["modes"]["Happy"]["gains"][0] = 13
            a.apply_profile(updated, start_time=.1)
            self.assertFalse(a.ready)
            self.assertEqual(a.profile_snapshot()["modes"]["Happy"]["gains"][0], 13)
            self.assertEqual(a.step(.1).q_deg, (90., 90., 90., 90.))

    def test_runtime_saves_only_in_safe_states_without_writing_motor(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "profile.json"
            save_profile_atomic(pasted_defaults(), path)
            config = load_runtime_config(APP_DIR / "robot_config.json")
            bus = FakeBus()
            adapter = MainCpgAdapter(start_time=0, profile_path=path, loop_hz=config.loop_hz)
            runtime = RobotRuntime(config, HardwareMapper(config), bus, adapter)
            changed = adapter.profile_snapshot()
            changed["modes"]["Sad"]["phi"] = .21
            runtime.apply_cpg_profile(changed)
            self.assertEqual(load_profile(path)["modes"]["Sad"]["phi"], .21)
            self.assertFalse(bus.events)
            runtime.submit(RuntimeCommand.CONNECT)
            runtime.process_once(0.01)
            runtime.submit(RuntimeCommand.ARM)
            runtime.process_once(.02)
            runtime.submit(RuntimeCommand.CENTER)
            runtime.process_once(.03)
            adapter.observe(happy(0, .04), .04)
            runtime.submit(RuntimeCommand.START_CPG)
            runtime.process_once(.05)
            self.assertEqual(runtime.latest_snapshot().state, RuntimeState.RUNNING)
            changing = adapter.profile_snapshot()
            changing["modes"]["Happy"]["phi"] = .66
            previous_bytes = path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "Stop CPG"):
                runtime.apply_cpg_profile(changing)
            self.assertEqual(path.read_bytes(), previous_bytes)

    def test_runtime_restarts_from_center_on_start(self):
        config = load_runtime_config(APP_DIR / "robot_config.json")
        bus = FakeBus()
        adapter = MainCpgAdapter(start_time=0.0)
        runtime = RobotRuntime(config, HardwareMapper(config), bus, adapter)
        for i in range(120):
            adapter.step((i + 1) * .05)
        runtime.submit(RuntimeCommand.CONNECT)
        runtime.process_once(6.05)
        runtime.submit(RuntimeCommand.ARM)
        runtime.process_once(6.10)
        runtime.submit(RuntimeCommand.CENTER)
        runtime.process_once(6.15)
        adapter.observe(happy(0, 6.20), 6.20)
        runtime.submit(RuntimeCommand.START_CPG)
        runtime.process_once(6.25)
        self.assertEqual(runtime.latest_snapshot().state, RuntimeState.RUNNING)
        self.assertEqual(runtime.latest_snapshot().cpg_deg, (90.,) * 4)
        self.assertEqual(runtime.latest_snapshot().target_raw, {i: config.servos[i].center for i in range(4)})


class CpgEditorTests(unittest.TestCase):
    def test_gui_exposes_profile_entries_and_rejects_unsafe_edits(self):
        import tkinter as tk
        from tools.robot_cpg_gui.cpg_editor import CpgProfileEditor
        config = load_runtime_config(APP_DIR / "robot_config.json")
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "profile.json"
            save_profile_atomic(pasted_defaults(), path)
            adapter = MainCpgAdapter(start_time=0, profile_path=path)
            runtime = RobotRuntime(config, HardwareMapper(config), FakeBus(), adapter)
            root = tk.Tk()
            root.withdraw()
            try:
                editor = CpgProfileEditor(root, runtime=runtime)
                editor.pack()
                self.assertEqual(len(editor.mode_tables), 4)
                self.assertTrue(editor.validate())
                editor.scalar["modes.Happy.phi"].set("0.9")
                self.assertTrue(editor.save_apply())
                self.assertAlmostEqual(adapter.profile_snapshot()["modes"]["Happy"]["phi"], .9)
                self.assertEqual(load_profile(path)["modes"]["Happy"]["phi"], .9)
                editor.set_editable(False)
                self.assertTrue(editor.buttons[-1].instate(("disabled",)))
                editor.scalar["modes.Happy.phi"].set("1.0")
                self.assertFalse(editor.save_apply())
                self.assertEqual(load_profile(path)["modes"]["Happy"]["phi"], .9)
                editor.set_editable(True)
                editor.scalar["modes.Happy.phi"].set("invalid")
                self.assertFalse(editor.save_apply())
                self.assertEqual(load_profile(path)["modes"]["Happy"]["phi"], .9)
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
