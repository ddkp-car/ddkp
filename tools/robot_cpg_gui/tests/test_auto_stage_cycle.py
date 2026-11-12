"""Regression tests for auto search, finite Stage 2, standby and expression gating."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.robot_cpg_gui.camera import CameraMailbox
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.face_search import FaceSearchController
from tools.robot_cpg_gui.emotion_stage import EmotionStageController, load_stage_settings

DIR = Path(__file__).resolve().parents[1]


def frame(t, selected='Happy'):
    hormones = {'Happy': 0.0, 'Sad': 0.0, 'Confused': 0.0}
    if selected in hormones:
        hormones[selected] = 0.96
    return SimpleNamespace(
        q_deg=(90.0, 90.0, 75.0, 100.0),
        emotion=selected, hormones=hormones,
        light={'color': '#ffaa00', 'brightness': .6}
    )


class StageCycleTests(unittest.TestCase):
    def test_dance_ends_after_five_seconds_and_cannot_repeat_until_reset(self):
        stage = EmotionStageController(load_stage_settings(DIR/'emotion_stage_config.json'))
        self.assertEqual(stage.settings.stage2_duration_s, 5.0)
        labels = []
        for i in range(1, 163):
            t = round(i * .05, 7)
            out = stage.step(t, frame(t), tracking=True,
                             camera_emotion='happy', camera_strength=.9)
            labels.append((t, out.label))
        started = next(t for t, label in labels if label == 'STAGE2 Happy')
        self.assertGreaterEqual(started, 3.0)
        done = next(t for t, label in labels if label == 'STAGE2_DONE Happy')
        self.assertAlmostEqual(done - started, 5.0, delta=.1)
        out = stage.step(done + .05, frame(done+.05), tracking=True,
                         camera_emotion='happy', camera_strength=.9)
        self.assertEqual(out.label, 'WAIT_NEW_EMOTION')
        stage.reset()
        self.assertEqual(stage.step(done+.1, frame(done+.1), tracking=True,
                                    camera_emotion='happy', camera_strength=.9).label,
                         'PEAK Happy')

    def test_record_face_then_play_observed_seconds_and_search_again(self):
        config=load_runtime_config(DIR/'robot_config.json')
        mailbox=CameraMailbox(); tracker=FaceSearchController(config,mailbox)
        self.assertEqual(tracker.standby_pose[2],45.0)
        tracker.start(0)
        def observe(t,detected=True,emotion='happy'):
            mailbox.publish(captured_at=t,observation={
                'detected':detected,'face_x':.5 if detected else None,
                'face_y':.5 if detected else None,'emotion':emotion,'strength':.9})
            return tracker.step(t,(90.0,)*4, actual_angles={j:90 for j in range(4)})
        for i in range(1,21):motion=observe(i*.05)
        self.assertEqual(motion.behavior,'TRACKING')
        for i in range(21,50):motion=observe(i*.05,detected=False)
        self.assertEqual(motion.behavior,'PLAYBACK')
        self.assertEqual(tracker.playback_mode,'Happy')
        t=tracker._phase_since
        recorded = tracker._playback_duration
        self.assertGreaterEqual(recorded, tracker.settings.playback_min_seconds)
        self.assertLess(recorded, 1.0)  # only ~0.8 seconds of observed face frames
        motion=observe(t+recorded-0.02,detected=False)
        self.assertEqual(motion.behavior,'PLAYBACK')
        motion=observe(t+recorded+0.05,detected=False)
        self.assertEqual(motion.behavior,'STANDBY')
        motion=observe(t+recorded+20.1,detected=False)
        self.assertEqual(motion.behavior,'SEARCHING')

    def test_no_new_face_search_motion_before_camera_frames(self):
        config=load_runtime_config(DIR/'robot_config.json')
        mailbox=CameraMailbox()
        tracker=FaceSearchController(config, mailbox)
        tracker.start(0.0)
        out=tracker.step(.1, (90.0,)*4)
        self.assertEqual(out.behavior, 'WAIT_CAMERA')
        self.assertEqual(out.angles, (90.0,)*4)

if __name__=='__main__':
    unittest.main()
