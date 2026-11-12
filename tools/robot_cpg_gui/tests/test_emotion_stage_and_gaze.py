"""Regression tests for camera gaze priority, multi-stage emotion and RGB LEDs."""
from __future__ import annotations

import math
import unittest
from pathlib import Path
from types import SimpleNamespace

from fakes import FakeBus
from tools.robot_cpg_gui.camera import CameraMailbox
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.emotion_stage import EmotionStageController, StageSettings, load_stage_settings
from tools.robot_cpg_gui.face_search import FaceSearchController, load_face_search_settings
from tools.robot_cpg_gui.maincpg_adapter import MainCpgAdapter
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.runtime import RobotRuntime, RuntimeCommand, RuntimeState

DIR = Path(__file__).resolve().parents[1]


def frame(emotion='Happy', hormone=0.95, angles=(90., 90., 72., 100.)):
    hs = {'Happy': 0., 'Sad': 0., 'Confused': 0.}
    if emotion in hs:
        hs[emotion] = hormone
    return SimpleNamespace(q_deg=angles, emotion=emotion, hormones=hs,
                           light={'color':'#ffcc44', 'brightness':0.6})


class StageUnitTests(unittest.TestCase):
    def setUp(self):
        self.c = EmotionStageController()

    def test_defaults_and_invalid_config(self):
        cfg = load_stage_settings(DIR/'emotion_stage_config.json')
        self.assertEqual(cfg.happy_peak_hold_s, 3.)
        self.assertEqual(cfg.other_peak_hold_s, 5.)
        self.assertEqual(cfg.hormone_peak_threshold, 0.9)
        with self.assertRaises(ValueError):
            load_stage_settings(DIR/'face_search_config.json')

    def test_manual_selection_without_real_face_never_escalates(self):
        out = None
        for i in range(140):
            out = self.c.step(i*0.05, frame(), tracking=False,
                              camera_emotion=None, camera_strength=0)
        self.assertEqual(out.label, 'BASE')

    def test_happy_requires_peak_hold_three_seconds_not_three_since_face(self):
        for i in range(60):
            t = i * 0.05
            self.assertNotIn('STAGE2', self.c.step(t, frame(hormone=0.89), tracking=True,
                    camera_emotion='happy', camera_strength=0.8).label)
        for i in range(70):
            t = 3.0 + i * 0.05
            result = self.c.step(t, frame(), tracking=True,
                                 camera_emotion='happy', camera_strength=0.8)
        self.assertEqual(result.label, 'STAGE2 Happy')
        self.assertGreaterEqual(result.peak_held_s, 3.0)

    def test_happy_dance_and_rainbow_change_over_time(self):
        cfg = StageSettings(stage_ramp_s=0.2)
        c = EmotionStageController(cfg)
        for i in range(75):
            now = i*0.05
            c.step(now, frame(), tracking=True, camera_emotion='happy', camera_strength=0.95)
        samples = [c.step(t, frame(), tracking=True, camera_emotion='happy', camera_strength=0.95)
                   for t in (3.8, 4.2, 4.6, 5.0)]
        self.assertTrue(all(x.label == 'STAGE2 Happy' for x in samples))
        self.assertGreater(len({s.light['color'] for s in samples}), 1)
        self.assertGreater(len({round(s.cpg_angles[1], 2) for s in samples}), 1)
        self.assertGreater(len({round(s.cpg_angles[2], 2) for s in samples}), 1)
        for x in samples:
            self.assertLessEqual(abs(x.gaze_wiggles[0]), cfg.max_j1_gaze_wiggle_deg)
            self.assertLessEqual(abs(x.gaze_wiggles[1]), cfg.max_j4_gaze_wiggle_deg)
            self.assertTrue(all(60 <= j <= 120 for j in x.cpg_angles))
            self.assertTrue(x.light['color'].startswith('#'))

    def test_other_stages_have_distinct_behaviors_and_lights(self):
        results = {}
        for emotion, raw in [('Sad','sad'),('Confused','confused'),('Neutral','neutral')]:
            c = EmotionStageController(StageSettings(stage_ramp_s=0.2))
            for i in range(115):
                t = i*0.05
                out = c.step(t, frame(emotion, hormone=0.96), tracking=True,
                             camera_emotion=raw, camera_strength=0.9)
            self.assertEqual(out.label, f'STAGE2 {emotion}')
            results[emotion] = out
        self.assertEqual(len({v.light['color'] for v in results.values()}), 3)
        self.assertEqual(len({tuple(round(a, 1) for a in v.cpg_angles[1:3]) for v in results.values()}), 3)

    def test_no_face_and_wrong_expression_reset_peak_and_stage(self):
        for i in range(90):
            out = self.c.step(i*0.05, frame(), tracking=True,
                              camera_emotion='happy', camera_strength=0.9)
        self.assertEqual(out.label, 'STAGE2 Happy')
        out = self.c.step(4.51, frame(), tracking=True,
                          camera_emotion='sad', camera_strength=0.9)
        self.assertEqual(out.label, 'BASE')
        out = self.c.step(4.56, frame(), tracking=True,
                          camera_emotion='happy', camera_strength=0.9)
        self.assertEqual(out.label, 'PEAK Happy')
        self.assertLess(out.peak_held_s, 0.1)
        out = self.c.step(4.61, frame(), tracking=False,
                          camera_emotion='happy', camera_strength=0.9)
        self.assertEqual(out.label, 'BASE')


