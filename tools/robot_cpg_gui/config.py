from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServoChannel:
    servo_id: int
    name: str
    center: int
    direction: int
    safe_min: int
    safe_max: int


@dataclass(frozen=True)
class RuntimeConfig:
    port: str
    baud: int
    camera_index: int
    loop_hz: float
    speed: int
    max_offset_raw: int
    cpg_center_deg: float
    cpg_hardware_scale: float
    nominal_degrees_per_count: float
    servos: dict[int, ServoChannel]


def _finite_positive(value: object, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return number


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    channels: dict[int, ServoChannel] = {}
    for item in payload.get("servos", []):
        channel = ServoChannel(
            servo_id=int(item["id"]),
            name=str(item["name"]),
            center=int(item["center"]),
            direction=int(item["direction"]),
            safe_min=int(item["safe_min"]),
            safe_max=int(item["safe_max"]),
        )
        if channel.servo_id in channels:
            raise ValueError(f"duplicate Servo ID {channel.servo_id}")
        if channel.direction not in (-1, 1):
            raise ValueError(f"direction for ID {channel.servo_id} must be -1 or +1")
        if not channel.safe_min <= channel.center <= channel.safe_max:
            raise ValueError(f"center for ID {channel.servo_id} is outside safe limits")
        channels[channel.servo_id] = channel
    if set(channels) != {0, 1, 2, 3}:
        raise ValueError("config must define Servo IDs 0, 1, 2, and 3")

    config = RuntimeConfig(
        port=str(payload.get("port", "AUTO")),
        baud=int(payload["baud"]),
        camera_index=int(payload.get("camera_index", 0)),
        loop_hz=_finite_positive(payload["loop_hz"], "loop_hz"),
        speed=int(payload["speed"]),
        max_offset_raw=int(payload["max_offset_raw"]),
        cpg_center_deg=float(payload["cpg_center_deg"]),
        cpg_hardware_scale=_finite_positive(
            payload["cpg_hardware_scale"], "cpg_hardware_scale"
        ),
        nominal_degrees_per_count=_finite_positive(
            payload["nominal_degrees_per_count"], "nominal_degrees_per_count"
        ),
        servos=channels,
    )
    if config.baud <= 0 or config.speed <= 0 or config.max_offset_raw <= 0:
        raise ValueError("baud, speed, and max_offset_raw must be greater than zero")
    if not math.isfinite(config.cpg_center_deg):
        raise ValueError("cpg_center_deg must be finite")
    return config

