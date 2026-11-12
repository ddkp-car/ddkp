"""Regression tests for the Numpad personality picker (no real servos)."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from fakes import FakeBus

from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand, RuntimeState

APP_DIR = Path(__file__).resolve().parents[1]


class ManualCpgSelectionTests(unittest.TestCase):
    def test_all_four_personalities_are_selectable_without_camera(self):
        adapter = MainCpgAdapter(start_time=0)
        self.assertFalse(adapter.ready)
        self.assertIsNone(adapter.manual_mode)
        for n, mode in enumerate(("Happy", "Sad", "Confused", "Neutral")):
            adapter.select_manual_mode(mode)
            self.assertEqual(adapter.manual_mode, mode)
            self.assertTrue(adapter.ready)
            frame = adapter.step(0.05 * (n + 1))
            self.assertEqual(frame.emotion, mode)
            self.assertEqual(frame.input_status, 'manual_numpad')
        adapter.select_camera_auto()
        self.assertFalse(adapter.ready)
        self.assertIsNone(adapter.manual_mode)
        self.assertEqual(adapter.step(0.25).emotion, 'Neutral')

    def test_camera_timeout_does_not_cancel_manual_mode(self):
        adapter = MainCpgAdapter(start_time=0)
        adapter.select_manual_mode('Happy')
        self.assertTrue(adapter.ready)
        for i in range(1, 30):
            frame = adapter.step(0.05 * i)
        self.assertEqual(frame.emotion, 'Happy')
        self.assertEqual(frame.input_status, 'manual_numpad')
        self.assertTrue(adapter.ready)

    def test_live_camera_does_not_override_manual_and_auto_restores_camera(self):
        adapter = MainCpgAdapter(start_time=0)
        adapter.select_manual_mode('Confused')
        for seq, at in enumerate((0.0, 0.15, 0.31)):
            self.assertTrue(adapter.observe({
                'schema': 1, 'stream_id': 'camera-1', 'seq': seq,
                'detected': True, 'emotion': 'happy', 'strength': 0.95,
            }, at))
        self.assertEqual(adapter.step(.36).emotion, 'Confused')
        adapter.select_camera_auto()
        self.assertEqual(adapter.step(.40).emotion, 'Happy')
        adapter.select_manual_mode('Sad')
        self.assertEqual(adapter.step(.95).input_status, 'manual_numpad')
        self.assertTrue(adapter.ready)

    def test_bad_modes_rejected_without_changing_selection(self):
        adapter = MainCpgAdapter(start_time=0)
        adapter.select_manual_mode('Sad')
        for value in ('happy', 'Angry', '', None):
            with self.assertRaises(ValueError):
                adapter.select_manual_mode(value)
            self.assertEqual(adapter.manual_mode, 'Sad')

    def test_manual_ready_does_not_skip_center_or_arming(self):
        config = load_runtime_config(APP_DIR / 'robot_config.json')
        adapter = MainCpgAdapter(start_time=0)
        adapter.select_manual_mode('Happy')
        bus = FakeBus({i: v.center for i, v in config.servos.items()})
        runtime = RobotRuntime(config, HardwareMapper(config), bus, adapter)
        runtime.submit(RuntimeCommand.CONNECT)
        runtime.process_once(.02)
        runtime.submit(RuntimeCommand.ARM)
        runtime.process_once(.03)
        # Start directly after ARM must not run; GO CENTER remains compulsory.
        runtime.submit(RuntimeCommand.START_CPG)
        runtime.process_once(.04)
        self.assertEqual(runtime.latest_snapshot().state, RuntimeState.ARMED)
        runtime.submit(RuntimeCommand.CENTER)
        runtime.process_once(.05)
        self.assertEqual(runtime.latest_snapshot().state, RuntimeState.ARMED)
        self.assertTrue(runtime.latest_snapshot().center_ready)
        runtime.submit(RuntimeCommand.START_CPG)
        runtime.process_once(.10)
        self.assertEqual(runtime.latest_snapshot().state, RuntimeState.RUNNING)
        self.assertEqual(runtime.latest_snapshot().emotion, 'Happy')
        self.assertEqual(runtime.latest_snapshot().input_status, 'manual_numpad')
        adapter.select_manual_mode('Sad')
        runtime.process_once(.15)
        self.assertEqual(runtime.latest_snapshot().emotion, 'Sad')
        adapter.select_manual_mode('Confused')
        runtime.process_once(.20)
        self.assertEqual(runtime.latest_snapshot().emotion, 'Confused')
        adapter.select_manual_mode('Neutral')
        runtime.process_once(.25)
        self.assertEqual(runtime.latest_snapshot().emotion, 'Neutral')
        runtime.request_emergency()
        runtime.process_once(.30)
        self.assertIn(runtime.latest_snapshot().state, (RuntimeState.EMERGENCY, RuntimeState.FAULT))


class NumpadGuiTests(unittest.TestCase):
    def test_numpad_bindings_and_text_edit_protection(self):
        import tkinter as tk
        from tkinter import ttk
        from tools.robot_cpg_gui.app import NUMPAD_PERSONALITIES, RobotCpgApp, project_ui
        from tools.robot_cpg_gui.camera import CameraMailbox

        self.assertEqual(NUMPAD_PERSONALITIES['KP_1'], 'Happy')
        self.assertEqual(NUMPAD_PERSONALITIES['KP_2'], 'Sad')
        self.assertEqual(NUMPAD_PERSONALITIES['KP_3'], 'Confused')
        self.assertEqual(NUMPAD_PERSONALITIES['KP_4'], 'Neutral')
        self.assertIsNone(NUMPAD_PERSONALITIES['KP_0'])

        config = load_runtime_config(APP_DIR / 'robot_config.json')
        adapter = MainCpgAdapter(start_time=0)
        runtime = RobotRuntime(config, HardwareMapper(config), FakeBus(), adapter)
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(root, config, HardwareMapper(config), runtime, CameraMailbox(),
                          camera_factory=None, mock_mode=True)
        try:
            for key, mode in (('KP_1', 'Happy'), ('KP_Down','Sad'),
                              ('KP_3','Confused'), ('KP_Left','Neutral')):
                app._on_numpad_key(SimpleNamespace(keysym=key, state=0, widget=root))
                self.assertEqual(adapter.manual_mode, mode)
            app._on_numpad_key(SimpleNamespace(keysym='KP_0', state=0, widget=root))
            self.assertIsNone(adapter.manual_mode)
            edit = ttk.Entry(root)
            app._on_numpad_key(SimpleNamespace(keysym='KP_1', state=0, widget=edit))
            self.assertIsNone(adapter.manual_mode)
            app._on_numpad_key(SimpleNamespace(keysym='KP_1', state=4, widget=root))
            self.assertIsNone(adapter.manual_mode)
            # Manual mode allows START button after Center, even with camera stopped.
            from tools.robot_cpg_gui.runtime import RuntimeSnapshot
            base = runtime.latest_snapshot()
            projection = project_ui(
                __import__('dataclasses').replace(base, state=RuntimeState.ARMED,
                                                   center_ready=True),
                camera_running=False, manual_ready=True,
            )
            self.assertTrue(projection.can_start_cpg)
            projection = project_ui(
                __import__('dataclasses').replace(base, state=RuntimeState.ARMED,
                                                   center_ready=False),
                camera_running=False, manual_ready=True,
            )
            self.assertFalse(projection.can_start_cpg)
        finally:
            app.close()
