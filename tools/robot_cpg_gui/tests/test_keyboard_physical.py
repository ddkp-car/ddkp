"""Keyboard events must actually trigger manual personalities, not only GUI buttons."""
import queue
import unittest
from pathlib import Path
from types import SimpleNamespace

from fakes import FakeBus
from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.runtime import RobotRuntime
from tools.robot_cpg_gui.camera import CameraMailbox

DIR = Path(__file__).resolve().parents[1]


class KeyboardIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tools.robot_cpg_gui.app import RobotCpgApp
        config = load_runtime_config(DIR / 'robot_config.json')
        self.adapter = MainCpgAdapter(start_time=0)
        self.runtime = RobotRuntime(config, HardwareMapper(config), FakeBus(), self.adapter)
        self.root = tk.Tk()
        self.app = RobotCpgApp(self.root, config, HardwareMapper(config), self.runtime,
                               CameraMailbox(), camera_factory=None, mock_mode=True)
        self.root.update()

    def tearDown(self):
        self.app.close()
        for _ in range(5):
            if self.app.closed:
                break
            self.root.update()

    def test_keyboard_without_mouse_click_or_buttons(self):
        # Generate actual Tk KeyPress events, not direct calls to button commands.
        self.root.focus_force()
        self.root.update()
        for num, mode in ((1, 'Happy'), (2, 'Sad'), (3, 'Confused'), (4, 'Neutral')):
            self.root.event_generate('<KeyPress>', keysym=f'KP_{num}')
            self.root.update()
            self.assertEqual(self.adapter.manual_mode, mode)
        self.root.event_generate('<KeyPress>', keysym='KP_0')
        self.root.update()
        self.assertIsNone(self.adapter.manual_mode)

    def test_native_listener_queue_changes_mode_without_tk_focus(self):
        # Native Windows worker delivers the physical number into a Queue;
        # this test uses its exact interface without needing Windows or hardware.
        worker = SimpleNamespace(events=queue.Queue(), statuses=queue.Queue())
        self.app._global_numpad = worker
        for num, mode in ((1, 'Happy'), (2, 'Sad'), (3, 'Confused'), (4, 'Neutral')):
            worker.events.put(num)
            self.app._poll_numpad_keyboard()
            self.assertEqual(self.adapter.manual_mode, mode)
        worker.events.put(0)
        self.app._poll_numpad_keyboard()
        self.assertIsNone(self.adapter.manual_mode)
        # Real app closure stops listener; fake needs a stop method.
        worker.stop = lambda: None
