"""Real-time per-emotion observation, dominant-duration playback and steady LED."""
import unittest
from pathlib import Path

from tools.robot_cpg_gui.camera import CameraMailbox
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.face_search import FaceSearchController, load_face_search_settings
from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand, RuntimeState
from fakes import FakeBus

DIR = Path(__file__).resolve().parents[1]


class ObservationDurationTests(unittest.TestCase):
    def setUp(self):
        self.config = load_runtime_config(DIR / "robot_config.json")
        self.mailbox = CameraMailbox()
        self.controller = FaceSearchController(self.config, self.mailbox,
                                               load_face_search_settings(DIR / 'face_search_config.json'))
        self.controller.start(0)

    def put(self, t, emotion="happy", detected=True, strength=.95, x=.5, y=.5):
        self.mailbox.publish(captured_at=t, observation={
            'detected': detected, 'emotion': emotion, 'strength': strength,
            'face_x': x if detected else None,
            'face_y': y if detected else None,
        })
        return self.controller.step(t, (90.,) * 4)

    def test_happy_eight_seconds_sad_three_confused_one_selects_happy_for_eight(self):
        # Exactly twelve seconds of observations with ~0.05 second frame intervals.
        for i in range(0, 240):
            t = .1 + i * .05
            emotion = ('happy' if i < 160 else 'sad' if i < 220 else 'confused')
            output = self.put(t, emotion)
        self.assertEqual(output.behavior, 'TRACKING')
        observed = self.controller._emotion_seconds
        self.assertAlmostEqual(observed['Happy'], 8.0, delta=.2)
        self.assertAlmostEqual(observed['Sad'], 3.0, delta=.2)
        self.assertAlmostEqual(observed['Confused'], 1.0, delta=.2)
        for j in range(1, 26):
            output = self.put(12.1 + j * .05, detected=False)
        self.assertEqual(output.behavior, 'PLAYBACK')
        self.assertEqual(output.lost_emotion, None)  # event fires once, at transition
        self.assertEqual(self.controller.playback_mode, 'Happy')
        self.assertAlmostEqual(self.controller._playback_duration, 8.0, delta=.2)
        begin = self.controller._phase_since
        output = self.put(begin + 7.6, detected=False)
        self.assertEqual(output.behavior, 'PLAYBACK')
        output = self.put(begin + 8.3, detected=False)
        self.assertEqual(output.behavior, 'STANDBY')

    def test_missing_face_does_not_count_gap_and_no_double_count_on_same_frame(self):
        for i in range(1, 16):
            self.put(i * .05)
        before = self.controller._emotion_seconds['Happy']
        for i in range(3):
            self.controller.step(.76, (90.,)*4)  # no new camera frames
        self.assertAlmostEqual(self.controller._emotion_seconds['Happy'], before)
        for i in range(16, 35):
            self.put(i * .05, detected=False)
        self.assertAlmostEqual(self.controller._emotion_seconds['Happy'], before)

    def test_face_tracking_speed_is_mildly_faster_with_unchanged_servo_speed(self):
        cfg = self.controller.settings
        self.assertEqual(cfg.track_max_speed_deg_s, 17.0)
        self.assertEqual(cfg.track_max_y_speed_deg_s, 12.0)
        self.assertEqual(self.config.speed, 150)
        for i in range(1, 10):
            self.put(i*.05, x=.9, y=.9)
        self.assertEqual(self.controller.behavior,'TRACKING')


class RuntimeYellowTests(unittest.TestCase):
    def test_face_recognized_sets_solid_yellow_and_search_light_unchanged(self):
        config = load_runtime_config(DIR/'robot_config.json')
        mailbox = CameraMailbox()
        adapter = MainCpgAdapter(start_time=0,mailbox=mailbox,
                                 profile_path=DIR/'cpg_profile.json',loop_hz=config.loop_hz)
        ctrl = FaceSearchController(config, mailbox)
        rt = RobotRuntime(config, HardwareMapper(config), FakeBus(), adapter, face_search=ctrl)
        for t,cmd in [(0,RuntimeCommand.CONNECT),(.05,RuntimeCommand.ARM),(.10,RuntimeCommand.CENTER)]:
            rt.submit(cmd); rt.process_once(t)
        self.assertEqual(rt.latest_snapshot().state, RuntimeState.ARMED)
        rt.submit(RuntimeCommand.CAMERA_AUTO_ON)
        rt.process_once(.2)
        def put(t,detected=True):
            mailbox.publish(captured_at=t,observation={'detected':detected,
                'face_x':.8 if detected else None,'face_y':.8 if detected else None,
                'emotion':'happy','strength':.95})
            rt.process_once(t+.001)
            return rt.latest_snapshot()
        snap=put(.3,False)
        snap=put(.35,False)
        self.assertEqual(snap.light['color'], '#ffb000')
        for i in range(8,15): snap=put(i*.05)
        self.assertEqual(snap.camera_behavior, 'TRACKING')
        self.assertEqual(snap.light['color'], '#ffd43b')
        self.assertNotIn('pattern',snap.light)
        self.assertGreater(snap.observed_seconds['Happy'], 0)

if __name__=='__main__': unittest.main()

class TwentyLedYellowTests(unittest.TestCase):
    def test_actual_config_has_20_and_steady_yellow_uses_same_color_for_all(self):
        from tools.robot_cpg_gui.led_gpio import LedGpioDriver, load_led_config
        from unittest.mock import patch
        import sys, types
        led_cfg = load_led_config(DIR/'led_gpio.json')
        self.assertEqual(led_cfg.pixel_count,20)
        self.assertEqual(led_cfg.mode,'ws2812_spi')
        class Neo:
            def __init__(self,*args,**kwargs):
                self.values=[(0,0,0)]*args[1]
            def fill_strip(self,r,g,b): self.values=[(r,g,b)]*len(self.values)
            def update_strip(self,sleep_duration=None): pass
            def clear_strip(self): self.fill_strip(0,0,0)
            def close(self): pass
        with patch.dict(sys.modules, {'pi5neo':types.SimpleNamespace(Pi5Neo=Neo)}), \
             patch('tools.robot_cpg_gui.led_gpio.Path.exists', return_value=True):
            leds=LedGpioDriver(led_cfg)
            leds.update({'color':'#ffd43b','brightness':.6})
            self.assertEqual(len(leds._pixel.values),20)
            self.assertEqual(len(set(leds._pixel.values)),1)
            self.assertTrue(all(v>0 for v in leds._pixel.values[0]))
            leds.close()
