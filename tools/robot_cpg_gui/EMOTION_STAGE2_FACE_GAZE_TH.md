# Emotion Robot — Face Gaze Priority + Emotion Stage 2 (12 พ.ย. 2569)

## เริ่มต้นจากเวอร์ชันใด

แก้ต่อจาก `robot_cpg_gui_J3_STANDBY_MINUS45_2026-11-12.zip` โดย **ไม่เปลี่ยน**
`robot_config.json`, `cpg_profile.json`, `led_gpio.json`, `cpg_engine.py`, ค่า Servo Speed = 150,
ทิศทาง J2, ระยะค้นหาหน้ากว้างของ J1, SO(2) CPG, Gain, Phi, Hormone, Pose,
ระบบ Numpad และ Emergency เดิม

## 1) J3 ที่ Standby = 0° offset

ไฟล์ `face_search_config.json` ตั้ง `standby_offsets_deg = [45, 0, 30]`.

**สำคัญ:** ตัวเลขสามตัวนี้เป็น **offset จาก Center 90°** ตามนิยามเวอร์ชันก่อนหน้า
ไม่ใช่มุมสัมบูรณ์ของ Servo ดังนั้น J3 = 0° offset หมายถึง **J3 มุมคำสั่ง 90°**
หรือ raw center ID2 = 593. ไม่ได้สั่ง absolute 0° ที่อาจออกนอก safe range.
ท่าพักที่คำนวณได้ J1=90, J2=120 (ถูก clamp), J3=90, J4=120.

## 2) การหันตามหน้าไม่ขึ้นกับ Personality

ถ้ากด START CAMERA และ Servo ผ่านขั้นตอน CONNECT -> ENABLE MOTION -> GO CENTER:

- SEARCHING: ค้นหาตามช่วง J1 ที่เซฟไว้, J2/J3/J4 เป็นท่าพัก
- TRACKING: J1 ติดตาม `face_x`, J4 ติดตาม `face_y` เพื่อให้ใบหน้าอยู่ใกล้กึ่งกลางภาพ
- J2/J3 คงเล่น SO(2) CPG ของ Personality ตามปกติ (รวม Numpad 1–4)
- สัญญาณ Personality ของ J4 เหลืออิทธิพลเล็กน้อย เพื่อไม่ให้ดึงหัวออกจากการมองหน้า
- ถ้าคนหายตามเกณฑ์เดิม จะกลับ STANDBY และรอ 20 วินาทีเพื่อ SEARCHING รอบใหม่
- ถ้ากล้องปิดและกำลังใช้ Numpad จะกลับไปใช้ CPG ที่เลือกด้วยการเปลี่ยนมุมแบบมี rate limit
- Numpad 0 ใช้ Camera Emotion AUTO ตามเดิม และ Numpad 1–4 ไม่ปิดระบบติดตามหน้า

**ข้อจำกัดจริง:** การจัดแนวหน้าใช้พิกัดใบหน้าในภาพ (visual servoing)
ความแม่นขึ้นอยู่กับตำแหน่งติดกล้อง การกลับทิศ Servo มุมที่กลไกไปได้จริง
ความเร็ว 150 และความหน่วงของการมองเห็น จึงไม่รับประกันตำแหน่งเป๊ะ 100% ทุกสถานการณ์

ปรับค่าใน `face_search_config.json` แล้วรีสตาร์ตโปรแกรม:

- `track_deadband_x`/`track_deadband_y`: ระยะคลาดเคลื่อนจากจุดกลางภาพที่ปล่อยให้ผ่าน
- `track_gain_deg_s`/`track_gain_y_deg_s`: ความไวต่อความคลาดเคลื่อน
- `track_max_speed_deg_s`/`track_max_y_speed_deg_s`: เพดานอัตราการหมุนเพื่อรักษาโหลด
- `track_invert_x`/`track_invert_y`: พลิกทิศ หากขยับคนแล้วหัวหุ่นยนต์หันหนี
- `track_observation_max_age_s`: หยุดแก้ทิศด้วยภาพเก่า (0.3 วินาที)
- `pose_max_speed_deg_s`: เพดานอัตราการหมุนสำหรับติดตามหน้าและท่าอื่น (20°/s)

## 3) Emotion Stage 2

ตัว CPG สูตรเดิม **ไม่ได้ถูกแทนที่**. มี `emotion_stage.py` เป็น layer ที่สร้าง
motion overlay เพิ่มจาก CPG ก่อนส่งเข้า face tracking และ mapper.

เงื่อนไข Stage 2:
1. กล้องเห็นหน้าและ Face Search เข้าสู่ TRACKING
2. อารมณ์ที่กล้องตรวจ **ตรงกับ Personality ที่กำลังทำ** (กด Numpad อย่างเดียวไม่พอ)
3. ฮอร์โมนของ Personality ถึงเกณฑ์ 0.90 แล้ว **ค้างต่อเนื่อง**: Happy 3 s, ที่เหลือ 5 s
   (Neutral ใช้น้ำหนักที่เหลือจากฮอร์โมนสามตัว)
4. ถ้าหน้าหาย อารมณ์เปลี่ยน ความมั่นใจลดหรือข้อมูลภาพเก่าเกินเกณฑ์ จะรีเซ็ต Stage

