import unittest


class FakeCapture:
    def __init__(self, *, opened=True, readable=True, backend="TEST", read_error=False):
        self.opened = opened
        self.readable = readable
        self.backend = backend
        self.read_error = read_error
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.read_error:
            raise RuntimeError("simulated read error")
        return self.readable, object() if self.readable else None

    def getBackendName(self):
        return self.backend

    def release(self):
        self.released = True


class CameraDiscoveryTests(unittest.TestCase):
    def test_discovery_returns_only_readable_cameras_and_releases_every_handle(self):
        from tools.robot_cpg_gui.camera_discovery import discover_cameras

        captures = {
            0: FakeCapture(backend="DSHOW"),
            1: FakeCapture(opened=False),
            2: FakeCapture(readable=False),
            3: FakeCapture(read_error=True),
        }

        choices = discover_cameras(
            max_index=4,
            capture_factory=lambda index: (
                (_ for _ in ()).throw(RuntimeError("factory failed"))
                if index == 4 else captures[index]
            ),
        )

        self.assertEqual(
            [(choice.index, choice.label) for choice in choices],
            [(0, "Camera 0 (DSHOW)")],
        )
        self.assertTrue(all(capture.released for capture in captures.values()))

    def test_preferred_choice_is_retained_or_falls_back_to_first(self):
        from tools.robot_cpg_gui.camera_discovery import CameraChoice, choose_camera

        choices = [CameraChoice(1, "Camera 1"), CameraChoice(4, "Camera 4")]

        self.assertEqual(choose_camera(choices, 4), choices[1])
        self.assertEqual(choose_camera(choices, 7), choices[0])
        self.assertIsNone(choose_camera([], 0))


if __name__ == "__main__":
    unittest.main()
