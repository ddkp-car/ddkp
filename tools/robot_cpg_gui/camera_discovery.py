from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class CameraChoice:
    index: int
    label: str


def choose_camera(
    choices: Sequence[CameraChoice], preferred_index: int
) -> CameraChoice | None:
    for choice in choices:
        if choice.index == int(preferred_index):
            return choice
    return choices[0] if choices else None


def discover_cameras(
    max_index: int = 9,
    capture_factory: Callable[[int], Any] | None = None,
) -> list[CameraChoice]:
    upper = int(max_index)
    if upper < 0:
        raise ValueError("max_index must be nonnegative")

    if capture_factory is None:
        import cv2

        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        capture_factory = lambda index: cv2.VideoCapture(index, backend)

    choices: list[CameraChoice] = []
    for index in range(upper + 1):
        capture = None
        try:
            capture = capture_factory(index)
            if capture is None or not bool(capture.isOpened()):
                continue
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            try:
                backend_name = str(capture.getBackendName()).strip()
            except Exception:
                backend_name = ""
            label = f"Camera {index}"
            if backend_name:
                label += f" ({backend_name})"
            choices.append(CameraChoice(index=index, label=label))
        except Exception:
            continue
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
    return choices