ผลลัพธ์ Stage 2:

| Personality | ท่าใหม่ | LED RGB |
|---|---|---|
| Happy | J2/J3 ส่ายสลับเป็นจังหวะคล้ายเต้น, J1/J4 โยกเพียงเล็กน้อยเพื่อรักษาการมองหน้า | วนสีรุ้งตาม HSV hue ในช่วง ~5 s |
| Sad | J2 ก้มช้า, J3 ขยับคล้ายหายใจสะอื้น, หัวพยักเบา | น้ำเงินเต้นความสว่างช้า |
| Confused | J2/J3 โยกไม่พร้อมกัน, หันหน้าเล็กน้อย | สลับม่วง–ฟ้า |
| Neutral | ท่าหายใจเบา ๆ | ขาวนวลหรี่ขึ้นลง |

การเพิ่ม Stage ใช้ smoothstep ramp 1 s, จำกัดมุมด้วย joint limits ของ CPG
แล้วระบบ Face Search จำกัดความเร็วซ้ำอีกชั้นก่อนส่งผ่าน HardwareMapper ที่ยัง
บังคับ safe_min/safe_max. J1/J4 ที่ Stage สร้างมีเพียง wiggle เล็ก ๆ
**โดยการเล็งหน้าเป็นลำดับความสำคัญสูงกว่า**. ถ้าคนหลุดจากภาพ หุ่นยนต์ต้องกลับไป SEARCHING อีกครั้ง

ปรับค่าต่าง ๆ ใน `emotion_stage_config.json` (รีสตาร์ตโปรแกรม):

- `enabled`: เปิด/ปิด Stage 2
- `hormone_peak_threshold`: ค่า Hormone peak, เริ่มที่ 0.90 ไม่ใช้ 1.0 เพราะ Euler มักเข้าใกล้ 1 แบบไม่ถึงพอดี
- `happy_peak_hold_s`: เวลาค้างระดับ peak ก่อน Happy Dance = 3.0
- `other_peak_hold_s`: เวลา peak โหมด Sad/Confused/Neutral = 5.0
- `stage_ramp_s`: ระยะไต่ความแรงของท่าใหม่ 1.0 วินาที
- `camera_emotion_max_age_s`: อายุสูงสุดของผลตรวจอารมณ์ 0.30 วินาที
- `rgb_rainbow_cycle_s`: เวลาที่วนสีรุ้งครบหนึ่งรอบ 5.0 วินาที
- `happy_dance_hz`: ความถี่การโยกตัวของ Happy 0.36 Hz (ไม่ใช่ phi ของ CPG)
- `max_j1_gaze_wiggle_deg`/`max_j4_gaze_wiggle_deg`: ขนาดโยกของแกนที่ต้องใช้เล็งหน้า สูงสุด 3°

หน้าจอสถานะแสดง `Emotion Stage: PEAK Happy | peak hold 2.0s`
และ `Emotion Stage: STAGE2 Happy` เมื่อเริ่มเต้น

## 4) GPIO LED สำหรับ Raspberry Pi

ใช้ `led_gpio.py` และ `led_gpio.json` **ของเดิม** ค่าปัจจุบันยัง `enabled: false`.
หากใช้ **ไฟ RGB แบบมีช่องแยก R/G/B (PWM)** ตั้ง `mode: rgb`, กำหนด GPIO BCM
ทั้งสามช่องและ `enabled: true` ผ่านแท็บ GPIO / LED (Pi) แล้วรีสตาร์ต.

สายรุ้งที่ทำในเวอร์ชันนี้คือ **RGB ทั้งดวงเปลี่ยนสีวนตามเวลา** ไม่ใช่เอฟเฟ็กต์ไล่สี
แตกต่างกันหลายตำแหน่งบนแถบ LED. ถ้าใช้ addressable WS2812/NeoPixel
ต้องเพิ่มไดรเวอร์กับการเดินสายไฟแยกต่างหากก่อน ยัง **ไม่ได้รองรับ** ในชุดนี้.

ห้ามต่อ LED strip หรือ LED กำลังสูงตรงขา GPIO 3.3V ของ Pi;
ใช้ตัวต้านทาน/driver/MOSFET กับแหล่งจ่ายตามกระแส LED และต่อกราวด์ร่วมอย่างเหมาะสม.

## 5) ทดสอบ

โหมด Mock: รันจากโฟลเดอร์โปรเจกต์หลัก `python -m tools.robot_cpg_gui.app --mock`

ทดสอบบน Servo จริง: แยกตรวจทิศ J1 และ J4 ทีละแกนก่อน, ขยับหน้าไปซ้าย/ขวาและขึ้น/ลง
เพื่อดูค่า x/y บนหน้าจอ; ถ้า Servo หันหนี ปรับ `track_invert_x`/`track_invert_y` แล้วรีสตาร์ต.
ให้ปุ่ม Emergency อยู่ใกล้มือและค้ำแขนไว้ขณะทดลอง Stage 2.

ผ่าน Unit/Integration/GUI Mock 130 ข้อในสภาพแวดล้อมทดสอบ.
**ยังไม่ได้ทดสอบ Servo จริง, กล้องจริง หรือไฟ GPIO จริงกับฮาร์ดแวร์ของผู้ใช้**.
