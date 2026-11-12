"""Pi5Neo hardware-SPI regression tests using in-memory fake hardware."""
from __future__ import annotations
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from tools.robot_cpg_gui.led_gpio import LedGpioConfig, LedGpioDriver, parse_led_config, save_led_config, load_led_config

class PixelType:
    GRB='GRB'; RGB='RGB'

class FakeNeo:
    instances=[]
    def __init__(self,path,num_leds,spi_speed_khz,pixel_type="GRB",quiet_mode=False):
        self.path=path; self.count=num_leds; self.speed=spi_speed_khz
        self.order=pixel_type; self.quiet=quiet_mode
        self.values=[(0,0,0)]*num_leds
        self.shows=0; self.closed=False
        self.__class__.instances.append(self)
    def fill_strip(self,r,g,b): self.values=[(r,g,b)]*self.count
    def set_led_color(self,i,r,g,b): self.values[i]=(r,g,b)
    def update_strip(self,sleep_duration=None): self.shows+=1
    def clear_strip(self): self.fill_strip(0,0,0)
    def close(self): self.closed=True

class Ws2812SpiTests(unittest.TestCase):
    def setUp(self):
        FakeNeo.instances.clear()
        self.patcher=patch.dict(sys.modules,{'pi5neo':types.SimpleNamespace(Pi5Neo=FakeNeo, EPixelType=PixelType)})
        self.patcher.start();self.addCleanup(self.patcher.stop)
        self.pathpatch=patch('tools.robot_cpg_gui.led_gpio.Path.exists',return_value=True)
        self.pathpatch.start();self.addCleanup(self.pathpatch.stop)

    def test_deployed_config_spi_gpio10(self):
        cfg=load_led_config(Path(__file__).parents[1]/'led_gpio.json')
        self.assertEqual(cfg.mode,'ws2812_spi'); self.assertEqual(cfg.data_bcm,10)
        driver=LedGpioDriver(cfg)
        pix=FakeNeo.instances[-1];self.assertEqual(pix.count,cfg.pixel_count)
        self.assertEqual(pix.path,'/dev/spidev0.0');self.assertEqual(pix.speed,800)
        driver.close();self.assertTrue(pix.closed)

    def test_validation_and_round_trip(self):
        for bad in [
            {'mode':'ws2812_spi','data_bcm':18},
            {'mode':'ws2812_spi','pixel_count':0},
            {'mode':'ws2812_spi','max_brightness':1.0},
            {'mode':'ws2812_spi','pixel_order':'RGBW'},
        ]:
            with self.assertRaises(ValueError):parse_led_config({'schema_version':1,'enabled':True,**bad})
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'led.json'
            cfg=LedGpioConfig(enabled=True,pixel_count=12,max_brightness=.2)
            save_led_config(path,cfg)
            self.assertEqual(load_led_config(path),cfg)

    def test_pi5neo_all_leds_solid_rainbow_and_close(self):
        driver=LedGpioDriver(LedGpioConfig(enabled=True,mode='ws2812_spi',data_bcm=10,pixel_count=8,max_brightness=.2))
        pix=FakeNeo.instances[-1]
        driver.update({'color':'#ff0000','brightness':1.0})
        self.assertEqual(pix.values,[(51,0,0)]*8)
        previous=pix.shows
        driver.update({'color':'#ff0000','brightness':1.0})
        self.assertEqual(pix.shows,previous)
        driver.update({'color':'#00ff00','brightness':.8,'pattern':'rainbow'})
        self.assertGreater(len(set(pix.values)),4)
        driver.update({'color':'bad','brightness':1.0})
        self.assertEqual(pix.values,[(0,0,0)]*8)
        driver.close();self.assertTrue(pix.closed)

if __name__=='__main__':unittest.main()
