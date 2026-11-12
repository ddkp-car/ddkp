# Local CPG + GUI แก้ค่าได้ — Release Notes (11 พ.ย. 2569)

## แก้ไข

- เปลี่ยนการคำนวณ CPG จาก `face_vision.lamp_controller` เป็น `cpg_engine.py` ในโฟลเดอร์นี้ (SO(2), hormone state, debounce, interpolation, head fade, slew/clamp)
- แยกพารามิเตอร์ของโมเดลไว้ที่ `cpg_profile.json` ตรวจด้วย `cpg_profile.py`
- เพิ่มแท็บ **CPG Settings** พร้อม Global/Input, Neutral, Happy, Sad, Confused; เพิ่ม/ลบ keyframe ได้
- `SAVE & APPLY` ตรวจค่าทุกช่อง, prepare controller, save JSON atomically, swap under runtime lock, ล้าง camera readiness และรอเฟรมใหม่
- ป้องกันกรณี CPG รัน idle จนคำสั่งค้างไกลจาก Center: เมื่อ Start CPG จะ reset การ ramp เป็น 90°/joint ก่อนเริ่มส่ง Servo
- รักษา Motor API, camera worker และตัวแปลงองศาเป็น raw เดิมตามไฟล์ที่แนบ
- ปรับ expected values ใน unit tests ของ Servo direction ให้ตรง `robot_config.json` ที่แนบ (J1–J4: -1, +1, -1, +1) **ไม่แก้ทิศจริงของ Servo**

## ผลตรวจในสภาพแวดล้อมจำลอง

- Python compile ผ่าน
- unit/integration smoke **80 tests ผ่าน** โดยใช้ test doubles ของ `servo_calibration_gui` และ serial (ไม่มีการเชื่อม Servo จริง)
- GUI smoke ผ่านบน Tk/Xvfb; ไม่ได้ทดสอบกล้อง MediaPipe หรือมอเตอร์ SCS15 จริงจากเครื่องของผู้ใช้

## ข้อจำกัดที่ควรรู้

- ต้องวางโฟลเดอร์นี้ใต้ `tools/robot_cpg_gui` ของโปรเจกต์เดิม (ยังต้องใช้ `tools/servo_calibration_gui` เพื่อส่งคำสั่ง Servo และ `face_vision.vision_core` สำหรับกล้อง)
- CPG recurrence ในเวอร์ชันนี้ใช้ SO(2) bounded tanh map ตามขอบเขต/พารามิเตอร์ใน design ที่แนบ ไม่ได้ import หรือรันโมดูล CPG เดิม
- `cpg_hardware_scale`, `max_offset_raw`, centers, directions, speed ยังคงอยู่ใน `robot_config.json` และ **ไม่ได้** อยู่ในแท็บ CPG Settings
- ตรวจ Servo direction และ range จริงทีละ ID; เริ่มจาก mock และ Emergency พร้อมก่อนจ่ายไฟ
