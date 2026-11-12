from __future__ import annotations

import argparse
import math
import os
import queue
import threading
import time
from dataclasses import dataclass
from dataclasses import replace as replace_dataclass
from pathlib import Path
from typing import Any, Callable

from tools.servo_calibration_gui.tcl_bootstrap import prepare_tcl_runtime

prepare_tcl_runtime()

import tkinter as tk
from tkinter import scrolledtext, ttk

from .camera import CameraMailbox
from .camera_discovery import CameraChoice, choose_camera, discover_cameras
from .config import RuntimeConfig, load_runtime_config
from .cpg_editor import CpgProfileEditor
from .devices import resolve_serial_port
from .history import RollingHistory
from .keyboard_hotkeys import WindowsNumpadHotkeys
from .mapping import HardwareMapper
from .plot_widget import LinePlot
from .runtime import RobotRuntime, RuntimeCommand, RuntimeSnapshot, RuntimeState
from .face_search import FaceSearchController, load_face_search_settings
from .led_gpio import (
    DEFAULT_LED_GPIO_PATH, LedGpioConfig, LedGpioDriver,
    load_led_config, save_led_config,
)


# Tk produces two keysyms for each physical Numpad key depending on Num Lock.
# Top-row digits are intentionally not shortcuts, to protect CPG Settings editing.
SERVO_DISPLAY_NAMES = {0: "Base", 1: "Shoulder", 2: "Elbow", 3: "Head"}

NUMPAD_PERSONALITIES = {
    "KP_1": "Happy", "KP_End": "Happy",
    "KP_2": "Sad", "KP_Down": "Sad",
    "KP_3": "Confused", "KP_Next": "Confused",
    "KP_4": "Neutral", "KP_Left": "Neutral",
    "KP_0": None, "KP_Insert": None,  # AUTO: return to camera
}


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[1]
DEFAULT_CONFIG_PATH = APP_DIR / "robot_config.json"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "face_vision(maincpg)" / "models" / "face_landmarker.task"


@dataclass(frozen=True)
class UiProjection:
    can_connect: bool
    can_arm: bool
    can_center: bool
    can_start_cpg: bool
    can_stop_cpg: bool
    can_emergency: bool
    can_start_camera: bool
    can_stop_camera: bool


def project_ui(snapshot: RuntimeSnapshot, *, camera_running: bool, manual_ready: bool = False) -> UiProjection:
    state = snapshot.state
    connected = state not in (RuntimeState.DISCONNECTED, RuntimeState.CLOSED)
    can_reconnect = state in (
        RuntimeState.DISCONNECTED,
        RuntimeState.FAULT,
        RuntimeState.EMERGENCY,
    )
    return UiProjection(
        can_connect=can_reconnect,
        can_arm=state is RuntimeState.CONNECTED,
        can_center=state is RuntimeState.ARMED,
        can_start_cpg=(
            state is RuntimeState.ARMED
            and snapshot.center_ready
            and (camera_running or manual_ready)
        ),
        can_stop_cpg=state in (RuntimeState.RUNNING, RuntimeState.STOPPING),
        can_emergency=connected,
        can_start_camera=not camera_running and state is not RuntimeState.CLOSED,
        can_stop_camera=camera_running,
    )


