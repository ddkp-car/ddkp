"""Motion-only changes must use more joint travel without changing hardware safety."""
import json
import math
import unittest
from pathlib import Path
from tools.robot_cpg_gui.cpg_engine import LampController
from tools.robot_cpg_gui.cpg_profile import load_profile
from tools.robot_cpg_gui.emotion_playback import EmotionPlayback
from tools.robot_cpg_gui.config import load_runtime_config
from tools.robot_cpg_gui.face_search import FaceSearchController, load_face_search_settings
from tools.robot_cpg_gui.mapping import HardwareMapper
from tools.robot_cpg_gui.camera import CameraMailbox

ROOT = Path(__file__).resolve().parents[1]


class ExpressiveUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(ROOT / 'cpg_profile.json')

    def trace(self, mode):
        controller = LampController(start_time=0., profile=self.profile, dt=0.05)
        controller.begin_motion(0., override_mode=mode)
        results = []
        for i in range(1, 401):
            result = controller.step(i * .05, override_mode=mode)
            if i > 80:  # measure after the initial safe acceleration blend
                results.append(result['q_deg'])
        return results

    def test_neutral_uses_all_four_joints_not_only_head_bow(self):
        results = self.trace('Neutral')
        for j in range(4):
            span = max(v[j] for v in results) - min(v[j] for v in results)
            self.assertGreater(span, 14, f'Neutral J{j+1} should be visibly active')

    def test_happy_and_confused_j1_travel_and_all_modes_stay_bounded(self):
        for mode in ('Neutral', 'Happy', 'Sad', 'Confused'):
            values = self.trace(mode)
            for j in range(4):
                lo, hi = self.profile['joint_limits'][j]['min'], self.profile['joint_limits'][j]['max']
                self.assertTrue(all(lo <= row[j] <= hi for row in values))
            if mode in ('Happy', 'Confused'):
                pan_range = max(x[0] for x in values) - min(x[0] for x in values)
                self.assertGreater(pan_range, 23, f'{mode} J1 range should increase')

    def test_physical_mapper_uses_original_servo_envelope_and_speed(self):
        cfg = load_runtime_config(ROOT/'robot_config.json')
        self.assertEqual(cfg.speed, 150)
        mapper = HardwareMapper(cfg)
        for mode in ('Neutral', 'Happy', 'Sad', 'Confused'):
            for pose in self.trace(mode)[::13]:
                raw = mapper.targets_from_cpg(pose)
                for j, value in raw.items():
                    servo = cfg.servos[j]
                    self.assertGreaterEqual(value, servo.safe_min)
                    self.assertLessEqual(value, servo.safe_max)

    def test_standby_j2_offset_20_from_90_means_nominal_110_and_j3_stays_45(self):
        cfg = load_runtime_config(ROOT/'robot_config.json')
        settings = load_face_search_settings(ROOT/'face_search_config.json')
        finder = FaceSearchController(cfg, CameraMailbox(), settings)
        self.assertEqual(settings.standby_offsets_deg[0], 20)
        self.assertEqual(finder.standby_pose[1], 110)
        self.assertEqual(finder.standby_pose[2], 45)
        self.assertEqual(settings.track_max_speed_deg_s, 17)
        self.assertEqual(settings.pose_max_speed_deg_s, 20)

    def test_stage_two_distinct_from_stage_one_for_every_personality(self):
        for mode in ('Neutral','Happy','Sad','Confused'):
            playback = EmotionPlayback(now=0,mode=mode,profile=self.profile,dt=.05,duration_s=8)
            one = playback.step(.5)
            self.assertEqual(one.stage, 'STAGE1')
            two = None
            for i in range(11,80):
                result = playback.step(i*.05)
                if i==75:
                    two=result
            self.assertEqual(two.stage,'STAGE2')
            # Compare a second-stage output against independent raw SO(2) at
            # precisely the same time to prove it is a genuinely new gesture.
            base = LampController(start_time=0,profile=self.profile,dt=.05)
            base.begin_motion(0,override_mode=mode)
            pure=None
            for i in range(1,80):
                pure=base.step(i*.05,override_mode=mode)
            self.assertTrue(any(abs(two.q_deg[j]-pure['q_deg'][j])>1 for j in range(4)),mode)
            for j,angle in enumerate(two.q_deg):
                self.assertTrue(self.profile['joint_limits'][j]['min'] <= angle <= self.profile['joint_limits'][j]['max'])
            if mode=='Happy':
                self.assertEqual(two.light.get('pattern'), 'rainbow')

    def test_short_observation_can_reach_stage_two_with_scaled_intro(self):
        p = EmotionPlayback(now=0,mode='Neutral',profile=self.profile,dt=.05,duration_s=.8)
        self.assertEqual(p.step(.05).stage, 'STAGE1')
        self.assertEqual(p.step(.4).stage, 'STAGE2')

if __name__=='__main__': unittest.main()
