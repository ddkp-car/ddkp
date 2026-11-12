# Face Observation Duration + Solid Yellow LEDs (Pi 5)

## New sequence

1. `SEARCHING` — original sweeping behavior and orange LED.
2. `TRACKING/OBSERVING` — J1 pans and J2/J3/J4 track face position; accumulate actual elapsed time in Happy, Sad, Confused and Neutral **once per fresh camera observation**. All 20 Pi5Neo LEDs stay steady yellow (`#ffd43b`); no blinking or rainbow during face observation.
3. Face absent beyond the existing 1-second grace period — select the emotion with the **longest observed time**, not the one with the most camera frames or highest confidence sum. Play that emotion's CPG + Stage 2 motion and lighting for its observed duration (e.g. Happy 8 seconds). No-face gaps and low-confidence classifications are excluded from the timer. If a tie is extremely close, the existing confidence sum is used only as a tie-breaker.
4. `STANDBY` — same blue low-brightness LED and standby pose as before; scan again after the existing 20 seconds. A newly detected face during playback still interrupts it and restarts tracking.

The GUI now shows `Observed: Happy ...s | Sad ...s | Confused ...s | Neutral ...s` as the face is classified. The stage timer has a **configurable 0.5-second minimum and 30-second maximum** for safe bounded performances. Adjust `playback_min_seconds` and `playback_max_seconds` in `face_search_config.json` if needed; maximum validation bound is 120 seconds. This does not alter servo hardware limits.

## Face tracking tuning only

`face_search_config.json` increases horizontal proportional tracking from 28 to 32 degrees/s per unit normalized error; horizontal tracking speed cap from 12 to 14 degrees/s. Vertical proportional tracking 20 -> 24, vertical speed cap 10 -> 12 degrees/s. The actual per-joint pose slew limit remains 20 degrees/s and calibrated raw bounds remain unchanged.

## Hardware and LED

The GPIO10 (physical pin 19), `/dev/spidev0.0`, Pi5Neo, 20-LED `fill_strip` output, and your Servo Speed 150, J2 direction, J3 standby angle, center tolerance and CPG profile are unchanged. Solid yellow brightness uses the configured `max_brightness` limit in `led_gpio.json`.

Test the Servo direction and working range with an emergency cutoff ready. These changes have only been exercised in simulated GUI/motor/camera tests, not validated on your physical robot.
