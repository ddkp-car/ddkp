# Face Search / Face Tracking / Standby / GPIO LED — เวอร์ชันเพิ่มเติม

## หลักการ (ไม่แตะ Servo/CPG ที่จูนไว้)

ไฟล์ `robot_config.json`, `cpg_profile.json` และ `keyboard_hotkeys.py` คงค่าเดิมแบบ byte-for-byte จาก `robot_cpg_gui_NUMPAD_KEYBOARD_FIXED_2026-11-12.zip` (Speed=150)

- กด **CONNECT → ENABLE MOTION → GO CENTER** ให้สำเร็จก่อน โดยต้องมี Servo ทุก ID และ Feedback ปลอดภัย
- กด **START CAMERA** และปล่อย Personality เป็น **AUTO Camera (Numpad 0)**
- ถ้า Center ยังไม่เสร็จ ระบบเพียงตั้งความต้องการไว้ **ไม่สั่งมอเตอร์**; จะเริ่มหลัง Center และกล้องส่งเฟรมล่าสุดจริง
- กล้องยังไม่ส่งเฟรม หรือกล้องล้มเหลว → **ไม่เริ่มกวาดแบบไร้ภาพ**
- `SEARCHING`: Servo ID0 / J1 กวาดจากมุม 60° ไป 120° (ภายในขอบเขตจริงที่อนุญาต) แล้วกลับ `STANDBY` (กรณีไม่มีหน้า)
- `STANDBY`: J1 กลับ Center และ J2/J3/J4 เข้า Standby แบบจำกัดความเร็ว/จำกัดมุม; เมื่อ Servo อยู่ในท่าแล้วจึงเริ่มนับ 20 วินาทีก่อนค้นหาใหม่
- `TRACKING`: พบหน้าติดต่อกัน 3 เฟรม → J1 ค่อย ๆ หมุนเพื่อให้ตำแหน่งแนวนอนของใบหน้าอยู่กลางภาพ (Face X = 0.5) ส่วน J2/J3/J4 ใช้ Emotion/Hormone/SO(2) CPG เดิม
- ถ้าใบหน้าหายเกิน 1 วินาที → กลับ `STANDBY` แล้วค้นหาใหม่ตามวงรอบ; หากพบคนอีกครั้งก่อนครบ 20 วินาทีจะติดตามได้ทันที
- **Stop Camera** จะยกเลิกระบบอัตโนมัติและกลับ Center, ยกเว้นมี Personality แบบ Manual ทำงานอยู่; **Stop → Center** จะยกเลิกระบบค้นหา; **Emergency** ตัด Torque ตามระบบเดิม
- Numpad 1–4 ใช้ Manual Personality ได้เสมอ; Numpad 0 ใช้ AUTO Camera อีกครั้ง (ถ้ากล้องกำลังรัน)

## ความหมายขององศา Standby

ค่าที่ผู้ใช้กำหนด: J2 เอียง **+45°**, J3 **−45°**, J4 **120°**.

**J2 / J3 ตีความเป็นมุม OFFSET จาก Center 90°**, ส่วน J4 เป็นมุมเป้าหมายสัมบูรณ์ 120° (OFFSET +30°)

ตัวอย่างบนเครื่องปัจจุบัน:

| Joint | Requested nominal | Allowed CPG/hardware range | Applied nominal |
|---|---:|---:|---:|
| J1 | 90° | 60–120° | 90° |
| J2 | 90 + 45 = 135° | 60–120° | 120° |
| J3 | 90 − 45 = 45° | กล้อง Standby 45–120° (ไม่เปลี่ยน CPG 60–120°) | 45° |
| J4 | 120° | 60–120° | 120° |

สำหรับ J3 ระหว่าง SEARCHING/STANDBY อนุญาตให้ขยายจาก ±171 raw เฉพาะด้านก้มลงให้ถึงมุม nominal 45° โดยยังจำกัดด้วยขอบเขต `safe_min/safe_max` ของ Servo ID2; สำหรับ Manual/CPG ปกติยังใช้ ±171 raw เหมือนเดิม. **ไม่ใช่การยืนยันว่าท่าทางเหล่านี้จะไม่ชนกลไกจริง**: ทดสอบทีละข้อและจัดระยะหลีกเลี่ยงจุดชนก่อน

