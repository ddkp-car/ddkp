"""Optional Raspberry Pi LED: PWM mono/RGB, Pi5Neo SPI WS2812 or Pi 5 PIO WS2812.

GPIO pins are BCM numbers, NOT physical header numbers. The selected WS2812
output uses Raspberry Pi hardware SPI0 MOSI: BCM10 / physical header pin 19.
This needs /dev/spidev0.0, a 5V WS2812-compatible LED, and a 3.3V->5V
level shifter. PIO/BCM12 is supported only as an alternative mode.
"""
from __future__ import annotations

import json
import colorsys
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LED_GPIO_PATH = Path(__file__).resolve().parent / "led_gpio.json"
GPIO_ALLOWED = frozenset({4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27})


@dataclass(frozen=True)
class LedGpioConfig:
    enabled: bool = False
    mode: str = "ws2812_spi"
    mono_bcm: int | None = None
    red_bcm: int | None = None
    green_bcm: int | None = None
    blue_bcm: int | None = None
    active_high: bool = True
    data_bcm: int = 10
    pixel_count: int = 1
    pixel_order: str = "GRB"
    max_brightness: float = 0.25


def load_led_config(path: str | Path = DEFAULT_LED_GPIO_PATH) -> LedGpioConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_led_config(payload)


def parse_led_config(payload: dict[str, Any]) -> LedGpioConfig:
    if payload.get("schema_version") != 1:
        raise ValueError("LED GPIO schema version must be 1")
    permitted = set(LedGpioConfig.__dataclass_fields__) | {"schema_version"}
    if set(payload) - permitted:
        raise ValueError("unknown fields in LED GPIO config")
    for field in ("enabled", "active_high"):
        if field in payload and not isinstance(payload[field], bool):
            raise ValueError(f"{field} must be a boolean")
    config = LedGpioConfig(**{k: v for k, v in payload.items() if k != "schema_version"})
    if config.mode not in ("mono", "rgb", "ws2812_spi", "ws2812_pio"):
        raise ValueError("LED mode must be mono, rgb, ws2812_spi or ws2812_pio")
    if type(config.pixel_count) is not int or not 1 <= config.pixel_count <= 300:
        raise ValueError("pixel_count must be an integer from 1 to 300")
    if config.pixel_order not in ("GRB", "RGB"):
        raise ValueError("pixel_order must be GRB or RGB (3-channel LEDs)")
    if (type(config.max_brightness) not in (int, float)
            or not math.isfinite(config.max_brightness)
            or not 0 < config.max_brightness <= 0.5):
        raise ValueError("max_brightness must be > 0 and <= 0.5 for initial tests")
    # Do not silently accept a GPIO number that the selected backend does not use.
    # Each backend is fixed to its physical data pin: SPI0 MOSI BCM10 or PIO BCM12.
    if type(config.data_bcm) is not int:
        raise ValueError("WS2812 data_bcm must be an integer")
    if config.mode == "ws2812_spi" and config.data_bcm != 10:
        raise ValueError("WS2812 SPI requires GPIO10 (physical pin 19)")
    if config.mode == "ws2812_pio" and config.data_bcm != 12:
        raise ValueError("WS2812 PIO is configured for GPIO12 (physical pin 32)")
    pins = ((config.mono_bcm,) if config.mode == "mono" else
            (config.red_bcm, config.green_bcm, config.blue_bcm) if config.mode == "rgb" else ())
    values = [v for v in pins if v is not None]
    if any(type(v) is not int or v not in GPIO_ALLOWED for v in values):
        raise ValueError("invalid GPIO BCM pin")
    if len(set(values)) != len(values):
        raise ValueError("GPIO pins must be unique")
    if config.enabled and len(values) != len(pins):
        raise ValueError("enable LED only after supplying all GPIO pins")
    return config


def save_led_config(path: str | Path, config: LedGpioConfig) -> None:
    path = Path(path)
    payload = {"schema_version": 1, **vars(config)}
    parse_led_config(payload)
    pending = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".led-", delete=False) as output:
            pending = output.name
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, path)
        pending = None
    finally:
        if pending:
            os.unlink(pending)


