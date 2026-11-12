"""Manual Pi-5 SPI0 (GPIO10) LED-only test. Never moves a Servo or starts the camera.

Usage AFTER verifying 5V WS2812/NeoPixel wiring and level shifter:
    python -m tools.robot_cpg_gui.led_test_pi --pixels 8

Requires /dev/spidev0.0; uses GPIO10 / physical header pin 19 (SPI0 MOSI).
"""
from __future__ import annotations

import argparse
import sys
import time
from .led_gpio import LedGpioConfig, LedGpioDriver


def main() -> int:
    parser = argparse.ArgumentParser(description='Test VERIFIED 5V addressable WS2812 LED on Raspberry Pi 5')
    parser.add_argument('--pixels', type=int, required=True, help='Exact pixel count (1-300)')
    parser.add_argument('--brightness', type=float, default=0.10, help='Maximum output brightness (0-0.5)')
    args = parser.parse_args()
    if not 1 <= args.pixels <= 300 or not 0 < args.brightness <= 0.5:
        parser.error('use 1-300 pixels, max brightness 0.01-0.5')
    print('Only proceed if the LED is verified 5V WS2812/NeoPixel and has a logic level shifter.')
    print('Data uses GPIO10 physical pin 19 (SPI0 MOSI). Supply & GND must be appropriate.')
    driver = None
    try:
        driver = LedGpioDriver(LedGpioConfig(enabled=True, mode='ws2812_spi', data_bcm=10,
                                             pixel_count=args.pixels,
                                             max_brightness=args.brightness))
        for color in ('#ff0000', '#00ff00', '#0000ff', '#ffffff'):
            print('Showing', color)
            driver.update({'color': color, 'brightness': 0.6})
            time.sleep(0.65)
        print('Rainbow (5 seconds)')
        import colorsys
        for k in range(50):
            rgb = colorsys.hsv_to_rgb(k / 50, 1, 1)
            color = '#' + ''.join(f'{round(c*255):02x}' for c in rgb)
            driver.update({'color': color, 'brightness': 0.65, 'pattern': 'rainbow'})
            time.sleep(0.1)
        return 0
    except (ImportError, RuntimeError, OSError) as exc:
        print('LED SPI test failed:', exc, file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            driver.close()


if __name__ == '__main__':
    raise SystemExit(main())
