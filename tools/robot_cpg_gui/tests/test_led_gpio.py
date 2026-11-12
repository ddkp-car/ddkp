import json
import tempfile
import unittest
from pathlib import Path
from tools.robot_cpg_gui.led_gpio import (
    LedGpioConfig, LedGpioDriver, load_led_config,
    parse_led_config, save_led_config,
)


class LedGpioTests(unittest.TestCase):
    def test_explicitly_disabled_does_not_access_gpio(self):
        config = load_led_config(Path(__file__).resolve().parents[1] / 'led_gpio.json')
        self.assertEqual(config.mode, 'ws2812_spi')
        self.assertEqual(config.data_bcm, 10)
        assert config.enabled  # preserve latest project choice; OFF must still be possible
        disabled = LedGpioConfig(enabled=False, mode='ws2812_spi', data_bcm=10)
        self.assertFalse(LedGpioDriver(disabled).outputs)

    def test_requires_three_pins_for_rgb_when_enabled(self):
        with self.assertRaises(ValueError):
            parse_led_config({'schema_version':1, 'enabled': True, 'mode':'rgb',
                              'red_bcm':18})
        config = parse_led_config({'schema_version':1, 'enabled': True, 'mode':'rgb',
                                   'red_bcm':18,'green_bcm':19,'blue_bcm':13})
        self.assertTrue(config.enabled)
        with self.assertRaises(ValueError):
            parse_led_config({'schema_version':1, 'enabled': True, 'mode':'rgb',
                              'red_bcm':18,'green_bcm':18,'blue_bcm':13})

    def test_roundtrip_and_reserved_pin_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.json'
            config = LedGpioConfig(mode='mono', mono_bcm=18)
            save_led_config(path, config)
            self.assertEqual(load_led_config(path), config)
            with self.assertRaises(ValueError):
                save_led_config(path, LedGpioConfig(enabled=True, mode='mono', mono_bcm=3))
            self.assertEqual(load_led_config(path), config)


if __name__ == '__main__':
    unittest.main()
