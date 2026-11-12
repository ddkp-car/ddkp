from __future__ import annotations

import threading
import time
import uuid

from .camera import CameraMailbox


class MockCameraWorker(threading.Thread):
    MODES = (
        ("neutral", "Neutral", "#dddddd"),
        ("happy", "Happy", "#ffd43b"),
        ("sad", "Sad", "#377dff"),
        ("surprised", "Confused", "#b05cff"),
    )

    def __init__(self, mailbox: CameraMailbox, *, fps: float = 10.0) -> None:
        super().__init__(name="mock-camera", daemon=True)
        self.mailbox = mailbox
        self.fps = float(fps)
        self.stop_event = threading.Event()

    def stop(self, timeout: float = 2.0) -> bool:
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=timeout)
        return not self.is_alive()

    def run(self) -> None:
        sequence = 0
        stream_id = "mock-" + uuid.uuid4().hex
        started = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            emotion, label, color = self.MODES[int((now - started) / 4.0) % len(self.MODES)]
            observation = {
                "schema": 1,
                "stream_id": stream_id,
                "seq": sequence,
                "detected": True,
                "face_x": 0.5 + 0.30 * __import__("math").sin((now - started) * 0.6),
                "face_y": 0.5,
                "emotion": emotion,
                "strength": 0.9,
            }
            sequence += 1
            preview = self._preview(label, color, sequence)
            self.mailbox.publish(
                captured_at=now,
                observation=observation,
                preview_rgb=preview,
            )
            self.stop_event.wait(1.0 / self.fps)

    @staticmethod
    def _preview(label: str, color: str, sequence: int):
        try:
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (320, 240), color)
            draw = ImageDraw.Draw(image)
            draw.rectangle((8, 8, 312, 232), outline="#222222", width=3)
            draw.text((25, 90), f"MOCK CAMERA\n{label}\nframe {sequence}", fill="#111111")
            return image
        except ImportError:
            return None

