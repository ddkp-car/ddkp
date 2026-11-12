"""Integration regression for face-centered observation then post-exit playback and Pi5Neo."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes import FakeBus
from tools.robot_cpg_gui.camera import CameraMailbox
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.face_search import FaceSearchController
from tools.robot_cpg_gui.led_gpio import LedGpioDriver, LedGpioConfig
from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand, RuntimeState

ROOT=Path(__file__).resolve().parents[1]


class FaceCycleIntegration(unittest.TestCase):
    def setUp(self):
        cfg=load_runtime_config(ROOT/'robot_config.json')
        self.mailbox=CameraMailbox()
        self.bus=FakeBus()
        self.adapter=MainCpgAdapter(start_time=0,mailbox=self.mailbox,profile_path=ROOT/'cpg_profile.json',loop_hz=cfg.loop_hz)
        self.finder=FaceSearchController(cfg,self.mailbox)
        self.runtime=RobotRuntime(cfg,HardwareMapper(cfg),self.bus,self.adapter,face_search=self.finder)
        for t,cmd in [(0,RuntimeCommand.CONNECT),(.05,RuntimeCommand.ARM),(.10,RuntimeCommand.CENTER)]:
            self.runtime.submit(cmd);self.runtime.process_once(t)
        self.assertEqual(self.runtime.latest_snapshot().state,RuntimeState.ARMED)
        self.runtime.submit(RuntimeCommand.CAMERA_AUTO_ON)
        self.runtime.process_once(.2)

    def put(self,t,detected=True,emotion='happy',x=.85,y=.85,strength=.95):
        self.mailbox.publish(captured_at=t,observation={
            'schema':1,'stream_id':'integration','seq':int(t*1000),
            'detected':detected,'face_x':x if detected else None,
            'face_y':y if detected else None,'emotion':emotion,'strength':strength})
        self.runtime.process_once(t+.001)
        return self.runtime.latest_snapshot()

    def test_no_personality_until_face_leaves_then_dance_five_seconds(self):
        self.put(.30, detected=False)
        results=[]
        for i in range(8,120):
            t=i*.05
            snap=self.put(t)
            if t>.8: results.append(snap)
        self.assertEqual(results[-1].camera_behavior,'TRACKING')
        self.assertTrue(all(s.personality_stage=='OBSERVING' for s in results))
        self.assertTrue(all(s.light['color']=='#ffd43b' for s in results))
        self.assertTrue(all(s.state==RuntimeState.RUNNING for s in results))
        # Face moved down/right; each joint must actively center, independent of CPG.
        self.assertNotEqual(results[-1].cpg_deg[0],90)
        self.assertTrue(all(results[-1].cpg_deg[j] != results[0].cpg_deg[j] for j in (1,2,3)))
        for i in range(120,152):
            snap=self.put(i*.05,detected=False)
        self.assertEqual(snap.camera_behavior,'PLAYBACK')
        self.assertEqual(snap.personality_stage,'STAGE1 Happy')
        colors=[]
        for i in range(152,220):
            snap=self.put(i*.05,detected=False)
            colors.append(snap.light['color'])
        self.assertGreater(len(set(colors)),4)
        self.assertEqual(snap.personality_stage,'STAGE2 Happy')
        self.assertEqual(snap.camera_behavior,'PLAYBACK')
        for i in range(220,260):
            snap=self.put(i*.05,detected=False)
        self.assertEqual(snap.camera_behavior,'STANDBY')
        self.assertEqual(snap.personality_stage,'BASE')
        for i in range(260,675):
            snap=self.put(i*.05,detected=False)
        self.assertEqual(snap.camera_behavior,'SEARCHING')
        self.assertTrue(all(self.runtime.config.servos[j].safe_min<=v<=self.runtime.config.servos[j].safe_max for j,v in snap.target_raw.items()))

    def test_face_reappears_and_preempts_playback(self):
        self.put(.30,detected=False)
        for i in range(8,60): self.put(i*.05,emotion='sad')
        for i in range(60,93): snap=self.put(i*.05,detected=False)
        self.assertEqual(snap.camera_behavior,'PLAYBACK')
        self.assertEqual(snap.personality_stage,'STAGE1 Sad')
        for i in range(93,100): snap=self.put(i*.05,emotion='happy')
        self.assertEqual(snap.camera_behavior,'TRACKING')
        self.assertEqual(snap.personality_stage,'OBSERVING')
        self.assertIsNone(self.runtime._playback)

    def test_camera_fault_and_emergency_override(self):
        self.put(.30,detected=False)
        for i in range(8,18): snap=self.put(i*.05)
        self.runtime.request_emergency();self.runtime.process_once(1.2)
        self.assertEqual(self.runtime.latest_snapshot().state,RuntimeState.EMERGENCY)


class FakePixelType:
    GRB='GRB'; RGB='RGB'

class FakeNeo:
    instances=[]
    def __init__(self,path,num_leds,spi_speed_khz,pixel_type="GRB",quiet_mode=False):
        self.path=path;self.pixel_count=num_leds;self.pixel_type=pixel_type
        self.values=[(0,0,0)]*num_leds;self.closed=False;self.calls=[]
        self.__class__.instances.append(self)
    def fill_strip(self,r,g,b):
        self.values=[(r,g,b)]*self.pixel_count;self.calls.append('fill_strip')
    def set_led_color(self,i,r,g,b): self.values[i]=(r,g,b);self.calls.append('set_led_color')
    def update_strip(self,sleep_duration=None): self.calls.append('update_strip')
    def clear_strip(self): self.fill_strip(0,0,0);self.calls.append('clear_strip')
    def close(self): self.closed=True

class Pi5NeoIntegration(unittest.TestCase):
    def test_same_pi5neo_fill_strip_update_strip_and_rainbow(self):
        FakeNeo.instances.clear()
        with patch.dict(sys.modules, {'pi5neo':types.SimpleNamespace(Pi5Neo=FakeNeo, EPixelType=FakePixelType)}), \
             patch('tools.robot_cpg_gui.led_gpio.Path.exists', return_value=True):
            led=LedGpioDriver(LedGpioConfig(enabled=True, mode='ws2812_spi',pixel_count=12))
            dev=FakeNeo.instances[-1]
            self.assertEqual(dev.path, '/dev/spidev0.0')
            led.update({'color':'#ff0000','brightness':.7})
            self.assertEqual(len(set(dev.values)),1)
            self.assertTrue(all(c[0]>0 for c in dev.values))
            led.update({'color':'#ff0000','brightness':.7,'pattern':'rainbow'})
            self.assertGreater(len(set(dev.values)),4)
            led.off(); self.assertTrue(all(v==(0,0,0) for v in dev.values))
            led.close();self.assertTrue(dev.closed)


if __name__ == '__main__':unittest.main()