class GazeTests(unittest.TestCase):
    def setUp(self):
        self.config = load_runtime_config(DIR/'robot_config.json')
        self.mailbox = CameraMailbox()
        self.tracker = FaceSearchController(self.config, self.mailbox)

    def obs(self, t, x=0.5, y=0.5, detected=True, emotion='happy', strength=0.9):
        self.mailbox.publish(captured_at=t, observation={
            'schema': 1, 'stream_id':'gaze', 'seq':int(t*1000),
            'detected':detected,'face_x':x if detected else None,
            'face_y':y if detected else None, 'emotion':emotion,
            'strength':strength,
        })

    def test_j3_standby_45_and_gaze_two_axes(self):
        self.assertEqual(self.tracker.standby_pose[2], 45)
        self.tracker.start(0)
        for i in range(1,4):
            t=i*0.05
            self.obs(t, x=0.8, y=0.8)
            out = self.tracker.step(t, (100, 95, 70, 118))
        self.assertEqual(out.behavior, 'TRACKING')
        start = out.angles
        self.obs(.20, x=0.8, y=0.8)
        end = self.tracker.step(.20, (80, 95, 70, 61))
        self.assertLess(end.angles[0], start[0])
        self.assertLess(end.angles[3], start[3])
        self.assertAlmostEqual(end.angles[2], start[2], delta=1.01)
        self.assertEqual(self.tracker.latest_expression(.20), ('happy', 0.9))

    def test_gaze_priority_over_large_personality_head_pose_and_stage_wiggles(self):
        self.tracker.start(0)
        for i in range(1,4):
            t=i*.05
            self.obs(t)
            out=self.tracker.step(t, (90, 95, 85, 120))
        self.assertEqual(out.behavior, 'TRACKING')
        self.obs(.20)
        p1=self.tracker.step(.20, (90, 95, 85, 120), gaze_wiggles=(1.0,1.0))
        self.obs(.25)
        p2=self.tracker.step(.25, (90, 95, 85, 60), gaze_wiggles=(-1.0,-1.0))
        # A 60-degree CPG head change must not cause a large gaze jump.
        self.assertLessEqual(abs(p2.angles[3] - p1.angles[3]), 1.00001)
        self.assertLessEqual(abs(p2.angles[0] - p1.angles[0]), 1.00001)

    def test_missing_vertical_landmarks_preserves_horizontal_tracking(self):
        self.tracker.start(0)
        for i in range(1,5):
            t=i*.05
            self.mailbox.publish(captured_at=t, observation={
                'detected':True, 'face_x':0.8, 'emotion':'neutral', 'strength':0})
            out=self.tracker.step(t, (90,90,90,90))
        self.assertEqual(out.behavior, 'TRACKING')
        self.assertIsNone(out.face_y)


class RuntimeStageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config=load_runtime_config(DIR/'robot_config.json')
        self.mailbox=CameraMailbox()
        self.bus=FakeBus()
        self.adapter=MainCpgAdapter(start_time=0, mailbox=self.mailbox,
                                    profile_path=DIR/'cpg_profile.json', loop_hz=self.config.loop_hz)
        self.mapper=HardwareMapper(self.config)
        self.finder=FaceSearchController(self.config, self.mailbox)
        self.runtime=RobotRuntime(self.config,self.mapper,self.bus,self.adapter,face_search=self.finder)
        for t, cmd in [(0,RuntimeCommand.CONNECT),(.05,RuntimeCommand.ARM),(.1,RuntimeCommand.CENTER)]:
            self.runtime.submit(cmd); self.runtime.process_once(t)
        self.assertTrue(self.runtime.latest_snapshot().center_ready)
        self.runtime.submit(RuntimeCommand.CAMERA_AUTO_ON)
        self.runtime.process_once(.2)

    def publish(self,t,emotion='happy',x=.5,y=.5,detected=True):
        self.mailbox.publish(captured_at=t, observation={
            'schema':1, 'stream_id':'gaze', 'seq':round(t*1000),
            'detected':detected, 'face_x':x if detected else None,
            'face_y':y if detected else None, 'emotion':emotion, 'strength':.95,
        })

    def test_auto_happy_stage_and_rainbow_with_servo_bounds(self):
        self.publish(.3)
        self.runtime.process_once(.35)
        for i in range(8,113):
            t=i*.05
            self.publish(t)
            self.runtime.process_once(t+.001)
            snapshot=self.runtime.latest_snapshot()
            self.assertTrue(all(self.config.servos[j].safe_min<=raw<=self.config.servos[j].safe_max
                                for j,raw in snapshot.target_raw.items()))
        result=self.runtime.latest_snapshot()
        self.assertEqual(result.camera_behavior,'TRACKING')
        self.assertEqual(result.personality_stage,'OBSERVING')
        self.assertEqual(result.emotion,'Happy')
        self.assertEqual(self.bus.events[-1][0], 'sync')
        old_color=result.light['color']
        for i in range(113,124):
            t=i*.05
            self.publish(t)
            self.runtime.process_once(t+.001)
        self.assertEqual(old_color,self.runtime.latest_snapshot().light['color'])

    def test_manual_numpad_keeps_face_tracking_and_never_auto_escalates_wrong_emotion(self):
        self.adapter.select_manual_mode('Sad')
        self.publish(.3, emotion='happy', x=.9, y=.9)
        self.runtime.process_once(.35)
        for i in range(8,135):
            t=i*.05
            self.publish(t, emotion='happy',x=.9,y=.9)
            self.runtime.process_once(t+.001)
        result=self.runtime.latest_snapshot()
        self.assertEqual(result.emotion,'Sad')
        self.assertEqual(result.camera_behavior,'TRACKING')
        self.assertEqual(result.personality_stage,'OBSERVING')
        self.assertLess(result.cpg_deg[0], 90)
        self.assertLess(result.cpg_deg[3], 90)

    def test_face_loss_cancels_dance_and_return_standby_then_emergency(self):
        self.publish(.3)
        self.runtime.process_once(.35)
        for i in range(8,112):
            t=i*.05
            self.publish(t)
            self.runtime.process_once(t+.001)
        self.assertEqual(self.runtime.latest_snapshot().personality_stage,'OBSERVING')
        for i in range(112,140):
            t=i*.05
            self.publish(t,detected=False)
            self.runtime.process_once(t+.001)
        self.assertEqual(self.runtime.latest_snapshot().camera_behavior,'PLAYBACK')
        self.assertEqual(self.runtime.latest_snapshot().personality_stage,'STAGE1 Happy')
        self.runtime.request_emergency()
        self.runtime.process_once(7.1)
        self.assertEqual(self.runtime.latest_snapshot().state,RuntimeState.EMERGENCY)
        self.assertFalse(any(x.torque_enabled for x in self.bus.feedback.values()))


if __name__ == '__main__':
    unittest.main()
