"""PIO WS2812 tests: mocked Pi 5 hardware, no actual GPIO access."""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.robot_cpg_gui.led_gpio import (
    LedGpioConfig, LedGpioDriver, parse_led_config, save_led_config, load_led_config
)


class FakePixelBuf:
    instances = []

    def __init__(self, size, **kwargs):
        self.size = size
        self.kwargs = kwargs
        self.values = [(0, 0, 0)] * size
        self.shows = 0
        self.__class__.instances.append(self)

    def __setitem__(self, index, color):
        self.values[index] = tuple(color)

    def fill(self, value):
        self.values = [tuple(value)] * self.size

    def show(self):
        self.shows += 1
        self._transmit(self.values)


class PioTests(unittest.TestCase):
    def setUp(self):
        FakePixelBuf.instances.clear()
        self.transmissions = []
        self.patcher = patch.dict(sys.modules, {
            'board': types.SimpleNamespace(D12='GPIO12'),
            'adafruit_pixelbuf': types.SimpleNamespace(PixelBuf=FakePixelBuf),
            'adafruit_raspberry_pi5_neopixel_write': types.SimpleNamespace(
                neopixel_write=lambda pin, buf: self.transmissions.append((pin, tuple(buf)))
            ),
        })
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.exists_patcher = patch('tools.robot_cpg_gui.led_gpio.Path.exists', return_value=True)
        self.exists_patcher.start()
        self.addCleanup(self.exists_patcher.stop)

    def test_project_default_is_spi_and_pio_remains_optional(self):
        cfg = load_led_config(Path(__file__).resolve().parents[1] / 'led_gpio.json')
        self.assertEqual(cfg.mode, 'ws2812_spi')
        self.assertEqual(cfg.data_bcm, 10)
        self.assertTrue(cfg.enabled)
        self.assertEqual(FakePixelBuf.instances, [])

    def test_pio_accepts_only_bcm12_and_keeps_spi_pin_separate(self):
        self.assertEqual(parse_led_config({'schema_version': 1, 'mode': 'ws2812_pio', 'data_bcm':12}).data_bcm, 12)
        with self.assertRaisesRegex(ValueError, 'GPIO12'):
            parse_led_config({'schema_version': 1, 'mode': 'ws2812_pio', 'data_bcm':10})
        with self.assertRaisesRegex(ValueError, 'GPIO10'):
            parse_led_config({'schema_version': 1, 'mode': 'ws2812_spi', 'data_bcm':12})

    def test_gpio12_brightness_color_rainbow_off_close(self):
        d = LedGpioDriver(LedGpioConfig(enabled=True, mode='ws2812_pio', data_bcm=12,
                                        pixel_count=8, pixel_order='GRB', max_brightness=0.2))
        pixel = FakePixelBuf.instances[-1]
        self.assertEqual(pixel._pin, 'GPIO12')
        self.assertEqual(pixel.kwargs['byteorder'], 'GRB')
        d.update({'color':'#ff0000', 'brightness':1.0})
        self.assertEqual(pixel.values[0], (51,0,0))
        count = pixel.shows
        d.update({'color':'#ff0000', 'brightness':1.0})
        self.assertEqual(pixel.shows, count)
        d.update({'color':'#00ff00', 'brightness':0.8, 'pattern':'rainbow'})
        self.assertGreaterEqual(len(set(pixel.values)), 5)
        self.assertTrue(all(max(v) <= 51 for v in pixel.values))
        d.update({'color':'#ff00ff', 'brightness':1.0}, enabled=False)
        self.assertEqual(pixel.values, [(0,0,0)]*8)
        d.close()
        self.assertEqual(pixel.values, [(0,0,0)]*8)
        self.assertTrue(all(pin=='GPIO12' for pin, _ in self.transmissions))

    def test_missing_pio_fails_explicitly(self):
        with patch('tools.robot_cpg_gui.led_gpio.Path.exists', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '/dev/pio0'):
                LedGpioDriver(LedGpioConfig(enabled=True, mode='ws2812_pio'))

    def test_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'led.json'
            cfg=LedGpioConfig(enabled=True, mode='ws2812_pio', data_bcm=12,
                              pixel_count=8, max_brightness=0.1)
            save_led_config(p, cfg)
            self.assertEqual(cfg, load_led_config(p))


if __name__ == '__main__':
    unittest.main()
