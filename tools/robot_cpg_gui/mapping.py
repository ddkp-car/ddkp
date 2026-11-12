from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .config import RuntimeConfig


class HardwareMapper:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def targets_from_cpg(self, q_deg: Sequence[float], *, allow_extended_j1: bool = False,
                         allow_extended_j3: bool = False) -> dict[int, int]:
        """Translate commands to raw, with an opt-in wider J1 camera pan.

        Manual/CPG commands use the unchanged global max_offset_raw limit.
        Camera scan may extend J1, and camera standby may extend J3 toward
        its requested -45-degree offset. Neither option bypasses the per-servo
        calibrated safe_min/safe_max. Normal/manual CPG keeps ±max_offset_raw.
        """
        if len(q_deg) != 4:
            raise ValueError("CPG output must contain exactly four joints")
        values = [float(value) for value in q_deg]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("CPG output must contain only finite values")

        counts_per_degree = 1.0 / self.config.nominal_degrees_per_count
        targets: dict[int, int] = {}
        for index, servo_id in enumerate(sorted(self.config.servos)):
            servo = self.config.servos[servo_id]
            cpg_delta = values[index] - self.config.cpg_center_deg
            requested_delta = round(
                servo.direction
                * cpg_delta
                * counts_per_degree
                * self.config.cpg_hardware_scale
            )
            if (index == 0 and allow_extended_j1) or (index == 2 and allow_extended_j3):
                limited_delta = requested_delta
            else:
                limited_delta = min(
                    self.config.max_offset_raw,
                    max(-self.config.max_offset_raw, requested_delta),
                )
            target = servo.center + limited_delta
            targets[servo_id] = min(servo.safe_max, max(servo.safe_min, target))
        return targets

    def feedback_relative_deg(
        self, raw_by_id: Mapping[int, int]
    ) -> dict[int, float]:
        if set(raw_by_id) != set(self.config.servos):
            raise ValueError("feedback must contain exactly Servo IDs 0, 1, 2, and 3")
        return {
            servo_id: (
                self.config.servos[servo_id].direction
                * (int(raw_by_id[servo_id]) - self.config.servos[servo_id].center)
                * self.config.nominal_degrees_per_count
            )
            for servo_id in sorted(self.config.servos)
        }

    def feedback_absolute_deg(
        self, raw_by_id: Mapping[int, int]
    ) -> dict[int, float]:
        relative = self.feedback_relative_deg(raw_by_id)
        return {
            servo_id: self.config.cpg_center_deg + relative[servo_id]
            for servo_id in sorted(self.config.servos)
        }

