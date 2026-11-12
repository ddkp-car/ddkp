import time
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]


class FakeRuntime:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.commands = []
        self.emergency_requested = False
        self.closed = False
        self.is_stopped = False
        self.events = []
        self.close_result = True

    def latest_snapshot(self):
        return self.snapshot

    def submit(self, command):
        self.commands.append(command)

    def request_emergency(self):
        self.emergency_requested = True
        self.events.append("emergency")

    def close(self, timeout=0.0):
        self.closed = True
        self.is_stopped = self.close_result
        self.events.append("runtime_closed")
        return self.close_result


class AppSmokeTests(unittest.TestCase):
    def test_shutdown_immediately_locks_camera_controls_and_rejects_start(self):
        from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime
        prepare_tcl_runtime()
        import tkinter as tk
        from tools.robot_cpg_gui.app import RobotCpgApp
        from tools.robot_cpg_gui.camera import CameraMailbox
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState

        config = load_runtime_config(APP_DIR / "robot_config.json")
        snapshot = RuntimeSnapshot(
            timestamp=0.0, state=RuntimeState.RUNNING,
            feedback_raw={}, feedback_speed={}, feedback_moving={}, feedback_torque={},
            cpg_deg=(90.0,) * 4, target_raw={},
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral", input_status="accepted", fault_text="",
            torque_off_verified=False,
        )
        runtime = FakeRuntime(snapshot)
        runtime.close_result = False
        started_indices = []
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(
            root, config, HardwareMapper(config), runtime, CameraMailbox(),
            camera_factory=lambda index: started_indices.append(index),
            mock_mode=True,
        )

        app.close()
        app.camera_start_button.invoke()
        app._start_camera()

        self.assertEqual(started_indices, [])
        self.assertIsNone(app.camera_worker)
        self.assertTrue(app.camera_start_button.instate(("disabled",)))
        self.assertTrue(app.camera_scan_button.instate(("disabled",)))
        self.assertTrue(app.camera_combo.instate(("disabled",)))
        runtime.close_result = True
        app._poll_shutdown()

    def test_camera_selector_passes_selected_index_and_locks_during_capture(self):
        from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime
        prepare_tcl_runtime()
        import tkinter as tk
        from tools.robot_cpg_gui.app import RobotCpgApp
        from tools.robot_cpg_gui.camera import CameraMailbox
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState

        class Worker:
            def __init__(self):
                self.started = False

            def start(self):
                self.started = True

            def is_alive(self):
                return self.started

            def stop(self, timeout=0.0):
                self.started = False
                return True

        config = load_runtime_config(APP_DIR / "robot_config.json")
        snapshot = RuntimeSnapshot(
            timestamp=0.0, state=RuntimeState.DISCONNECTED,
            feedback_raw={}, feedback_speed={}, feedback_moving={}, feedback_torque={},
            cpg_deg=(90.0,) * 4, target_raw={},
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral", input_status="not_started", fault_text="",
            torque_off_verified=False,
        )
        selected = []
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(
            root, config, HardwareMapper(config), FakeRuntime(snapshot), CameraMailbox(),
            camera_factory=lambda index: (selected.append(index) or Worker()),
            mock_mode=True,
        )

        self.assertEqual(tuple(app.camera_combo.cget("values")), ("Mock Camera (index 0)",))
        self.assertEqual(app.camera_choice_var.get(), "Mock Camera (index 0)")
        app._start_camera()
        app._apply_projection(
            __import__("tools.robot_cpg_gui.app", fromlist=["project_ui"]).project_ui(
                snapshot, camera_running=True
            )
        )

        self.assertEqual(selected, [0])
        self.assertTrue(app.camera_combo.instate(("disabled",)))
        self.assertTrue(app.camera_scan_button.instate(("disabled",)))
        app._stop_camera()
        app.close()

    def test_gui_constructs_two_live_plots_and_closes_runtime(self):
        try:
            from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime
            prepare_tcl_runtime()
            import tkinter as tk
            from tools.robot_cpg_gui.app import RobotCpgApp
            from tools.robot_cpg_gui.camera import CameraMailbox
            from tools.robot_cpg_gui.config import load_runtime_config
            from tools.robot_cpg_gui.mapping import HardwareMapper
            from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState
        except ImportError as exc:
            self.fail(f"GUI API is not implemented: {exc}")

        config = load_runtime_config(APP_DIR / "robot_config.json")
        snapshot = RuntimeSnapshot(
            timestamp=0.0, state=RuntimeState.DISCONNECTED,
            feedback_raw={}, feedback_speed={}, feedback_moving={}, feedback_torque={},
            cpg_deg=(90.0, 90.0, 90.0, 90.0), target_raw={},
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral", input_status="not_started", fault_text="",
            torque_off_verified=False,
        )
        runtime = FakeRuntime(snapshot)
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(
            root, config, HardwareMapper(config), runtime, CameraMailbox(),
            camera_factory=None, mock_mode=True,
        )

        root.update_idletasks()
        self.assertIsNotNone(app.servo_plot)
        self.assertIsNotNone(app.cpg_plot)
        self.assertEqual(len(app.servo_tree.get_children()), 4)
        app.close()

        self.assertTrue(runtime.closed)

    def test_servo_feedback_uses_same_absolute_degree_frame_as_cpg(self):
        from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime
        prepare_tcl_runtime()
        import tkinter as tk
        from tools.robot_cpg_gui.app import RobotCpgApp
        from tools.robot_cpg_gui.camera import CameraMailbox
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState

        config = load_runtime_config(APP_DIR / "robot_config.json")
        now = time.monotonic()
        centers = {servo_id: servo.center for servo_id, servo in config.servos.items()}
        snapshot = RuntimeSnapshot(
            timestamp=now, state=RuntimeState.ARMED,
            feedback_raw=centers,
            feedback_speed={servo_id: 0 for servo_id in centers},
            feedback_moving={servo_id: False for servo_id in centers},
            feedback_torque={servo_id: True for servo_id in centers},
            cpg_deg=(90.0,) * 4, target_raw=centers,
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral", input_status="center", fault_text="",
            torque_off_verified=False,
        )
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(
            root, config, HardwareMapper(config), FakeRuntime(snapshot), CameraMailbox(),
            camera_factory=None, mock_mode=True,
        )

        app._update_from_snapshot(snapshot)

        series = app.servo_history.series(time.monotonic())
        self.assertTrue(all(points[-1][1] == 90.0 for points in series.values()))
        self.assertEqual(float(app.servo_tree.item("0", "values")[3]), 90.0)
        app.close()

    def test_preview_uses_image_dimensions_not_text_units(self):
        from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime
        prepare_tcl_runtime()
        import tkinter as tk
        from PIL import Image
        from tools.robot_cpg_gui.app import RobotCpgApp
        from tools.robot_cpg_gui.camera import CameraMailbox
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState

        config = load_runtime_config(APP_DIR / "robot_config.json")
        snapshot = RuntimeSnapshot(
            timestamp=0.0, state=RuntimeState.DISCONNECTED,
            feedback_raw={}, feedback_speed={}, feedback_moving={}, feedback_torque={},
            cpg_deg=(90.0,) * 4, target_raw={},
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral", input_status="not_started", fault_text="",
            torque_off_verified=False,
        )
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(
            root, config, HardwareMapper(config), FakeRuntime(snapshot), CameraMailbox(),
            camera_factory=None, mock_mode=True,
        )

        app._show_preview(Image.new("RGB", (320, 240), "red"))
        root.update_idletasks()

        self.assertEqual(int(app.preview_label.cget("width")), 0)
        self.assertEqual(int(app.preview_label.cget("height")), 0)
        app.close()

    def test_close_keeps_gui_and_camera_alive_until_robot_shutdown_confirms(self):
        from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime
        prepare_tcl_runtime()
        import tkinter as tk
        from tools.robot_cpg_gui.app import RobotCpgApp
        from tools.robot_cpg_gui.camera import CameraMailbox
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState

        class FakeCamera:
            def __init__(self):
                self.stop_calls = 0

            def stop(self, timeout=0.0):
                self.stop_calls += 1
                return True

            def is_alive(self):
                return True

        config = load_runtime_config(APP_DIR / "robot_config.json")
        snapshot = RuntimeSnapshot(
            timestamp=0.0, state=RuntimeState.RUNNING,
            feedback_raw={}, feedback_speed={}, feedback_moving={}, feedback_torque={},
            cpg_deg=(90.0,) * 4, target_raw={},
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral", input_status="accepted", fault_text="",
            torque_off_verified=False,
        )
        runtime = FakeRuntime(snapshot)
        runtime.close_result = False
        camera = FakeCamera()
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(
            root, config, HardwareMapper(config), runtime, CameraMailbox(),
            camera_factory=None, mock_mode=True,
        )
        app.camera_worker = camera
        app.camera_running = True

        app.close()

        self.assertTrue(runtime.emergency_requested)
        self.assertEqual(camera.stop_calls, 0)
        self.assertTrue(root.winfo_exists())
        runtime.close_result = True
        app._poll_shutdown()
        self.assertEqual(camera.stop_calls, 1)

    def test_camera_stop_timeout_retains_sole_owner_until_it_exits(self):
        from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime
        prepare_tcl_runtime()
        import tkinter as tk
        from tools.robot_cpg_gui.app import RobotCpgApp
        from tools.robot_cpg_gui.camera import CameraMailbox
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState

        class SlowCamera:
            def __init__(self):
                self.alive = True

            def stop(self, timeout=0.0):
                return not self.alive

            def is_alive(self):
                return self.alive

        config = load_runtime_config(APP_DIR / "robot_config.json")
        snapshot = RuntimeSnapshot(
            timestamp=0.0, state=RuntimeState.DISCONNECTED,
            feedback_raw={}, feedback_speed={}, feedback_moving={}, feedback_torque={},
            cpg_deg=(90.0,) * 4, target_raw={},
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral", input_status="not_started", fault_text="",
            torque_off_verified=False,
        )
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(
            root, config, HardwareMapper(config), FakeRuntime(snapshot), CameraMailbox(),
            camera_factory=None, mock_mode=True,
        )
        camera = SlowCamera()
        app.camera_worker = camera
        app.camera_running = True

        app._stop_camera()
        self.assertIs(app.camera_worker, camera)
        self.assertTrue(app.camera_running)
        camera.alive = False
        app._poll_camera()
        self.assertIsNone(app.camera_worker)
        self.assertFalse(app.camera_running)
        app.close()

    def test_camera_error_keeps_restart_disabled_until_owner_exits(self):
        from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime
        prepare_tcl_runtime()
        import tkinter as tk
        from tools.robot_cpg_gui.app import RobotCpgApp, project_ui
        from tools.robot_cpg_gui.camera import CameraMailbox
        from tools.robot_cpg_gui.config import load_runtime_config
        from tools.robot_cpg_gui.mapping import HardwareMapper
        from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState

        class FinishingCamera:
            def is_alive(self):
                return True

            def stop(self, timeout=0.0):
                return False

        config = load_runtime_config(APP_DIR / "robot_config.json")
        snapshot = RuntimeSnapshot(
            timestamp=0.0, state=RuntimeState.DISCONNECTED,
            feedback_raw={}, feedback_speed={}, feedback_moving={}, feedback_torque={},
            cpg_deg=(90.0,) * 4, target_raw={},
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral", input_status="not_started", fault_text="",
            torque_off_verified=False,
        )
        mailbox = CameraMailbox()
        root = tk.Tk()
        root.withdraw()
        app = RobotCpgApp(
            root, config, HardwareMapper(config), FakeRuntime(snapshot), mailbox,
            camera_factory=None, mock_mode=True,
        )
        app.camera_worker = FinishingCamera()
        app.camera_running = True
        mailbox.publish(captured_at=1.0, error="camera unplugged")

        app._poll_camera()
        projection = project_ui(snapshot, camera_running=app.camera_running)

        self.assertTrue(app.camera_running)
        self.assertFalse(projection.can_start_camera)
        app.camera_worker = None
        app.camera_running = False
        app.close()


if __name__ == "__main__":
    unittest.main()
