from __future__ import annotations

import math
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .maincpg_adapter import ensure_maincpg_importable


@dataclass(frozen=True)
class CameraPacket:
    sequence: int
    observation: dict[str, Any] | None
    captured_at: float
    preview_rgb: Any | None
    error: str


class CameraMailbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._latest: CameraPacket | None = None

    def publish(
        self,
        *,
        captured_at: float,
        observation: dict[str, Any] | None = None,
        preview_rgb: Any | None = None,
        error: str = "",
    ) -> CameraPacket:
        timestamp = float(captured_at)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("captured_at must be finite and nonnegative")
        with self._lock:
            self._sequence += 1
            packet = CameraPacket(
                sequence=self._sequence,
                observation=None if observation is None else dict(observation),
                captured_at=timestamp,
                preview_rgb=preview_rgb,
                error=str(error),
            )
            self._latest = packet
            return packet

    def latest_after(self, sequence: int) -> CameraPacket | None:
        with self._lock:
            if self._latest is None or self._latest.sequence <= int(sequence):
                return None
            return self._latest


class UsbCameraWorker(threading.Thread):
    def __init__(
        self,
        model_path: str,
        mailbox: CameraMailbox,
        *,
        camera_index: int = 0,
        fps: float = 15.0,
        preview_width: int = 320,
        preview_height: int = 240,
    ) -> None:
        super().__init__(name="usb-camera", daemon=True)
        self.model_path = str(model_path)
        self.mailbox = mailbox
        self.camera_index = int(camera_index)
        self.fps = float(fps)
        self.preview_size = (int(preview_width), int(preview_height))
        if self.fps <= 0 or min(self.preview_size) <= 0:
            raise ValueError("camera FPS and preview dimensions must be positive")
        self.stop_event = threading.Event()

    def stop(self, timeout: float = 2.0) -> bool:
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=timeout)
        return not self.is_alive()

    def run(self) -> None:
        capture = detector = None
        try:
            ensure_maincpg_importable()
            from face_vision.vision_core import (
                classify_expression,
                cv2,
                mp,
                mp_python,
                np,
                vision,
            )

            options = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=self.model_path),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                output_face_blendshapes=True,
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            detector = vision.FaceLandmarker.create_from_options(options)
            backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
            capture = cv2.VideoCapture(self.camera_index, backend)
            if not capture.isOpened():
                raise RuntimeError(f"cannot open USB camera index {self.camera_index}")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            origin = time.monotonic()
            last_ms = -1
            observation_seq = 0
            stream_id = uuid.uuid4().hex
            while not self.stop_event.is_set():
                captured_at = time.monotonic()
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError("USB camera read failed")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                media_image = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=np.ascontiguousarray(rgb),
                )
                timestamp_ms = max(last_ms + 1, int((captured_at - origin) * 1000))
                last_ms = timestamp_ms
                result = detector.detect_for_video(media_image, timestamp_ms)
                detected = bool(result.face_landmarks)
                emotion, strength = "unknown", 0.0
                if detected:
                    blendshapes = result.face_blendshapes[0] if result.face_blendshapes else []
                    emotion, strength = classify_expression(blendshapes)
                # MediaPipe landmarks are normalized by image width/height.
                # A face's bounding-box midpoint is more stable than a single
                # landmark; these two coordinates steer the J1 face tracker.
                face_x = face_y = None
                if detected:
                    points = result.face_landmarks[0]
                    face_x = max(0.0, min(1.0, (min(point.x for point in points) + max(point.x for point in points)) * 0.5))
                    face_y = max(0.0, min(1.0, (min(point.y for point in points) + max(point.y for point in points)) * 0.5))
                observation = {
                    "face_x": face_x,
                    "face_y": face_y,
                    "schema": 1,
                    "stream_id": stream_id,
                    "seq": observation_seq,
                    "detected": detected,
                    "emotion": emotion,
                    "strength": float(strength),
                }
                observation_seq += 1
                preview = cv2.resize(rgb, self.preview_size, interpolation=cv2.INTER_AREA)
                self.mailbox.publish(
                    captured_at=captured_at,
                    observation=observation,
                    preview_rgb=preview,
                )
                self.stop_event.wait(1.0 / self.fps)
        except Exception as exc:
            self.mailbox.publish(captured_at=time.monotonic(), error=str(exc))
        finally:
            if capture is not None:
                capture.release()
            if detector is not None:
                detector.close()

