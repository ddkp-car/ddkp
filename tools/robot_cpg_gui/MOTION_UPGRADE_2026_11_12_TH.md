# Emotion Robotic Lamp — Expressive Motion Upgrade

รุ่นนี้อัปเดตจากเวอร์ชัน Emotion Timer + Pi5Neo GPIO10/20 LEDs ล่าสุด

## สรุปการเปลี่ยนแปลง

- ลดค่า `phi` ของ CPG เพราะเดิมที่ 20 Hz การแกว่งเร็วเกินกว่าตัวจำกัดความเร็ว 20°/s จะตามทัน จึงเพิ่มระยะ *มุมคำสั่ง* ที่ใช้ได้จริงโดยไม่เพิ่ม Servo Speed
- เพิ่ม Gain ของ J1 และ J2/J3/J4 โดยเฉพาะ Neutral, Happy, Confused และปรับ Pose/keyframe ให้ทุกข้อเคลื่อนไหวชัดเจนขึ้น
- Confused ย้าย pose J1 ที่ค้างอยู่ตรง 120° ให้มีระยะเหลือสำหรับแกว่งกลับไปมา ไม่ติด Clamp ที่ปลายช่วง
- เมื่อคนออกจากกล้อง Playback เริ่มด้วย **Stage 1 (CPG พื้นฐาน)** ก่อน แล้วเข้าสู่ **Stage 2 (ท่าใหม่ประสานทั้ง 4 ข้อ)** โดยมีระยะเวลารวมเท่ากับเวลาของอารมณ์ที่เก็บได้ (ขั้นต่ำ/สูงสุดยังคงตาม config เดิม)
- Stage 2: Happy เต้นแรงขึ้นและไฟ Rainbow, Confused ขยับไม่พร้อมกัน, Sad ขยับช้าเหมือนเศร้า, Neutral เป็นคลื่นแบบไม่แข็งทื่อ
- Standby ของ **J2 = +20° จาก Center 90° (มุมคำสั่ง 110°)**, J3 = 45° ตามเดิม
- Face Tracking J1 ปรับ Gain 32 → 38 และอัตรา 14 → 17°/s เฉพาะการติดตามหน้า ไม่เปลี่ยนการกวาดค้นหาหรือความเร็ว Servo

## ค่าหลักที่เปลี่ยน

| Personality | Phi | Gain J1/J2/J3/J4 |
| --- | ---: | --- |
| Neutral | 0.09 | 21 / 20 / 18 / 18 |
| Happy | 0.12 | 24 / 23 / 19 / 19 |
| Sad | 0.095 | 13 / 12 / 10 / 12 |
| Confused | 0.11 | 28 / 21 / 19 / 18 |

## ค่าที่ไม่เปลี่ยน

- `robot_config.json` — Servo speed 150, center/raw limits, motor directions, 20 Hz, hardware scale
- `led_gpio.json` — GPIO10, Pi5Neo SPI, 20 ดวง
- `emotion_stage_config.json`, ระบบสะสมเวลาของอารมณ์, การกลับ Standby 20 วินาที, ระบบ Emergency และขอบเขต CPG 60–120°
- ค่า Global CPG `max_slew_deg_s=20` และโค้ดสมการ SO(2) เดิม

## วิธีติดตั้งบน Pi

ปิดหุ่นยนต์และบันทึกสำรองโปรเจกต์ก่อน แล้วส่ง ZIP ไปที่ `/home/ddkp/lamp_ddkp/`

```bash
cd ~/lamp_ddkp/Emotion_Robotic_Lamp_Windows
cp -a tools/robot_cpg_gui "tools/robot_cpg_gui.backup_$(date +%Y%m%d_%H%M%S)"
unzip -o ~/lamp_ddkp/robot_cpg_gui_EXPRESSIVE_MOTION_STAGE2_FASTER_GAZE_2026-11-12.zip -d tools/
source ~/lamp_ddkp/.venv/bin/activate
python3 -m tools.robot_cpg_gui.app --port /dev/ttyACM0 --camera-index 0
```

## คำเตือนการทดสอบฮาร์ดแวร์

CPG ใหม่สั่งมุมเคลื่อนที่กว้างกว่าเดิมอย่างชัดเจน แม้ยังอยู่ในขอบเขตที่ตั้งไว้ ควรเริ่มในพื้นที่โล่ง รองรับแขนหุ่นยนต์ ตรวจสายไฟไม่ให้ตึง และเตรียม Emergency Torque Off ก่อน Start CPG; หากกลไกติดขัดให้หยุดทันที และปรับ Gain ผ่าน GUI ให้น้อยลงก่อนเพิ่มระยะอีก

ผลทดสอบอัตโนมัติเป็นการตรวจโค้ดและ Mock ไม่ใช่การวัดมุมจริงของ Servo
