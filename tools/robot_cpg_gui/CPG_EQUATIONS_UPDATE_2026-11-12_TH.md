# อัปเดตสมการ CPG (12 พฤศจิกายน 2569)

## ต้นฉบับ

แก้จากไฟล์ `Pasted text(20261111-182847).txt` โดยยึดส่วนสมการที่ส่งมาจริง
ไฟล์ต้นฉบับจบกลางบรรทัดที่ `t = round` ใน `simulate()` จึงไม่มีโค้ดจำลองช่วงหลังจากนั้นให้ย้ายตรงตัว
การแก้ครั้งนี้นำส่วนที่ครบถ้วน (SO(2), Hormone, pose/brightness interpolation,
4-joint decoder และ head fade) มาประยุกต์เข้ากับ runtime ตัวจริงที่มีอยู่เดิม
แทนที่จะสร้างฟังก์ชันจำลองที่ไม่ครบ

## 1. SO(2) CPG ใหม่

```
a1_next = alpha * (cos(phi) * o1_old + sin(phi) * o2_old)
a2_next = alpha * (-sin(phi) * o1_old + cos(phi) * o2_old)
o1_next = tanh(a1_next)
o2_next = tanh(a2_next)
```

- คำนวณทั้ง a1/a2 โดยอ่านค่า o1/o2 **รอบก่อนหน้า** ชุดเดียวกัน
- ออสซิลเลเตอร์เริ่มที่ `[0.10, 0.00]` และ warm-up Neutral 200 steps ตามต้นฉบับ
- alpha อยู่ที่ `global.so2_alpha` เพราะทุก mode ในต้นฉบับใช้ค่าเดียวกัน 1.10
- ค่าพารามิเตอร์ยังปรับผ่าน CPG Settings GUI ได้

## 2. สมการมุม 4 ข้อต่อ

```
J1 = center[0] + blend * gain[0] * o1
J2 = center[1] + blend * gain[1] * o2
J3 = center[2] - blend * gain[2] * o1
J4 = center[3] + head_blend * gain[3] * o2
```

- `blend` เพิ่มจาก 0 ถึง 1 ระหว่าง 1 วินาทีแรก (`global.oscillation_blend_s` เปลี่ยนได้)
- J4 เมื่อ mode มี `head_fade` ใช้ `head_blend = blend * (1 - progress)`
- fade ใช้กับ **การแกว่ง J4 หลังผสม gain จากฮอร์โมนแล้ว** ตามตัวอย่าง `decode_angles()`
- คง clamp รายข้อต่อและ slew limit เดิม เพื่อจำกัดการส่งคำสั่งไป Servo
- การแปลงองศา -> raw pulse ใช้ `mapping.py` และ `robot_config.json` เดิม

## 3. Hormone และ pose ตามเวลา

- รักษาสมการ Euler: `H_next = H_old + dt * (target-H_old) / tau`
- ใช้ `tau_up/tau_down` ตาม mode, ค่าจาก `cpg_profile.json`
- ผสม center/gain/phase/light ตาม hormone weights พร้อม normalize
- แยกเวลาที่เริ่มของแต่ละ mode เพื่อไม่ให้ keyframe เดิมกระโดดกลับเวลา 0
  ขณะที่ฮอร์โมนของ mode ก่อนหน้ากำลังค่อย ๆ ลดลง
- คงการใช้ J4 จริงเมื่อเริ่ม Sad เพื่อไล่จากมุมปัจจุบันไปยัง keyframe ถัดไป

## 4. ข้อมูลของผู้ใช้ที่ *ไม่แก้*

- `robot_config.json` ไม่เปลี่ยนแม้แต่ byte เดียว: J1/J2/J3/J4 center
  = 410/566/593/413; direction = -1/+1/-1/+1, scale = 1.0
- `cpg_profile.json` ไม่เปลี่ยนแม้แต่ byte เดียว: **Happy J4 ณ 5s = 120°**
  ตาม ZIP ล่าสุดของผู้ใช้ (ต้นฉบับที่ paste ใช้ 105° แต่รอบนี้ขอแก้สมการ)
- กล้อง, adapter, GUI, emergency และระบบเชื่อมต่อมอเตอร์ ไม่แก้
- `RESET TO PASTED DEFAULTS` ใน GUI จะเปลี่ยน Happy J4 กลับ 105° หากผู้ใช้กดเอง

## 5. การทดสอบ

- ทดสอบสูตรการหมุนทั้งสองแกน, seed/warmup, 4-joint decoder,
  fade J4 (รวมกรณีผสม hormone), Euler up/down และ runtime safety
- **87 tests ผ่าน** ใน Linux/Xvfb โดยจำลองโมดูล `servo_calibration_gui` และ `serial`
  ที่ไม่ได้แนบมาใน ZIP (ตัวจำลองมีเฉพาะในระบบทดสอบ ไม่อยู่ใน ZIP ที่ส่งคืน)
- ตรวจด้วย SHA-256 ว่า `robot_config.json`, `cpg_profile.json`, `app.py`,
  `maincpg_adapter.py` และ `camera.py` ยังคง byte เดิมทุกประการ
- ไม่ได้ทดสอบกับมอเตอร์หรือกล้องจริง ต้องทดสอบแบบ mock ก่อนจ่ายไฟ Servo

## วิธีใช้งาน

วาง `robot_cpg_gui` ทับโฟลเดอร์ `tools/robot_cpg_gui` ในโปรเจกต์เดิม
แล้วเปิดจากโฟลเดอร์หลักของโปรเจกต์:

```
python -m tools.robot_cpg_gui.app --mock
```

หลังจากยืนยันการแสดงผลและการเคลื่อนที่ใน mock แล้ว จึงทดลองกับ Servo
โดยเริ่มทดสอบทีละข้อต่อ มีปุ่ม Emergency พร้อมใช้งาน
