# Emotion Robotic Lamp: AUTO Face Search + 5-second Stage 2

Based on the user's merged GPIO10 revision (`robot_cpg_gui(4)` + full project merge). This update does NOT change servo calibration, CPG formulas, speed, GPIO/LED pins, hormone gain settings, or hardware safe ranges.

## Operating sequence

1. CONNECT → ENABLE MOTION → GO CENTER. Physical motion never bypasses the motor safety gate.
2. Press **0 AUTO — SEARCH FACE** in the GUI or keypad **0** (with GUI focus on Linux) to enable automatic camera operation. If the camera is stopped, this starts the camera worker. If it is already running, it explicitly restarts the face search. The camera must produce valid frames before any autonomous motion starts.
3. With no face, J1 scans inside its existing calibrated safe range; J2/J3/J4 adopt the configured standby pose. **J3 standby target is nominal 45°**, corresponding to offset −45° from the 90° controller center. It is rate-limited and clamped again by hardware mapping. Normal CPG joint limits remain unchanged.
4. If a face is found, visual gaze controls J1 and J4 and the camera automatically chooses the CPG emotion. Manual selection of personalities is NOT necessary; Numpad 1–4 remain optional shortcuts.
5. When the hormone peak dwell requirements are met (Happy 3 s; other modes 5 s), Stage 2 animates for **5 seconds**, then transitions into **STANDBY**, with an anti-repeat gate for the same continuous camera emotion.
6. While the same face/emotion continues, STANDBY stays in `WAIT_NEW_EMOTION`. A different recognized emotion stable for 3 fresh observations can restart tracking and expression. If the face disappears beyond the lost-face grace period, the anti-repeat gate clears. After **20 seconds in STANDBY** without reacquisition, the system returns to SEARCHING, even if a servo did not settle exactly at its standby angle.
7. STOP / EMERGENCY behavior and motion safety checks are unchanged. Missing camera stream causes the original safe stop, not uncontrolled blind scanning.

## Raspberry Pi install from the small ZIP

Copy the ZIP to `/home/ddkp/lamp_ddkp/` using FileZilla or `scp`, then:

```bash
cd ~/lamp_ddkp/Emotion_Robotic_Lamp_Windows
cp -a tools/robot_cpg_gui tools/robot_cpg_gui.backup_before_auto_stage
unzip -o ~/lamp_ddkp/robot_cpg_gui_GPIO10_AUTO_FACE_STAGE2_5S_2026-11-12.zip -d tools/
```

To launch with real motors on an existing X11 desktop session:

```bash
source ~/lamp_ddkp/.venv/bin/activate
python3 -m tools.robot_cpg_gui.app --port /dev/ttyACM0 --camera-index 0
```

If using X11 forwarding from Windows, the window must have focus for Linux Numpad shortcuts; the on-screen **0 AUTO — SEARCH FACE** button also works.

## Unchanged baseline

- Center tolerance: ±20 raw counts
- Servo speed: 150
- J2 direction: -1
- GPIO10 (physical pin 19) / `ws2812_spi`
- CPG profile, hormone constants, stage-2 dances and LED effects, calibration, emergency

**Hardware warning:** Physical J3 travel to nominal 45° must be verified for linkage clearance and cables before unattended use. The code does not enlarge stored servo safe_min/safe_max values.
