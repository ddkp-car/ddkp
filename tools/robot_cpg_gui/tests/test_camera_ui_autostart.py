import unittest
import tkinter as tk
from pathlib import Path

from tools.robot_cpg_gui.app import RobotCpgApp
from tools.robot_cpg_gui.camera import CameraMailbox
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.runtime import RuntimeCommand, RobotRuntime
from fakes import FakeBus, FakeCpgSource

DIR = Path(__file__).resolve().parents[1]


class CameraUiTests(unittest.TestCase):
    def test_auto_button_starts_camera_then_restarts_search_without_motor_motion(self):
        from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
        class Worker:
            def start(self):
                pass
            def stop(self, timeout=0.0):
                return True
            def is_alive(self):
                return True
        config = load_runtime_config(DIR / 'robot_config.json')
        adapter = MainCpgAdapter(start_time=0, profile_path=DIR / 'cpg_profile.json')
        adapter.select_manual_mode('Happy')
        runtime = RobotRuntime(config, HardwareMapper(config), FakeBus(), adapter)
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(root, config, HardwareMapper(config), runtime, CameraMailbox(),
                          camera_factory=lambda index: Worker(), mock_mode=True)
        try:
            app._activate_numpad_number(0)
            self.assertIsNone(adapter.manual_mode)
            self.assertTrue(app.camera_running)
            self.assertEqual(runtime._commands.get_nowait(), RuntimeCommand.CAMERA_AUTO_ON)
            app._activate_numpad_number(0)
            self.assertEqual(runtime._commands.get_nowait(), RuntimeCommand.CAMERA_SEARCH)
            self.assertFalse(any(e[0] == 'sync' for e in runtime.bus.events))
        finally:
            app.close()
            runtime.process_once(0.0)
            root.update_idletasks()
            if not app.closed:
                app._poll_shutdown()

    def test_camera_button_enables_auto_search_and_stop_disables_it(self):
        class Worker:
            def __init__(self):
                self.running = False
            def start(self):
                self.running = True
            def stop(self, timeout=0.0):
                self.running = False
                return True
            def is_alive(self):
                return self.running
        config = load_runtime_config(DIR / 'robot_config.json')
        runtime = RobotRuntime(config, HardwareMapper(config), FakeBus(), FakeCpgSource())
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(root, config, HardwareMapper(config), runtime, CameraMailbox(),
                          camera_factory=lambda index: Worker(), mock_mode=True)
        try:
            self.assertTrue(app.led_config.enabled)  # current user config is enabled
            self.assertIsNone(app._led_driver)  # mock must never touch GPIO
            app._start_camera()
            self.assertTrue(app.camera_running)
            self.assertEqual(runtime._commands.get_nowait(), RuntimeCommand.CAMERA_AUTO_ON)
            app._stop_camera()
            self.assertFalse(app.camera_running)
            self.assertEqual(runtime._commands.get_nowait(), RuntimeCommand.CAMERA_AUTO_OFF)
            # _complete_camera_stop publishes an error and updates UI only;
            # no implicit arm/center/servo move is allowed.
            self.assertFalse(any(e[0] == 'sync' for e in runtime.bus.events))
        finally:
            app.close()
            runtime.process_once(0.0)
            root.update_idletasks()
            if not app.closed:
                app._poll_shutdown()


if __name__ == '__main__':
    unittest.main()
