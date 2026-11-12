from __future__ import annotations

from collections.abc import Mapping, Sequence

from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime

prepare_tcl_runtime()

import tkinter as tk
from tkinter import ttk


DEFAULT_COLORS = ("#00a6d6", "#f28e2b", "#59a14f", "#d66fd1")


class LinePlot(ttk.Frame):
    def __init__(
        self,
        parent,
        *,
        title: str,
        names: Sequence[str],
        window_seconds: float = 10.0,
        height: int = 270,
    ) -> None:
        super().__init__(parent, padding=4)
        self.names = tuple(names)
        self.colors = {
            name: DEFAULT_COLORS[index % len(DEFAULT_COLORS)]
            for index, name in enumerate(self.names)
        }
        self.window_seconds = float(window_seconds)
        ttk.Label(self, text=title, font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(
            self, height=height, background="#111820", highlightthickness=1,
            highlightbackground="#58616b",
        )
        self.canvas.pack(fill="both", expand=True, pady=(3, 0))
        legend = ttk.Frame(self)
        legend.pack(fill="x", pady=(3, 0))
        for index, name in enumerate(self.names):
            tk.Label(legend, text=f"  {name}  ", fg=self.colors[name]).grid(
                row=0, column=index, sticky="w"
            )

    def draw(
        self,
        series: Mapping[str, Sequence[tuple[float, float]]],
        *,
        now: float,
        y_range: tuple[float, float] | None = None,
    ) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(320, canvas.winfo_width())
        height = max(160, canvas.winfo_height())
        left, right, top, bottom = 48, width - 12, 12, height - 28
        if right <= left or bottom <= top:
            return

        values = [value for points in series.values() for _, value in points]
        if y_range is None:
            if values:
                low, high = min(values), max(values)
                padding = max(1.0, (high - low) * 0.12)
                if high == low:
                    padding = max(1.0, abs(high) * 0.1)
                low, high = low - padding, high + padding
            else:
                low, high = -1.0, 1.0
        else:
            low, high = map(float, y_range)
        if high <= low:
            low, high = low - 1.0, high + 1.0

        for division in range(5):
            fraction = division / 4
            y = top + fraction * (bottom - top)
            value = high - fraction * (high - low)
            canvas.create_line(left, y, right, y, fill="#293640")
            canvas.create_text(left - 5, y, text=f"{value:.1f}", fill="#c4ccd4", anchor="e")
        for division in range(6):
            fraction = division / 5
            x = left + fraction * (right - left)
            seconds_ago = self.window_seconds * (1.0 - fraction)
            canvas.create_line(x, top, x, bottom, fill="#22303a")
            canvas.create_text(x, bottom + 14, text=f"-{seconds_ago:.0f}s", fill="#aeb8c2")

        start = float(now) - self.window_seconds
        for name in self.names:
            coordinates: list[float] = []
            for timestamp, value in series.get(name, ()):
                fraction_x = (float(timestamp) - start) / self.window_seconds
                fraction_y = (float(value) - low) / (high - low)
                x = left + max(0.0, min(1.0, fraction_x)) * (right - left)
                y = bottom - max(0.0, min(1.0, fraction_y)) * (bottom - top)
                coordinates.extend((x, y))
            if len(coordinates) >= 4:
                canvas.create_line(*coordinates, fill=self.colors[name], width=2, smooth=True)
            elif len(coordinates) == 2:
                x, y = coordinates
                canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=self.colors[name], outline="")

