"""Scrollable Tk editor for all validated local CPG parameters (no Servo writes)."""
from __future__ import annotations

import copy
import math
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from .cpg_profile import EMOTIONS, MODES, load_profile, pasted_defaults, validate_profile


class KeyframeTable(ttk.Frame):
    def __init__(self, parent: tk.Misc, *, title: str, fields: tuple[str, ...], changed):
        super().__init__(parent)
        self.fields = fields
        self.changed = changed
        self.entries: list[tuple[ttk.Frame, list[tk.StringVar]]] = []
        ttk.Label(self, text=title, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(8, 2))
        header = ttk.Frame(self)
        header.pack(fill="x")
        for index, name in enumerate(("Time (s)",) + fields):
            ttk.Label(header, text=name, width=12).grid(row=0, column=index, padx=2)
        self.rows_area = ttk.Frame(self)
        self.rows_area.pack(fill="x")
        self.add_button = ttk.Button(self, text="+ Add keyframe", command=self.add_row)
        self.add_button.pack(anchor="w", pady=(3, 3))
        self.can_edit = True

    def clear(self):
        for frame, _ in self.entries:
            frame.destroy()
        self.entries.clear()

    def add_row(self, values=None):
        if values is None:
            last = self.entries[-1][1][0].get() if self.entries else "-1"
            try:
                next_t = float(last) + 1
            except ValueError:
                next_t = 0
            if next_t > 60:
                return
            values = [str(next_t)] + ["90" if name.startswith("J") else "0.6" for name in self.fields]
        frame = ttk.Frame(self.rows_area)
        frame.pack(anchor="w")
        vars_ = []
        for index, val in enumerate(values):
            variable = tk.StringVar(value=str(val))
            variable.trace_add("write", lambda *_: self.changed())
            entry = ttk.Entry(frame, textvariable=variable, width=13)
            entry.grid(row=0, column=index, padx=2, pady=2)
            entry.state(("!disabled",) if self.can_edit else ("disabled",))
            vars_.append(variable)
        remove = ttk.Button(frame, text="−", width=3, command=lambda: self.remove_row(frame))
        remove.grid(row=0, column=len(values), padx=4)
        remove.state(("!disabled",) if self.can_edit else ("disabled",))
        self.entries.append((frame, vars_))
        self.changed()

    def remove_row(self, frame):
        if len(self.entries) <= 1:
            return
        self.entries = [item for item in self.entries if item[0] != frame]
        frame.destroy()
        self.changed()

    def values(self, poses: bool):
        if not self.entries:
            raise ValueError("A keyframe table cannot be empty")
        output = []
        for _, variables in self.entries:
            numbers = [float(variable.get()) for variable in variables]
            output.append({"time": numbers[0], "angles": numbers[1:]} if poses else {"time": numbers[0], "value": numbers[1]})
        return output

    def set_editable(self, allowed: bool):
        self.can_edit = allowed
        self.add_button.state(("!disabled",) if allowed else ("disabled",))
        for frame, _ in self.entries:
            for child in frame.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Button)):
                    child.state(("!disabled",) if allowed else ("disabled",))


