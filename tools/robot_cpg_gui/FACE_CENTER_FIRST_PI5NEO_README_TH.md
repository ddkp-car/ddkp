# Face-center-first + Post-exit Personality + Pi5Neo GPIO10

## Behavior

After Connect / Enable Motion / Go Center, press 0 AUTO or Start Camera.

1. SEARCHING: J1 sweeps only within the calibrated safe range. J2/J3/J4 take the existing standby pose; J3 remains 45° nominal.
2. TRACKING/OBSERVING: face coordinates are used as image feedback. J1 pans horizontally. J2, J3, and J4 share vertical correction. No CPG personality pose is sent while a face is visible. Camera emotion and hormone estimates still update in the background.
3. Once the face has *left* for longer than `lost_grace_seconds` (1 second), the most persistent valid camera emotion is latched. The robot starts a 5-second CPG + expressive Stage 2 performance and the LED responds to that emotion. Neutral is used if there was no sufficiently reliable non-neutral expression.
4. STANDBY: smoothly return to standby, wait 20 seconds and search again. Any newly detected face interrupts playback/standby and immediately resumes TRACKING. Camera errors/Servo fault/Emergency retain their existing protections.

**Physical limits:** All 4 joints remain bounded by the saved Servo safety configuration and pose slew limiter. Exact face centering cannot be guaranteed until the camera/Servo mounting geometry and direction signs have been checked on the actual lamp.

## Fine tune gaze direction

Edit `face_search_config.json` while stopped, then restart:

- `track_invert_x`: reverse J1 horizontal correction if the face runs farther from center.
- `track_invert_y`: reverse the overall vertical correction.
- `track_vertical_mix`: weights for J2/J3/J4. Must sum to 1. Default `[0.3, 0.3, 0.4]`.
- `track_vertical_invert`: physical direction reversal for J2/J3/J4. Default `[false, true, false]`; check one axis at a time before using wide tracking.
- `track_gain_deg_s`, `track_gain_y_deg_s`: proportional correction speed.
- `track_deadband_x`, `track_deadband_y`: zone around image center that avoids jitter.
- `playback_seconds`: default `5.0` (kept aligned with Emotion Stage 2).

Start with the arm supported, no obstructions, and an accessible Emergency switch. First test with small face movements.

## Verified LED backend

We switched **GPIO10 / physical Pin 19** to `pi5neo` / `/dev/spidev0.0`, the API that the operator successfully used:

```python
from pi5neo import Pi5Neo
neo = Pi5Neo('/dev/spidev0.0', num_leds=LED_COUNT, spi_speed_khz=800)
neo.fill_strip(30, 0, 0)
neo.update_strip()
neo.clear_strip()
neo.update_strip()
neo.close()
```

The GUI now uses the same `fill_strip`, `set_led_color`, `update_strip`, `clear_strip`, and `close` methods, with spatial rainbow when multiple LEDs are configured.

**IMPORTANT: Configure `pixel_count` in `led_gpio.json` to the ACTUAL LED count before expecting all bulbs to illuminate.** WS2812 provides no LED-count autodetection. The profile shipped here keeps the previous value of 1 because the actual count has not been provided. Using 1 only illuminates the first physical LED.

Install dependencies on Pi inside the project's virtual environment:

```bash
cd ~/lamp_ddkp/Emotion_Robotic_Lamp_Windows
source ~/lamp_ddkp/.venv/bin/activate
python3 -m pip install -r tools/robot_cpg_gui/requirements-pi.txt
```

Test LED only (without Servo):

```bash
python3 -m tools.robot_cpg_gui.led_test_pi --pixels 12 --brightness 0.10
```

Replace `12` with the actual number of LEDs. When this succeeds, set `led_gpio.json` `pixel_count` to the same number and restart the GUI. Do NOT power an unknown LED strip from the Pi's 5V header without checking its current draw and supplying an appropriate 5V source and 3.3V-to-5V logic-level conversion.

## Preserved without change

`robot_config.json` (including speed 150, Center tolerance implementation), `cpg_profile.json` (CPG Phi/Gain/Pose/hormones), `led_gpio.json` (SPI GPIO10 settings and existing LED count), emergency behavior, Numpad 0 AUTO.
