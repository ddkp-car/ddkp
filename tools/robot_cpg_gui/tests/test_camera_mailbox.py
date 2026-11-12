import unittest
import threading


def camera_api(testcase: unittest.TestCase):
    try:
        from tools.robot_cpg_gui.camera import CameraMailbox, CameraPacket
    except ImportError as exc:
        testcase.fail(f"camera API is not implemented: {exc}")
    return CameraMailbox, CameraPacket


class CameraMailboxTests(unittest.TestCase):
    def test_latest_packet_replaces_older_unconsumed_packet(self):
        CameraMailbox, CameraPacket = camera_api(self)
        mailbox = CameraMailbox()
        first = mailbox.publish(
            observation={"seq": 1}, captured_at=1.0, preview_rgb="frame-1"
        )
        second = mailbox.publish(
            observation={"seq": 2}, captured_at=2.0, preview_rgb="frame-2"
        )

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertIsNone(mailbox.latest_after(2))
        self.assertEqual(mailbox.latest_after(0), second)

    def test_error_packet_is_visible_to_all_consumers(self):
        CameraMailbox, CameraPacket = camera_api(self)
        mailbox = CameraMailbox()
        packet = mailbox.publish(error="camera unplugged", captured_at=3.0)

        runtime_copy = mailbox.latest_after(0)
        gui_copy = mailbox.latest_after(0)

        self.assertEqual(runtime_copy, packet)
        self.assertEqual(gui_copy, packet)
        self.assertEqual(packet.error, "camera unplugged")

    def test_publish_requires_finite_nonnegative_capture_time(self):
        CameraMailbox, CameraPacket = camera_api(self)
        mailbox = CameraMailbox()

        for bad in (-1.0, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    mailbox.publish(observation={}, captured_at=bad)

    def test_camera_stop_reports_timeout_until_owner_has_terminated(self):
        from tools.robot_cpg_gui.camera import UsbCameraWorker

        release = threading.Event()

        class BlockingCamera(UsbCameraWorker):
            def run(self):
                release.wait(1.0)

        mailbox, _ = camera_api(self)
        worker = BlockingCamera("unused", mailbox())
        worker.start()

        self.assertFalse(worker.stop(timeout=0.01))
        self.assertTrue(worker.is_alive())
        release.set()
        self.assertTrue(worker.stop(timeout=1.0))


if __name__ == "__main__":
    unittest.main()
