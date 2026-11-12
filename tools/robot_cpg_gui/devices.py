from __future__ import annotations

from typing import Sequence


def serial_candidates() -> list[str]:
    found: list[str] = []
    try:
        from serial.tools import list_ports

        ports = list(list_ports.comports())
        tokens = ("CH343", "CH340", "CH341", "1A86:55D4", "1A86:7523")
        for item in sorted(ports, key=lambda candidate: candidate.device):
            identity = " ".join(
                str(getattr(item, field, "") or "").upper()
                for field in ("description", "manufacturer", "product", "hwid")
            )
            if any(token in identity for token in tokens):
                found.append(item.device)
    except ImportError:
        pass
    return list(dict.fromkeys(found))


def resolve_serial_port(requested: str, *, candidates: Sequence[str] | None = None) -> str:
    value = str(requested).strip()
    if value and value.upper() != "AUTO":
        return value
    available = list(serial_candidates() if candidates is None else candidates)
    if not available:
        raise RuntimeError(
            "cannot auto-detect CH343; connect it or pass --port /dev/ttyUSB0 (Windows: --port COM8)"
        )
    return str(available[0])