class CpgProfileEditor(ttk.Frame):
    """Edit all live model fields when runtime motion is stopped."""
    def __init__(self, parent: tk.Misc, *, runtime: Any, logger=None):
        super().__init__(parent)
        self.runtime = runtime
        self.logger = logger or (lambda _message: None)
        self.adapter = getattr(runtime, "cpg_source", None)
        self.dt = float(getattr(self.adapter, "dt", 1 / runtime.config.loop_hz if hasattr(runtime, "config") else .05))
        self.path = getattr(self.adapter, "profile_path", None)
        self.profile = (self.adapter.profile_snapshot() if self.adapter is not None and hasattr(self.adapter, "profile_snapshot")
                        else load_profile(dt=self.dt))
        self._loading = False
        self._allowed = True
        self.scalar: dict[str, tk.StringVar] = {}
        self.scalar_entries: list[ttk.Entry] = []
        self.mode_tables: dict[str, tuple[KeyframeTable, KeyframeTable]] = {}
        self.fade_flags: dict[str, tk.BooleanVar] = {}
        self.fade_checks: list[ttk.Checkbutton] = []
        self.status_var = tk.StringVar(value="Applied")
        self._build()
        self.show_profile(self.profile)

    def _changed(self):
        if not self._loading:
            self.status_var.set("Modified — validate before applying")

    def _field(self, parent, key, label, row, *, column=0, width=13):
        ttk.Label(parent, text=label).grid(row=row, column=column * 2, sticky="w", padx=(6, 2), pady=3)
        variable = tk.StringVar()
        variable.trace_add("write", lambda *_: self._changed())
        entry = ttk.Entry(parent, width=width, textvariable=variable)
        entry.grid(row=row, column=column * 2 + 1, sticky="w", padx=(2, 10), pady=3)
        self.scalar[key] = variable
        self.scalar_entries.append(entry)
        return variable

    def _scroll_tab(self, notebook, title):
        holder = ttk.Frame(notebook)
        notebook.add(holder, text=title)
        canvas = tk.Canvas(holder, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        interior = ttk.Frame(canvas, padding=(8, 6))
        winid = canvas.create_window((0, 0), window=interior, anchor="nw")
        interior.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(winid, width=event.width))
        return interior

    def _build(self):
        ttk.Label(self, text="Local CPG profile · saved JSON parameters, not Servo calibration", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", pady=(2, 5))
        note = ("STOP CPG and wait for ARMED before editing. SAVE & APPLY resets camera readiness. "
                "Servo speed, raw offset and hardware scaling stay in robot_config.json.")
        ttk.Label(self, text=note, wraplength=1000, foreground="#a55a00").pack(anchor="w", pady=(0, 6))
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        general = self._scroll_tab(notebook, "Global & Input")
        groups = [
            ("global", (("so2_alpha", "SO(2) alpha"), ("warmup_steps", "Warmup steps"),
                        ("oscillation_blend_s", "Oscillation blend (s)"), ("max_slew_deg_s", "Max slew (°/s)"))),
            ("input_gate", (("min_strength", "Min expression strength"), ("stable_s", "Stable (s)"),
                            ("timeout_s", "Timeout (s)"), ("min_samples", "Min samples"))),
        ]
        for group_name, fields in groups:
            lf = ttk.LabelFrame(general, text=group_name.replace("_", " ").title(), padding=7)
            lf.pack(fill="x", pady=5)
            for row, (key, label) in enumerate(fields):
                self._field(lf, f"{group_name}.{key}", label, row)
        hormones = ttk.LabelFrame(general, text="Hormone memory", padding=7)
        hormones.pack(fill="x", pady=5)
        for row, emotion in enumerate(EMOTIONS):
            self._field(hormones, f"hormones.{emotion}.tau_up", f"{emotion} rise τ (s)", row)
            self._field(hormones, f"hormones.{emotion}.tau_down", "decay τ (s)", row, column=1)
        limits = ttk.LabelFrame(general, text="Joint software limits (°)", padding=7)
        limits.pack(fill="x", pady=5)
        for j in range(4):
            self._field(limits, f"joint_limits.{j}.min", f"J{j+1} minimum", j)
            self._field(limits, f"joint_limits.{j}.max", "maximum", j, column=1)
        for mode in MODES:
            tab = self._scroll_tab(notebook, mode)
            top = ttk.LabelFrame(tab, text=f"{mode} oscillator and LED", padding=7)
            top.pack(fill="x", pady=5)
            self._field(top, f"modes.{mode}.phi", "Phi (radians/tick)", 0)
            self._field(top, f"modes.{mode}.light_color", "LED #RRGGBB", 0, column=1)
            for j in range(4):
                self._field(top, f"modes.{mode}.gains.{j}", f"Gain J{j+1} (°)", j + 1, column=(j % 2))
            pose = KeyframeTable(tab, title="Pose interpolation · time then J1–J4 angles (°)", fields=("J1", "J2", "J3", "J4"), changed=self._changed)
            pose.pack(fill="x", pady=4)
            bright = KeyframeTable(tab, title="LED brightness · time then 0–1 brightness", fields=("Brightness",), changed=self._changed)
            bright.pack(fill="x", pady=4)
            self.mode_tables[mode] = (pose, bright)
            fade = ttk.LabelFrame(tab, text="Optional J4 oscillation fade", padding=7)
            fade.pack(fill="x", pady=5)
            flag = tk.BooleanVar(value=False)
            flag.trace_add("write", lambda *_: self._changed())
            self.fade_flags[mode] = flag
            checkbox = ttk.Checkbutton(fade, text="Enable head fade", variable=flag)
            checkbox.grid(row=0, column=0, sticky="w", pady=2)
            self.fade_checks.append(checkbox)
            self._field(fade, f"modes.{mode}.head_fade.start", "Start (s)", 1)
            self._field(fade, f"modes.{mode}.head_fade.end", "End (s)", 1, column=1)
        footer = ttk.Frame(self)
        footer.pack(fill="x", pady=(7, 0))
        self.buttons = [
            ttk.Button(footer, text="VALIDATE", command=self.validate),
            ttk.Button(footer, text="RELOAD SAVED", command=self.reload),
            ttk.Button(footer, text="RESET TO PASTED DEFAULTS", command=lambda: self.show_profile(pasted_defaults(), mark_modified=True)),
            ttk.Button(footer, text="SAVE & APPLY", command=self.save_apply),
        ]
        for button in self.buttons:
            button.pack(side="left", padx=(0, 8))
        ttk.Label(self, textvariable=self.status_var).pack(fill="x", pady=(4, 0), anchor="w")

    def show_profile(self, profile, *, mark_modified=False):
        self._loading = True
        try:
            for key, variable in self.scalar.items():
                parts = key.split(".")
                if parts[0] == "joint_limits":
                    value = profile["joint_limits"][int(parts[1])][parts[2]]
                elif parts[0] == "modes":
                    m = profile["modes"][parts[1]]
                    if parts[2] == "gains":
                        value = m["gains"][int(parts[3])]
                    elif parts[2] == "head_fade":
                        value = (m["head_fade"] or {"start": 5., "end": 10.})[parts[3]]
                    else:
                        value = m[parts[2]]
                else:
                    node = profile
                    for component in parts:
                        node = node[component]
                    value = node
                variable.set(str(value))
            for mode, (pose, bright) in self.mode_tables.items():
                p = profile["modes"][mode]
                self.fade_flags[mode].set(p["head_fade"] is not None)
                pose.clear()
                bright.clear()
                for row in p["poses"]:
                    pose.add_row([row["time"]] + row["angles"])
                for row in p["brightness"]:
                    bright.add_row([row["time"], row["value"]])
        finally:
            self._loading = False
        self.status_var.set("Modified — validate before applying" if mark_modified else "Applied")

    def _parse(self):
        profile = copy.deepcopy(self.profile)
        for key, variable in self.scalar.items():
            parts = key.split(".")
            raw = variable.get().strip()
            if not raw:
                raise ValueError(f"{key}: empty value")
            if key.endswith("light_color"):
                value = raw
            elif key.endswith("warmup_steps") or key.endswith("min_samples"):
                value = int(raw)
            else:
                value = float(raw)
            if parts[0] == "joint_limits":
                profile["joint_limits"][int(parts[1])][parts[2]] = value
            elif parts[0] == "modes":
                m = profile["modes"][parts[1]]
                if parts[2] == "gains":
                    m["gains"][int(parts[3])] = value
                elif parts[2] == "head_fade":
                    if m["head_fade"] is None:
                        m["head_fade"] = {"start": 5., "end": 10.}
                    m["head_fade"][parts[3]] = value
                else:
                    m[parts[2]] = value
            else:
                node = profile
                for component in parts[:-1]:
                    node = node[component]
                node[parts[-1]] = value
        for mode, (pose, bright) in self.mode_tables.items():
            m = profile["modes"][mode]
            m["poses"] = pose.values(True)
            m["brightness"] = bright.values(False)
            if not self.fade_flags[mode].get():
                m["head_fade"] = None
        return validate_profile(profile, dt=self.dt)

    def validate(self):
        try:
            self._parse()
        except (ValueError, TypeError, OverflowError) as exc:
            self.status_var.set(f"Invalid: {exc}")
            return False
        self.status_var.set("Valid — not saved yet")
        return True

    def reload(self):
        try:
            profile = load_profile(self.path, dt=self.dt) if self.path else load_profile(dt=self.dt)
            self.profile = profile
            self.show_profile(profile)
            self.status_var.set("Reloaded saved file (not applied; SAVE & APPLY to activate)")
        except Exception as exc:
            self.status_var.set(f"Reload failed: {exc}")

    def save_apply(self):
        if not self._allowed:
            self.status_var.set("Stop CPG and wait for ARMED before editing")
            return False
        try:
            clean = self._parse()
            if not hasattr(self.runtime, "apply_cpg_profile"):
                raise RuntimeError("Active runtime does not support CPG profile changes")
            self.runtime.apply_cpg_profile(clean)
            self.profile = clean
            self.status_var.set("Applied — profile saved; new camera observation required")
            self.logger("CPG profile Saved & Applied (camera readiness reset)")
            return True
        except Exception as exc:
            self.status_var.set(f"Save & Apply failed: {exc}")
            return False

    def set_editable(self, allowed: bool):
        if self._allowed == allowed:
            return
        self._allowed = allowed
        for entry in self.scalar_entries:
            entry.state(("!disabled",) if allowed else ("disabled",))
        for checkbox in self.fade_checks:
            checkbox.state(("!disabled",) if allowed else ("disabled",))
        for tables in self.mode_tables.values():
            for table in tables:
                table.set_editable(allowed)
        for button in self.buttons:
            button.state(("!disabled",) if allowed else ("disabled",))