class RobotCpgApp:
    POLL_MS = 100

    def __init__(
        self,
        root: tk.Tk,
        config: RuntimeConfig,
        mapper: HardwareMapper,
        runtime: RobotRuntime,
        camera_mailbox: CameraMailbox,
        *,
        camera_factory: Callable[[int], Any] | None,
        mock_mode: bool,
        camera_discovery: Callable[[], list[CameraChoice]] | None = None,
    ) -> None:
        self.root = root
        self.config = config
        self.mapper = mapper
        self.runtime = runtime
        self.camera_mailbox = camera_mailbox
        self.camera_factory = camera_factory
        self.camera_discovery = camera_discovery or discover_cameras
        self.mock_mode = bool(mock_mode)
        self.camera_worker: Any | None = None
        self.camera_running = False
        self.closed = False
        self.closing = False
        self._camera_stop_requested = False
        self._poll_after_id: str | None = None
        self._last_snapshot_time = -math.inf
        self._last_preview_sequence = 0
        self._last_state: RuntimeState | None = None
        self._preview_photo = None
        self._camera_choices: list[CameraChoice] = []
        self._camera_scan_queue: queue.Queue[tuple[list[CameraChoice], str]] = queue.Queue()
        self._camera_scan_thread: threading.Thread | None = None

        self.servo_names = tuple(
            f"ID{servo_id} {SERVO_DISPLAY_NAMES.get(servo_id, config.servos[servo_id].name)}"
            for servo_id in sorted(config.servos)
        )
        self.cpg_names = tuple(f"J{index}" for index in range(1, 5))
        self.servo_history = RollingHistory(self.servo_names, window_seconds=10.0)
        self.cpg_history = RollingHistory(self.cpg_names, window_seconds=10.0)

        self.state_var = tk.StringVar(value="DISCONNECTED")
        self.camera_var = tk.StringVar(value="Camera stopped")
        self.camera_choice_var = tk.StringVar(value="")
        self.emotion_var = tk.StringVar(value="Neutral")
        self.personality_var = tk.StringVar(value="AUTO (Camera)")
        self.keyboard_status_var = tk.StringVar(value="Numpad: GUI keyboard shortcuts")
        self.global_numpad_var = tk.BooleanVar(value=(os.name == "nt"))
        self._global_numpad: WindowsNumpadHotkeys | None = None
        self.input_var = tk.StringVar(value="not_started")
        self.fault_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="Face Search: OFF")
        self.stage_var = tk.StringVar(value="Stage: BASE")
        self.observation_timer_var = tk.StringVar(value="Observed: Happy 0.0s | Sad 0.0s | Confused 0.0s | Neutral 0.0s")
        self._last_stage = "BASE"
        self.led_var = tk.StringVar(value="GPIO LED: Disabled / reserved")
        self.led_config = load_led_config()
        self._led_driver: LedGpioDriver | None = None
        self._led_error = ""
        # Mock must never initialize physical GPIO even if the saved LED
        # configuration is enabled for real Raspberry Pi operation.
        if not self.mock_mode:
            try:
                self._led_driver = LedGpioDriver(self.led_config)
            except Exception as exc:
                self._led_error = str(exc)
        self.hormone_vars = {
            name: tk.DoubleVar(value=0.0) for name in ("Happy", "Sad", "Confused")
        }

        self._build_ui()
        if self.mock_mode:
            self._set_camera_choices(
                [CameraChoice(config.camera_index, f"Mock Camera (index {config.camera_index})")]
            )
            self.camera_var.set("Mock camera ready")
        else:
            self.root.after_idle(self._begin_camera_scan)
        # Tk receives key events when GUI is foreground. On Windows additionally
        # install native Numpad hotkeys so clicking the GUI is not required.
        self.root.bind_all("<KeyPress>", self._on_numpad_key, add="+")
        self.root.bind_all("<FocusIn>", self._on_keyboard_focus_change, add="+")
        self.root.bind_all("<FocusOut>", self._on_keyboard_focus_change, add="+")
        if self.global_numpad_var.get():
            self._toggle_global_numpad()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll()

    def _build_ui(self) -> None:
        mode_label = "MOCK" if self.mock_mode else "REAL HARDWARE"
        self.root.title(f"Emotion Robotic Lamp — Camera CPG Control [{mode_label}]")
        self.root.geometry("1380x900")
        self.root.minsize(1120, 740)
        try:
            ttk.Style(self.root).theme_use("clam")
        except tk.TclError:
            pass

        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        controls = ttk.LabelFrame(outer, text="1) Hardware / Motion Control", padding=7)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(controls, text=f"Port: {self.config.port}").grid(row=0, column=0, padx=4)
        ttk.Label(controls, text=f"Baud: {self.config.baud}").grid(row=0, column=1, padx=4)
        ttk.Label(controls, text="State:").grid(row=0, column=2, padx=(15, 2))
        ttk.Label(controls, textvariable=self.state_var, width=14).grid(row=0, column=3, padx=2)

        self.connect_button = ttk.Button(
            controls, text="CONNECT", command=lambda: self.runtime.submit(RuntimeCommand.CONNECT)
        )
        self.arm_button = ttk.Button(
            controls, text="ENABLE MOTION", command=lambda: self.runtime.submit(RuntimeCommand.ARM)
        )
        self.center_button = ttk.Button(
            controls, text="GO CENTER", command=lambda: self.runtime.submit(RuntimeCommand.CENTER)
        )
        self.start_cpg_button = ttk.Button(
            controls, text="START CPG", command=lambda: self.runtime.submit(RuntimeCommand.START_CPG)
        )
        self.stop_cpg_button = ttk.Button(
            controls, text="STOP → CENTER", command=lambda: self.runtime.submit(RuntimeCommand.STOP_CPG)
        )
        buttons = (
            self.connect_button, self.arm_button, self.center_button,
            self.start_cpg_button, self.stop_cpg_button,
        )
        for column, button in enumerate(buttons, start=4):
            button.grid(row=0, column=column, padx=3, pady=2)

        self.emergency_button = tk.Button(
            controls,
            text="EMERGENCY\nTORQUE OFF",
            command=self._emergency,
            background="#c41414",
            foreground="white",
            activebackground="#8d0000",
            activeforeground="white",
            font=("TkDefaultFont", 11, "bold"),
            width=18,
            height=2,
        )
        self.emergency_button.grid(row=0, column=9, rowspan=2, padx=(12, 2), sticky="e")
        controls.columnconfigure(9, weight=1)
        ttk.Label(
            controls,
            text="STOP CPG: return to center | EMERGENCY: torque off immediately; no return movement",
            foreground="#a55a00",
        ).grid(row=1, column=0, columnspan=9, sticky="w", padx=4, pady=(4, 0))

        camera_and_status = ttk.Frame(outer)
        camera_and_status.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        camera_and_status.columnconfigure(1, weight=1)

        camera_frame = ttk.LabelFrame(camera_and_status, text="2) USB Camera", padding=6)
        camera_frame.grid(row=0, column=0, sticky="nw", padx=(0, 7))
        camera_frame.columnconfigure(0, weight=1)
        self.camera_combo = ttk.Combobox(
            camera_frame,
            textvariable=self.camera_choice_var,
            values=(),
            state="readonly",
            width=30,
        )
        self.camera_scan_button = ttk.Button(
            camera_frame, text="SCAN CAMERAS", command=self._begin_camera_scan
        )
        self.camera_combo.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=(0, 5))
        self.camera_scan_button.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=(0, 5))
        self.preview_label = tk.Label(
            camera_frame, text="Camera preview", width=44, height=14,
            background="#101010", foreground="#d0d0d0",
        )
        self.preview_label.grid(row=1, column=0, columnspan=2)
        self.camera_start_button = ttk.Button(camera_frame, text="START CAMERA", command=self._start_camera)
        self.camera_stop_button = ttk.Button(camera_frame, text="STOP CAMERA", command=self._stop_camera)
        self.camera_start_button.grid(row=2, column=0, sticky="ew", pady=4, padx=(0, 2))
        self.camera_stop_button.grid(row=2, column=1, sticky="ew", pady=4, padx=(2, 0))
        ttk.Label(camera_frame, textvariable=self.camera_var).grid(row=3, column=0, columnspan=2, sticky="w")

        status_frame = ttk.LabelFrame(camera_and_status, text="3) Emotion / Hormone / Fault", padding=8)
        status_frame.grid(row=0, column=1, sticky="nsew")
        ttk.Label(status_frame, text="Emotion", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.emotion_var, font=("TkDefaultFont", 18, "bold")).grid(row=0, column=1, sticky="w", padx=10)
        ttk.Label(status_frame, text="Input status").grid(row=1, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.input_var).grid(row=1, column=1, sticky="w", padx=10)
        for row, name in enumerate(("Happy", "Sad", "Confused"), start=2):
            ttk.Label(status_frame, text=f"H {name}").grid(row=row, column=0, sticky="w", pady=2)
            ttk.Progressbar(
                status_frame, variable=self.hormone_vars[name], maximum=1.0, length=300
            ).grid(row=row, column=1, sticky="ew", padx=10)
        ttk.Label(status_frame, text="FAULT", foreground="#b00000").grid(row=5, column=0, sticky="nw", pady=(8, 0))
        ttk.Label(status_frame, textvariable=self.fault_var, foreground="#b00000", wraplength=650).grid(
            row=5, column=1, sticky="w", padx=10, pady=(8, 0)
        )
        personality_frame = ttk.Frame(status_frame)
        personality_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        ttk.Label(personality_frame, text="Automatic emotion detection:").pack(side="left", padx=(0, 6))
        ttk.Button(
            personality_frame, text="0 AUTO — SEARCH FACE", command=self._select_camera_auto
        ).pack(side="left", padx=2)
        ttk.Label(personality_frame, text="Numpad 1–4: optional manual override").pack(side="left", padx=8)
        ttk.Label(status_frame, textvariable=self.personality_var, foreground="#126b8b").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(2, 0)
        )
        keyboard_frame = ttk.Frame(status_frame)
        keyboard_frame.grid(row=8, column=0, columnspan=2, sticky="w", pady=(3, 0))
        if os.name == "nt":
            ttk.Checkbutton(
                keyboard_frame, text="Global Numpad (Num Lock ON)",
                variable=self.global_numpad_var, command=self._toggle_global_numpad,
            ).pack(side="left", padx=(0, 8))
        ttk.Label(keyboard_frame, textvariable=self.keyboard_status_var).pack(side="left")
        ttk.Label(status_frame, textvariable=self.search_var, foreground="#805c10").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Label(status_frame, textvariable=self.led_var, foreground="#346056").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(2, 0)
        )
        ttk.Label(status_frame, textvariable=self.stage_var, foreground="#7c3997").grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(2, 0)
        )
        ttk.Label(status_frame, textvariable=self.observation_timer_var, foreground="#126b8b").grid(
            row=12, column=0, columnspan=2, sticky="w", pady=(2, 0)
        )
        status_frame.columnconfigure(1, weight=1)

        notebook = ttk.Notebook(outer)
        notebook.grid(row=2, column=0, sticky="nsew")
        graph_tab = ttk.Frame(notebook, padding=6)
        servo_tab = ttk.Frame(notebook, padding=6)
        notebook.add(graph_tab, text="Live Graphs")
        notebook.add(servo_tab, text="Servo Feedback / Log")
        settings_tab = ttk.Frame(notebook, padding=6)
        notebook.add(settings_tab, text="CPG Settings")
        gpio_tab = ttk.Frame(notebook, padding=15)
        notebook.add(gpio_tab, text="GPIO / LED (Pi)")
        self._build_gpio_settings(gpio_tab)
        self.cpg_editor = CpgProfileEditor(settings_tab, runtime=self.runtime, logger=self._write_log)
        self.cpg_editor.pack(fill="both", expand=True)
        graph_tab.columnconfigure(0, weight=1)
        graph_tab.columnconfigure(1, weight=1)
        graph_tab.rowconfigure(0, weight=1)
        self.servo_plot = LinePlot(
            graph_tab,
            title="Servo Feedback — absolute joint angle (degrees)",
            names=self.servo_names,
        )
        self.cpg_plot = LinePlot(
            graph_tab,
            title="Command — CPG / Face Search / Tracking (degrees)",
            names=self.cpg_names,
        )
        self.servo_plot.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        self.cpg_plot.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        ttk.Label(
            graph_tab,
            text=(f"Direct drive 1:1 · Center=90° · 1° ≈ {1 / self.config.nominal_degrees_per_count * self.config.cpg_hardware_scale:.2f} raw "
                  f"(scale={self.config.cpg_hardware_scale:g}) · limit ±{self.config.max_offset_raw} raw · physical feedback remains authoritative."),
            foreground="#a55a00",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        columns = ("id", "joint", "raw", "angle", "target", "speed", "moving", "torque")
        self.servo_tree = ttk.Treeview(servo_tab, columns=columns, show="headings", height=5)
        headings = {
            "id": "ID", "joint": "Joint", "raw": "Raw", "angle": "Absolute ° (nominal)",
            "target": "Target raw", "speed": "Speed", "moving": "Moving", "torque": "Torque",
        }
        for name in columns:
            self.servo_tree.heading(name, text=headings[name])
            self.servo_tree.column(name, width=110 if name != "joint" else 150, anchor="center")
        self.servo_tree.pack(fill="x")
        for servo_id in sorted(self.config.servos):
            servo = self.config.servos[servo_id]
            self.servo_tree.insert(
                "", "end", iid=str(servo_id),
                values=(servo_id, servo.name, "—", "—", servo.center, "—", "—", "OFF"),
            )
        self.log_box = scrolledtext.ScrolledText(servo_tab, height=13, state="disabled")
        self.log_box.pack(fill="both", expand=True, pady=(7, 0))

    def _build_gpio_settings(self, container: ttk.Frame) -> None:
        ttk.Label(container, text="Raspberry Pi 5 — WS2812 SPI GPIO10 / Pin 19 + PWM", font=("TkDefaultFont", 14, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10)
        )
        ttk.Label(container,
                  text="WS2812 5V ONLY: Signal/DIN -> BCM10 (physical pin 19) via 74AHCT125 logic shifter."
                       "  Connect 5V + GND correctly. Verify LED model before enabling.").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(0, 8)
        )
        self.led_enabled_var = tk.BooleanVar(value=self.led_config.enabled)
        self.led_mode_var = tk.StringVar(value=self.led_config.mode)
        self.led_pin_vars = {
            key: tk.StringVar(value="" if getattr(self.led_config, key) is None else str(getattr(self.led_config, key)))
            for key in ("mono_bcm", "red_bcm", "green_bcm", "blue_bcm")
        }
        self.led_active_high_var = tk.BooleanVar(value=self.led_config.active_high)
        self.led_pixel_count_var = tk.StringVar(value=str(self.led_config.pixel_count))
        self.led_pixel_order_var = tk.StringVar(value=self.led_config.pixel_order)
        self.led_max_brightness_var = tk.StringVar(value=str(self.led_config.max_brightness))
        ttk.Checkbutton(container, text="Enable GPIO output (ONLY after wiring/model checked)",
                        variable=self.led_enabled_var).grid(row=2, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(container, text="LED Type:").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(container, state="readonly", textvariable=self.led_mode_var,
                     values=("ws2812_spi", "ws2812_pio", "mono", "rgb"), width=16).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(container, text="SPI0 MOSI: BCM10 / physical pin 19 (selected); PIO BCM12 / pin 32 is optional").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(5, 3))
        ttk.Label(container, text="Number of addressable LED pixels:").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(container, textvariable=self.led_pixel_count_var, width=12).grid(row=5, column=1, sticky="w", pady=3)
        ttk.Label(container, text="Pixel order:").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Combobox(container, state="readonly", textvariable=self.led_pixel_order_var,
                     values=("GRB", "RGB"), width=12).grid(row=6, column=1, sticky="w", pady=3)
        ttk.Label(container, text="Max LED brightness (0.01–0.5):").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Entry(container, textvariable=self.led_max_brightness_var, width=12).grid(row=7, column=1, sticky="w", pady=3)
        ttk.Label(container, text="Below: for old single-color / analog R/G/B PWM only:").grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(12, 3))
        for row, (key, title) in enumerate((("mono_bcm", "Mono GPIO BCM"), ("red_bcm", "Red GPIO BCM"),
                                            ("green_bcm", "Green GPIO BCM"), ("blue_bcm", "Blue GPIO BCM")), start=9):
            ttk.Label(container, text=title).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(container, textvariable=self.led_pin_vars[key], width=12).grid(row=row, column=1, sticky="w", pady=3)
        ttk.Checkbutton(container, text="Active high (analog PWM only)", variable=self.led_active_high_var).grid(
            row=13, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Button(container, text="SAVE GPIO CONFIG (restart to apply)", command=self._save_gpio_settings).grid(
            row=14, column=0, columnspan=3, sticky="w", pady=(10, 5))
        self.gpio_save_status = tk.StringVar(value="GPIO output OFF by default")
        ttk.Label(container, textvariable=self.gpio_save_status).grid(row=15, column=0, columnspan=3, sticky="w")
        ttk.Label(container, text=("SPI BCM10: enable SPI0 and check /dev/spidev0.0.\n"
                                  "DIN is NOT DOUT. Use 74AHCT125 to shift data from 3.3V to 5V.\n"
                                  "Do not power unknown 3-wire LED from 5V. For strips, use adequate\n"
                                  "external supply and common GND; GPIO supplies data only.")).grid(
            row=16, column=0, columnspan=4, sticky="w", pady=16)

    def _save_gpio_settings(self) -> None:
        try:
            pins = {key: (None if not var.get().strip() else int(var.get().strip()))
                    for key, var in self.led_pin_vars.items()}
            selected = LedGpioConfig(enabled=self.led_enabled_var.get(), mode=self.led_mode_var.get(),
                                     active_high=self.led_active_high_var.get(),
                                     data_bcm=(12 if self.led_mode_var.get() == "ws2812_pio" else 10),
                                     pixel_count=int(self.led_pixel_count_var.get()),
                                     pixel_order=self.led_pixel_order_var.get(),
                                     max_brightness=float(self.led_max_brightness_var.get()), **pins)
            save_led_config(DEFAULT_LED_GPIO_PATH, selected)
            self.gpio_save_status.set("GPIO settings saved — restart application to apply")
            self._write_log("GPIO/LED configuration saved (restart required, outputs unchanged)")
        except (ValueError, OSError) as exc:
            self.gpio_save_status.set("GPIO error: " + str(exc))

    def _poll_led(self, snapshot: RuntimeSnapshot) -> None:
        if self._led_error:
            self.led_var.set("GPIO LED error: " + self._led_error)
            return
        if self._led_driver is None:
            return
        if not self.led_config.enabled:
            self.led_var.set("GPIO LED: disabled (pins reserved)")
            return
        try:
            enabled = snapshot.state is RuntimeState.RUNNING and not self.closing
            self._led_driver.update(snapshot.light or {}, enabled=enabled)
            label = (f"PIO WS2812 BCM12 ({self.led_config.pixel_count} px)" if self.led_config.mode == "ws2812_pio"
                     else f"SPI WS2812 BCM10 ({self.led_config.pixel_count} px)" if self.led_config.mode == "ws2812_spi"
                     else f"{self.led_config.mode.upper()} PWM")
            self.led_var.set(f"GPIO LED: {label} active" if enabled else "GPIO LED: OFF")
        except Exception as exc:
            self._led_error = str(exc)
            self._led_driver.off()
            self.led_var.set("GPIO LED error (OFF): " + str(exc))

    def _poll(self) -> None:
        if self.closed or self.closing:
            return
        # Hotkey worker never touches Tk or CPG; consume its queue on GUI thread.
        self._poll_numpad_keyboard()
        snapshot = self.runtime.latest_snapshot()
        self._update_from_snapshot(snapshot)
        self._poll_camera_scan()
        self._poll_camera()
        self._poll_led(snapshot)
        self.search_var.set(f"Face Search: {snapshot.camera_behavior}" +
                            (f" | face x={snapshot.face_x:.2f}" if snapshot.face_x is not None else "") +
                            (f" y={snapshot.face_y:.2f}" if snapshot.face_y is not None else ""))
        times = snapshot.observed_seconds or {}
        self.observation_timer_var.set("Observed: " + " | ".join(
            f"{name} {times.get(name, 0.0):.1f}s"
            for name in ("Happy", "Sad", "Confused", "Neutral")
        ))
        stage_label = snapshot.personality_stage
        self.stage_var.set(f"Emotion Stage: {stage_label}" +
                           (f" | peak hold {snapshot.peak_held_s:.1f}s" if stage_label != "BASE" else ""))
        if stage_label != self._last_stage:
            if stage_label.startswith("STAGE2"):
                self._write_log(f"Emotion Stage 2 activated: {stage_label}")
            self._last_stage = stage_label
        manual_mode = self._selected_manual_mode()
        self.personality_var.set(f"MANUAL: {manual_mode}" if manual_mode else "AUTO (Camera)")
        self._apply_projection(project_ui(
            snapshot, camera_running=self.camera_running,
            manual_ready=manual_mode is not None,
        ))
        self.cpg_editor.set_editable(
            not self.closing and snapshot.state in (
                RuntimeState.DISCONNECTED, RuntimeState.CONNECTED, RuntimeState.ARMED
            )
        )
        self._poll_after_id = self.root.after(self.POLL_MS, self._poll)

    def _update_from_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self.state_var.set(snapshot.state.value)
        self.emotion_var.set(snapshot.emotion)
        self.input_var.set(snapshot.input_status)
        self.fault_var.set(snapshot.fault_text)
        for name, variable in self.hormone_vars.items():
            variable.set(float(snapshot.hormones.get(name, 0.0)))
        if snapshot.state is not self._last_state:
            self._write_log(f"State → {snapshot.state.value}")
            self._last_state = snapshot.state

        if snapshot.timestamp > self._last_snapshot_time:
            if set(snapshot.feedback_raw) == set(self.config.servos):
                absolute = self.mapper.feedback_absolute_deg(snapshot.feedback_raw)
                self.servo_history.append(
                    snapshot.timestamp,
                    {
                        self.servo_names[index]: absolute[servo_id]
                        for index, servo_id in enumerate(sorted(self.config.servos))
                    },
                )
            self.cpg_history.append(
                snapshot.timestamp,
                {name: snapshot.cpg_deg[index] for index, name in enumerate(self.cpg_names)},
            )
            self._last_snapshot_time = snapshot.timestamp

        now = max(0.0, time.monotonic())
        servo_series = self.servo_history.series(now)
        cpg_series = self.cpg_history.series(now)
        all_angles = [value for series in (servo_series, cpg_series)
                      for trace in series.values() for _, value in trace]
        graph_low = min([60.0] + all_angles)
        graph_high = max([120.0] + all_angles)
        graph_range = (graph_low - 3.0, graph_high + 3.0)
        self.servo_plot.draw(servo_series, now=now, y_range=graph_range)
        self.cpg_plot.draw(cpg_series, now=now, y_range=graph_range)
        angles = (
            self.mapper.feedback_absolute_deg(snapshot.feedback_raw)
            if set(snapshot.feedback_raw) == set(self.config.servos)
            else {}
        )
        for servo_id in sorted(self.config.servos):
            servo = self.config.servos[servo_id]
            raw = snapshot.feedback_raw.get(servo_id, "—")
            angle = f"{angles[servo_id]:.2f}" if servo_id in angles else "—"
            target = snapshot.target_raw.get(servo_id, servo.center)
            speed = snapshot.feedback_speed.get(servo_id, "—")
            moving = int(snapshot.feedback_moving.get(servo_id, False)) if servo_id in snapshot.feedback_moving else "—"
            torque = "ON" if snapshot.feedback_torque.get(servo_id, False) else "OFF"
            self.servo_tree.item(
                str(servo_id),
                values=(servo_id, SERVO_DISPLAY_NAMES.get(servo_id, servo.name), raw, angle, target, speed, moving, torque),
            )

    def _poll_camera(self) -> None:
        packet = self.camera_mailbox.latest_after(self._last_preview_sequence)
        if packet is None:
            if self.camera_worker is not None and not self.camera_worker.is_alive():
                if self._camera_stop_requested:
                    self._complete_camera_stop()
                else:
                    self.camera_running = False
                    self.camera_worker = None
                    self.runtime.submit(RuntimeCommand.CAMERA_AUTO_OFF)
            return
        self._last_preview_sequence = packet.sequence
        if packet.error:
            if packet.error == "camera stopped":
                self.camera_var.set("Camera stopped")
            else:
                self.camera_var.set(f"Camera fault: {packet.error}")
                self._write_log(self.camera_var.get())
            owner_alive = bool(
                self.camera_worker is not None and self.camera_worker.is_alive()
            )
            self.camera_running = owner_alive
            if not owner_alive:
                self.camera_worker = None
                self.runtime.submit(RuntimeCommand.CAMERA_AUTO_OFF)
            return
        self.camera_var.set("Camera running")
        if packet.preview_rgb is not None:
            self._show_preview(packet.preview_rgb)
        if (
            self._camera_stop_requested
            and self.camera_worker is not None
            and not self.camera_worker.is_alive()
        ):
            self._complete_camera_stop()

    def _show_preview(self, preview_rgb: Any) -> None:
        try:
            from PIL import Image, ImageTk

            image = preview_rgb if isinstance(preview_rgb, Image.Image) else Image.fromarray(preview_rgb)
            photo = ImageTk.PhotoImage(image=image)
            self._preview_photo = photo
            self.preview_label.configure(image=photo, text="", width=0, height=0)
        except Exception as exc:
            self.camera_var.set(f"Preview unavailable: {exc}")

    def _apply_projection(self, projection: UiProjection) -> None:
        self._set_enabled(self.connect_button, projection.can_connect)
        self._set_enabled(self.arm_button, projection.can_arm)
        self._set_enabled(self.center_button, projection.can_center)
        self._set_enabled(self.start_cpg_button, projection.can_start_cpg)
        self._set_enabled(self.stop_cpg_button, projection.can_stop_cpg)
        scan_active = self._camera_scan_thread is not None
        camera_owner = self.camera_worker is not None
        selection_unlocked = not scan_active and not camera_owner and not self.closing
        can_start_camera = (
            projection.can_start_camera
            and selection_unlocked
            and self._selected_camera_index() is not None
        )
        self._set_enabled(self.camera_start_button, can_start_camera)
        self._set_enabled(self.camera_stop_button, projection.can_stop_camera)
        self._set_enabled(self.camera_scan_button, selection_unlocked)
        self.camera_combo.configure(state="readonly" if selection_unlocked else "disabled")
        self.emergency_button.configure(state="normal" if projection.can_emergency else "disabled")

    @staticmethod
    def _set_enabled(widget: ttk.Widget, enabled: bool) -> None:
        widget.state(("!disabled",) if enabled else ("disabled",))

    def _start_camera(self) -> None:
        if self.closed or self.closing:
            self.camera_var.set("Camera start rejected during shutdown")
            return
        if self._camera_scan_thread is not None:
            self.camera_var.set("Wait for camera scan to finish")
            return
        if self.camera_running or self.camera_worker is not None:
            self.camera_var.set("Camera cleanup in progress; wait before restart")
            return
        if self.camera_factory is None:
            self.camera_var.set("Camera factory is not configured")
            return
        camera_index = self._selected_camera_index()
        if camera_index is None:
            self.camera_var.set("No usable camera selected; press SCAN CAMERAS")
            return
        try:
            self.camera_worker = self.camera_factory(camera_index)
            self.camera_worker.start()
            self._camera_stop_requested = False
            self.camera_running = True
            self.camera_var.set("Camera starting…")
            self.runtime.submit(RuntimeCommand.CAMERA_AUTO_ON)
            self._write_log("Camera start requested; Face Search starts automatically after Enable Motion + Go Center + live camera frames (AUTO mode)")
        except Exception as exc:
            self.camera_running = False
            self.camera_var.set(f"Camera start failed: {exc}")

    def _begin_camera_scan(self) -> None:
        if self.closed or self.closing or self.camera_worker is not None:
            return
        if self.mock_mode:
            self._set_camera_choices(
                [
                    CameraChoice(
                        self.config.camera_index,
                        f"Mock Camera (index {self.config.camera_index})",
                    )
                ]
            )
            self.camera_var.set("Mock camera ready")
            return
        if self._camera_scan_thread is not None and self._camera_scan_thread.is_alive():
            return

        self.camera_var.set("Scanning camera indices 0-9...")
        self._set_enabled(self.camera_scan_button, False)
        self.camera_combo.configure(state="disabled")
        self._set_enabled(self.camera_start_button, False)

        def scan() -> None:
            try:
                self._camera_scan_queue.put((list(self.camera_discovery()), ""))
            except Exception as exc:
                self._camera_scan_queue.put(([], str(exc)))

        self._camera_scan_thread = threading.Thread(
            target=scan, name="camera-discovery", daemon=True
        )
        self._camera_scan_thread.start()

    def _poll_camera_scan(self) -> None:
        try:
            choices, error = self._camera_scan_queue.get_nowait()
        except queue.Empty:
            return
        self._camera_scan_thread = None
        if self.closed or self.closing:
            return
        if error:
            self._set_camera_choices([])
            self.camera_var.set(f"Camera scan failed: {error}")
            return
        self._set_camera_choices(choices)
        if choices:
            self.camera_var.set(f"Found {len(choices)} usable camera(s)")
        else:
            self.camera_var.set("No usable camera found at indices 0-9")

    def _set_camera_choices(self, choices: list[CameraChoice]) -> None:
        previous = self._selected_camera_index()
        preferred = self.config.camera_index if previous is None else previous
        self._camera_choices = list(choices)
        labels = tuple(choice.label for choice in self._camera_choices)
        self.camera_combo.configure(values=labels)
        selected = choose_camera(self._camera_choices, preferred)
        self.camera_choice_var.set("" if selected is None else selected.label)

    def _selected_camera_index(self) -> int | None:
        selected_label = self.camera_choice_var.get()
        for choice in self._camera_choices:
            if choice.label == selected_label:
                return choice.index
        return None

    def _stop_camera(self) -> None:
        self.runtime.submit(RuntimeCommand.CAMERA_AUTO_OFF)
        if self.camera_worker is not None:
            self._camera_stop_requested = True
            if not self.camera_worker.stop(timeout=0.0):
                self.camera_running = True
                self.camera_var.set("Camera stopping...")
                return
        self._complete_camera_stop()

    def _complete_camera_stop(self) -> None:
        self.camera_worker = None
        self.camera_running = False
        self._camera_stop_requested = False
        self.camera_mailbox.publish(captured_at=time.monotonic(), error="camera stopped")
        self.camera_var.set("Camera stopped")
        self._write_log("Camera stopped")

    def _selected_manual_mode(self) -> str | None:
        source = getattr(self.runtime, "cpg_source", None)
        return getattr(source, "manual_mode", None)

    def _select_personality(self, mode: str) -> None:
        """Switch CPG input, never connect/arm/move any Servo directly."""
        if self.closed or self.closing:
            return
        state = self.runtime.latest_snapshot().state
        if state in (RuntimeState.FAULT, RuntimeState.EMERGENCY, RuntimeState.CLOSED):
            self._write_log(f"Personality {mode} ignored in {state.value}")
            return
        source = getattr(self.runtime, "cpg_source", None)
        if source is None or not hasattr(source, "select_manual_mode"):
            self._write_log("Manual personality not supported by CPG source")
            return
        if self._selected_manual_mode() == mode:
            return
        try:
            source.select_manual_mode(mode)
        except (ValueError, RuntimeError) as exc:
            self._write_log(f"Personality rejected: {exc}")
            return
        self.personality_var.set(f"MANUAL: {mode}")
        self._write_log(f"Numpad personality → {mode} (no automatic motor start)")

    def _select_camera_auto(self) -> None:
        if self.closed or self.closing:
            return
        if self.runtime.latest_snapshot().state in (
            RuntimeState.FAULT, RuntimeState.EMERGENCY, RuntimeState.CLOSED
        ):
            return
        source = getattr(self.runtime, "cpg_source", None)
        if source is None or not hasattr(source, "select_camera_auto"):
            return
        # Pressing 0 always requests AUTO. If the camera is stopped,
        # start its worker; if already running, explicitly restart SEARCHING.
        source.select_camera_auto()
        self.personality_var.set("AUTO (Camera)")
        if not self.camera_running:
            self._start_camera()
        else:
            self.runtime.submit(RuntimeCommand.CAMERA_SEARCH)
        self._write_log("Numpad 0: AUTO Face Search requested (center/torque safety remains active)")

    @staticmethod
    def _is_text_input(widget: Any) -> bool:
        return widget is not None and isinstance(
            widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, ttk.Spinbox)
        )

    def _on_keyboard_focus_change(self, event: Any) -> None:
        # Pause Windows hotkeys while a numeric text field is being edited;
        # RegisterHotKey would otherwise eat the actual digits before Tk sees them.
        if os.name == "nt" and not self.closing:
            self.root.after_idle(self._refresh_global_numpad_focus)

    def _refresh_global_numpad_focus(self) -> None:
        if self.closed or self.closing or os.name != "nt":
            return
        editing = self._is_text_input(self.root.focus_get())
        if editing and self._global_numpad is not None:
            self._global_numpad.stop()
            self._global_numpad = None
            self.keyboard_status_var.set("Global Numpad paused while editing")
        elif not editing and self.global_numpad_var.get() and self._global_numpad is None:
            self._toggle_global_numpad()

    def _toggle_global_numpad(self) -> None:
        """Reserve/release physical numeric keypad 0..4 on Windows only."""
        if self._global_numpad is not None:
            self._global_numpad.stop()
            self._global_numpad = None
        if self.closed or self.closing or not self.global_numpad_var.get():
            self.keyboard_status_var.set("Numpad: GUI focus only")
            return
        if os.name == "nt":
            if self._is_text_input(self.root.focus_get()):
                self.keyboard_status_var.set("Global Numpad paused while editing")
                return
            self._global_numpad = WindowsNumpadHotkeys()
            self._global_numpad.start()
            self.keyboard_status_var.set("Registering keyboard hotkeys...")

    def _activate_numpad_number(self, num: int) -> None:
        """Both Tk key events and native Win32 hotkeys use one routing path."""
        if self.closed or self.closing:
            return
        if num == 0:
            self._select_camera_auto()
        elif num in (1, 2, 3, 4):
            self._select_personality(("", "Happy", "Sad", "Confused", "Neutral")[num])

    def _poll_numpad_keyboard(self) -> None:
        listener = self._global_numpad
        if listener is None:
            return
        while True:
            try:
                failed = listener.statuses.get_nowait()
            except queue.Empty:
                break
            if failed:
                self.keyboard_status_var.set(f"Global Numpad unavailable: {failed}; GUI keys still work")
                self._write_log(f"Windows Numpad keys {failed} could not be registered; check keyboard apps")
            else:
                self.keyboard_status_var.set("Keyboard Numpad 0–4 ACTIVE (global)")
        # Drain pending events; keep only the last command in this GUI tick.
        latest: int | None = None
        while True:
            try:
                latest = listener.events.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            # Even global events should not switch the robot while the operator
            # is typing numeric parameters in the CPG Settings editor.
            focused = self.root.focus_get()
            if self._is_text_input(focused):
                return
            self._activate_numpad_number(latest)

    def _on_numpad_key(self, event: Any) -> None:
        """Handle actual keypad keys, including NumLock-off aliases.

        Ignore key presses made while editing forms so shortcuts cannot hijack
        settings input, as well as any Ctrl/Alt/Meta combinations.
        """
        if self.closed or self.closing or (int(getattr(event, "state", 0)) & 0x000C):
            return
        keysym = getattr(event, "keysym", "")
        if keysym not in NUMPAD_PERSONALITIES:
            return
        focused = getattr(event, "widget", None)
        if self._is_text_input(focused):
            return
        selected = NUMPAD_PERSONALITIES[keysym]
        if selected is None:
            self._activate_numpad_number(0)
        else:
            self._activate_numpad_number(("Happy", "Sad", "Confused", "Neutral").index(selected) + 1)

    def _emergency(self) -> None:
        if self._led_driver is not None:
            self._led_driver.off()
        self.runtime.request_emergency()
        self._write_log("EMERGENCY requested — Torque Off")

    def _write_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def close(self) -> None:
        if self.closed or self.closing:
            return
        self.closing = True
        if self._led_driver is not None:
            self._led_driver.off()
        self.runtime.request_emergency()
        if self._global_numpad is not None:
            self._global_numpad.stop()
            self._global_numpad = None
        self._set_enabled(self.camera_start_button, False)
        self._set_enabled(self.camera_stop_button, False)
        self._set_enabled(self.camera_scan_button, False)
        self.camera_combo.configure(state="disabled")
        self.cpg_editor.set_editable(False)
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        self.state_var.set("SHUTTING DOWN - TORQUE OFF")
        self._write_log("Shutdown requested - waiting for robot Torque Off")
        self._poll_shutdown()

    def _poll_shutdown(self) -> None:
        if not self.runtime.close(timeout=0.0):
            snapshot = self.runtime.latest_snapshot()
            self._update_from_snapshot(snapshot)
            self._poll_after_id = self.root.after(self.POLL_MS, self._poll_shutdown)
            return
        if self.camera_worker is not None:
            self._camera_stop_requested = True
            if not self.camera_worker.stop(timeout=0.0):
                self.camera_var.set("Robot safe; waiting for camera to stop...")
                self._poll_after_id = self.root.after(self.POLL_MS, self._poll_shutdown)
                return
            self._complete_camera_stop()
        self.closed = True
        if self._led_driver is not None:
            self._led_driver.close()
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="USB camera + main CPG + SCS15 control GUI")
    parser.add_argument("--mock", action="store_true", help="run without camera or Servo hardware")
    parser.add_argument("--smoke-test", action="store_true", help="construct and close the GUI")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--port", default=None, help="COM8, /dev/ttyUSB0, or AUTO")
    parser.add_argument("--baud", type=int, default=None)
    parser.add_argument("--camera-index", type=int, default=None)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--cpg-profile", type=Path, default=None,
                        help="editable CPG JSON path (defaults to tools/robot_cpg_gui/cpg_profile.json)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_runtime_config(args.config)
    requested_port = args.port if args.port is not None else config.port
    baud = args.baud if args.baud is not None else config.baud
    camera_index = args.camera_index if args.camera_index is not None else config.camera_index

    mailbox = CameraMailbox()
    from .maincpg_adapter import MainCpgAdapter

    from .cpg_profile import DEFAULT_PROFILE_PATH
    cpg_adapter = MainCpgAdapter(
        start_time=time.monotonic(), mailbox=mailbox,
        profile_path=args.cpg_profile or DEFAULT_PROFILE_PATH,
        loop_hz=config.loop_hz, allow_defaults=args.mock,
    )
    mapper = HardwareMapper(config)
    if args.mock:
        from tools.servo_calibration_gui.bus import MockServoBus
        from .mock_components import MockCameraWorker

        bus = MockServoBus({servo_id: servo.center for servo_id, servo in config.servos.items()})
        camera_factory = lambda _camera_index: MockCameraWorker(mailbox)
        config = replace_dataclass(config, port="MOCK", baud=baud, camera_index=camera_index)
    else:
        from tools.servo_calibration_gui.feetech_bus import FeetechBus
        from .camera import UsbCameraWorker

        selected_port = resolve_serial_port(requested_port)
        config = replace_dataclass(
            config, port=selected_port, baud=baud, camera_index=camera_index
        )
        mapper = HardwareMapper(config)
        bus = FeetechBus(config.port, config.baud)

        def camera_factory(selected_camera_index: int):
            if not args.model_path.is_file():
                raise FileNotFoundError(f"MediaPipe model not found: {args.model_path}")
            return UsbCameraWorker(
                str(args.model_path), mailbox, camera_index=selected_camera_index
            )

    limits = cpg_adapter.profile_snapshot()["joint_limits"]
    face_search = FaceSearchController(config, mailbox,
                                       settings=load_face_search_settings(),
                                       joint_limits=limits)
    runtime = RobotRuntime(config, mapper, bus, cpg_adapter, face_search=face_search)
    runtime.start()
    root = tk.Tk()
    if args.smoke_test:
        root.withdraw()
    app = RobotCpgApp(
        root,
        config,
        mapper,
        runtime,
        mailbox,
        camera_factory=camera_factory,
        mock_mode=args.mock,
    )
    if args.smoke_test:
        root.update_idletasks()
        app.close()
        deadline = time.monotonic() + 3.0
        while not app.closed and time.monotonic() < deadline:
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(0.01)
        if not app.closed:
            raise RuntimeError("smoke-test shutdown did not complete")
        print("Integrated CPG GUI smoke test passed")
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

