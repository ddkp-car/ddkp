import unittest
from dataclasses import replace
from pathlib import Path


def app_api(testcase: unittest.TestCase):
    try:
        from tools.robot_cpg_gui.app import project_ui
        from tools.robot_cpg_gui.runtime import RuntimeSnapshot, RuntimeState
    except ImportError as exc:
        testcase.fail(f"GUI projection API is not implemented: {exc}")
    return project_ui, RuntimeSnapshot, RuntimeState


class UiProjectionTests(unittest.TestCase):
    def snapshot(self, state, *, center_ready=False):
        _, RuntimeSnapshot, _ = app_api(self)
        return RuntimeSnapshot(
            timestamp=0.0,
            state=state,
            feedback_raw={},
            feedback_speed={},
            feedback_moving={},
            feedback_torque={},
            cpg_deg=(90.0, 90.0, 90.0, 90.0),
            target_raw={},
            hormones={"Happy": 0.0, "Sad": 0.0, "Confused": 0.0},
            emotion="Neutral",
            input_status="not_started",
            fault_text="",
            torque_off_verified=False,
            center_ready=center_ready,
        )

    def test_disconnected_allows_only_connect_and_camera(self):
        project_ui, _, states = app_api(self)

        projection = project_ui(self.snapshot(states.DISCONNECTED), camera_running=False)

        self.assertTrue(projection.can_connect)
        self.assertTrue(projection.can_start_camera)
        self.assertFalse(projection.can_arm)
        self.assertFalse(projection.can_start_cpg)
        self.assertFalse(projection.can_emergency)

    def test_armed_requires_camera_and_successful_center_before_cpg_start(self):
        project_ui, _, states = app_api(self)
        without_camera = project_ui(self.snapshot(states.ARMED), camera_running=False)
        before_center = project_ui(self.snapshot(states.ARMED), camera_running=True)
        ready = project_ui(
            self.snapshot(states.ARMED, center_ready=True), camera_running=True
        )

        self.assertTrue(without_camera.can_center)
        self.assertFalse(without_camera.can_start_cpg)
        self.assertFalse(before_center.can_start_cpg)
        self.assertTrue(ready.can_start_cpg)
        self.assertTrue(ready.can_emergency)

    def test_running_allows_stop_and_emergency_not_other_motion(self):
        project_ui, _, states = app_api(self)

        projection = project_ui(self.snapshot(states.RUNNING), camera_running=True)

        self.assertTrue(projection.can_stop_cpg)
        self.assertTrue(projection.can_emergency)
        self.assertFalse(projection.can_center)
        self.assertFalse(projection.can_arm)
        self.assertFalse(projection.can_connect)

    def test_fault_and_emergency_allow_reconnect_but_no_motion(self):
        project_ui, _, states = app_api(self)
        for state in (states.FAULT, states.EMERGENCY):
            with self.subTest(state=state):
                projection = project_ui(self.snapshot(state), camera_running=True)
                self.assertTrue(projection.can_connect)
                self.assertTrue(projection.can_emergency)
                self.assertFalse(projection.can_start_cpg)
                self.assertFalse(projection.can_arm)


if __name__ == "__main__":
    unittest.main()