class LedGpioDriver:
    """Write light from the CPG snapshot; never control servo or CPG timing.

    Addressable LEDs require verified 5V WS2812-compatible pixels and
    3.3V -> 5V logic level shifting. PIO does not use SPI or software PWM.
    """

    def __init__(self, config: LedGpioConfig):
        self.config = config
        self.outputs = []
        self._pixel = None
        self._spi = None
        self._last = None
        if not config.enabled:
            return
        if os.name != "posix":
            raise RuntimeError("GPIO output is supported on Raspberry Pi Linux only")
        try:
            if config.mode == "ws2812_pio":
                if not Path("/dev/pio0").exists():
                    raise RuntimeError("Pi 5 RP1 PIO not available (/dev/pio0 missing). Update Pi OS/kernel firmware per PIO README")
                import board
                import adafruit_pixelbuf
                from adafruit_raspberry_pi5_neopixel_write import neopixel_write

                class Pi5Pixelbuf(adafruit_pixelbuf.PixelBuf):
                    def __init__(self, pin, size, **kwargs):
                        self._pin = pin
                        super().__init__(size=size, **kwargs)

                    def _transmit(self, buf):
                        neopixel_write(self._pin, buf)

                self._pixel = Pi5Pixelbuf(
                    board.D12, config.pixel_count,
                    auto_write=False, byteorder=config.pixel_order,
                )
                self._pixel.fill((0, 0, 0))
                self._pixel.show()
            elif config.mode == "ws2812_spi":
                # Exactly the backend verified by the user on Pi 5:
                # Pi5Neo('/dev/spidev0.0', num_leds=N, spi_speed_khz=800).
                from pi5neo import Pi5Neo
                if not Path('/dev/spidev0.0').exists():
                    raise RuntimeError('SPI device /dev/spidev0.0 not found')
                if config.pixel_order == 'GRB':
                    # Works with the same API as the successful user test,
                    # including older Pi5Neo versions that lack EPixelType.
                    self._pixel = Pi5Neo('/dev/spidev0.0', config.pixel_count, 800)
                else:
                    from pi5neo import EPixelType
                    self._pixel = Pi5Neo('/dev/spidev0.0', config.pixel_count, 800,
                                         pixel_type=EPixelType.RGB)
                self._pixel.clear_strip()
                self._update_pi5neo()
            else:
                from gpiozero import PWMLED
                pins = ([config.mono_bcm] if config.mode == "mono" else
                        [config.red_bcm, config.green_bcm, config.blue_bcm])
                for pin in pins:
                    self.outputs.append(PWMLED(pin, active_high=config.active_high, initial_value=0))
        except Exception:
            self.close()
            raise

    def _update_pi5neo(self) -> None:
        # Pi5Neo 1.1+ can skip the 100ms default latch sleep. This is
        # important because GUI and Servo commands should not be stalled.
        try:
            self._pixel.update_strip(sleep_duration=None)
        except TypeError:
            self._pixel.update_strip()  # older Pi5Neo versions

    def update(self, light: dict[str, Any], *, enabled: bool = True) -> None:
        if not self.outputs and self._pixel is None:
            return
        try:
            brightness = max(0.0, min(1.0, float(light.get("brightness", 0.0)))) if enabled else 0.0
            color = str(light.get("color", "#000000"))
            if len(color) != 7 or color[0] != "#":
                raise ValueError("light color must be #RRGGBB")
            rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
            pattern = light.get("pattern") if enabled else None
            if pattern not in (None, "rainbow"):
                raise ValueError("unrecognized LED pattern")
            if not math.isfinite(brightness):
                raise ValueError("LED brightness must be finite")
        except (TypeError, ValueError):
            brightness, rgb, pattern = 0.0, (0, 0, 0), None

        if self.config.mode in ("ws2812_spi", "ws2812_pio"):
            scale = brightness * self.config.max_brightness
            if pattern == "rainbow" and self.config.pixel_count > 1:
                base_hue = colorsys.rgb_to_hsv(*(v / 255.0 for v in rgb))[0]
                samples = [tuple(round(255 * scale * component) for component in
                                  colorsys.hsv_to_rgb((base_hue + i / self.config.pixel_count) % 1.0, 1.0, 1.0))
                           for i in range(self.config.pixel_count)]
            else:
                shade = tuple(round(v * scale) for v in rgb)
                samples = [shade] * self.config.pixel_count
            values = tuple(samples)
            if values != self._last:
                try:
                    if self.config.mode == "ws2812_spi":
                        if len(set(samples)) == 1:
                            self._pixel.fill_strip(*samples[0])
                        else:
                            for i, pixel_rgb in enumerate(samples):
                                self._pixel.set_led_color(i, *pixel_rgb)
                        self._update_pi5neo()
                    else:
                        for i, pixel_rgb in enumerate(samples):
                            self._pixel[i] = pixel_rgb
                        self._pixel.show()
                except Exception:
                    self.off()
                    raise
                self._last = values
        else:
            rgb_normalized = tuple(v / 255.0 for v in rgb)
            values = ((brightness,) if self.config.mode == "mono" else
                      tuple(brightness * channel for channel in rgb_normalized))
            if values != self._last:
                for pin, value in zip(self.outputs, values):
                    pin.value = float(value)
                self._last = values

    def off(self) -> None:
        if self._pixel is not None:
            if self.config.mode == "ws2812_spi":
                self._pixel.clear_strip()
                self._update_pi5neo()
            else:
                self._pixel.fill((0, 0, 0))
                self._pixel.show()
            self._last = tuple([(0, 0, 0)] * self.config.pixel_count)
        for output in self.outputs:
            output.off()
        if self.outputs:
            self._last = (0.0,) * len(self.outputs)

    def close(self) -> None:
        for output in self.outputs:
            try:
                output.off()
                output.close()
            except Exception:
                pass
        self.outputs.clear()
        if self._pixel is not None:
            try:
                if self.config.mode == "ws2812_spi":
                    self._pixel.clear_strip()
                    self._update_pi5neo()
                    self._pixel.close()
                else:
                    self._pixel.fill((0, 0, 0))
                    self._pixel.show()
            except Exception:
                try:
                    if self.config.mode == 'ws2812_spi':
                        self._pixel.close()
                except Exception:
                    pass
            self._pixel = None
        if self._spi is not None:
            try:
                if hasattr(self._spi, "deinit"):
                    self._spi.deinit()
            except Exception:
                pass
            self._spi = None
        self._last = None