## ปรับการค้นหาและการติดตาม

แก้ไขที่ `face_search_config.json` จากนั้นปิดเปิดโปรแกรมใหม่:

- `scan_min_deg` / `scan_max_deg` ช่วง J1 **ภายใน** safe envelope; ใส่ค่ากว้างกว่าที่อนุญาตจะถูกจำกัด
- `scan_speed_deg_s` เริ่มต้น 12°/s
- `scan_endpoint_tolerance_deg` 2.5°; `scan_endpoint_timeout_seconds` 8 s: ถ้า Feedback J1 ไม่ถึงปลายทางภายในเวลาให้หยุดและเข้า FAULT (Torque Off)
- `face_confirm_frames` 3, `lost_grace_seconds` 1 s
- `standby_seconds` 20 s **เริ่มหลังท่า Standby ถึงตำแหน่ง**
- `track_deadband_x` 0.07 (สัดส่วนความกว้างภาพ), `track_gain_deg_s` 28, `track_max_speed_deg_s` 12
- `track_invert_x`: เปลี่ยนเป็น `true` หาก J1 หมุนไล่ใบหน้าผิดทิศทาง ขณะทดสอบกับกล้องและตัวหุ่นจริง
- `standby_offsets_deg`: `[45, -45, 30]` ตามลำดับ J2,J3,J4 **เป็นค่า OFFSET ไม่ใช่มุม Absolute**

การตรวจจับหน้าใช้ตำแหน่งกลางกรอบ Landmark ของ MediaPipe ไม่ต้องตรวจพบอารมณ์ Happy/Sad ก่อนจึงจะเริ่มติดตาม

## GPIO / LED Raspberry Pi (เตรียมช่องแล้ว)

- แท็บ `GPIO / LED (Pi)` มีช่อง `Mono / R / G / B` หน่วย **BCM GPIO number** และปุ่ม Save
- ค่าใน `led_gpio.json` เริ่มต้น `enabled:false` และทุก GPIO เป็น `null`: **ไม่เปิดขาใด ๆ อัตโนมัติ**
- หากต้องการ LED เดี่ยว เลือก `mono` และตั้ง Mono GPIO; RGB เลือก `rgb` และระบุ 3 ขาไม่ซ้ำ (Red Green Blue)
- ติ๊ก `Enable GPIO output`, Save แล้ว **restart โปรแกรม** ถึงจะเริ่มใช้ GPIO ที่กำหนด
- บน Pi ติดตั้ง `gpiozero` และ pin factory ที่รองรับบอร์ด เช่น `python3-lgpio` (ระบบปฏิบัติการ/virtualenv ต้องมองเห็นโมดูล)
- ค่าสีและความสว่างของ LED ตาม CPG: SEARCHING = สีเหลืองอำพัน, STANDBY = น้ำเงินอ่อน, TRACKING = สี/ความสว่างของ Emotion เดิม, นอกสถานะ RUNNING = OFF
- เมื่อปิด GUI, Emergency, หรือเกิดข้อผิดพลาด GPIO จะสั่ง OFF

**สำคัญ:** GPIO ของ Pi เป็นสัญญาณ 3.3 V กระแสต่ำ ต้องใช้ตัวต้านทาน/วงจรขับ LED ที่เหมาะสม และใช้ MOSFET / Driver + แหล่งจ่ายแยกเมื่อเป็นหลอดหรือแถบ LED กระแสสูง ห้ามต่อ LED strip กับ GPIO โดยตรง

## การทดสอบ

เปิดโหมดจำลองจากโฟลเดอร์โปรเจกต์ (ที่มี `tools/servo_calibration_gui`):

```bash
python -m tools.robot_cpg_gui.app --mock
```

กล้อง Mock แสดงหน้าเคลื่อนแนวนอนและเปลี่ยนอารมณ์ทุก 4 วินาที การหายหน้า/ค้นหาซ้ำทดสอบเพิ่มเติมด้วย unit tests และ `face_search_config.json`

การทดสอบด้วย Servo จริง: ยึดแขนให้มั่นคง, แยกวัตถุที่อาจชน, ตรวจมุมและทิศ J1 ก่อน, คงปุ่ม Emergency ให้กดได้เสมอ จากนั้นจึงทดสอบท่า Standby และการติดตาม
