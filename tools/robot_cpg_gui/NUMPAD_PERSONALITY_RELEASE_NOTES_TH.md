# อัปเดต Personality ผ่าน Numpad

> **แก้ไขใหม่ 12 พ.ย. 2569:** เวอร์ชันนี้เพิ่มระบบจับปุ่ม Numpad ผ่าน Windows โดยตรงด้วย `RegisterHotKey` จึงกดปุ่มบนคีย์บอร์ดได้แม้หน้าต่าง GUI ไม่ได้โฟกัส เปิด Num Lock และตรวจข้อความ `Keyboard Numpad 0–4 ACTIVE (global)` ที่หน้าจอ สามารถปิด Global Numpad ด้วย checkbox ได้เมื่อจะใช้ตัวเลขในโปรแกรมอื่น ขณะกรอกค่า CPG Settings โปรแกรมจะพัก hotkeys ให้อัตโนมัติ รายละเอียด: `KEYBOARD_NUMPAD_FIXED_README_TH.md`

ต้นฉบับ: `robot_cpg_gui(3).zip` (ชุดตั้งค่าที่ผู้ใช้อนุมัติแล้ว)

## วิธีใช้งาน

- **Numpad 1** = Happy
- **Numpad 2** = Sad
- **Numpad 3** = Confused
- **Numpad 4** = Neutral
- **Numpad 0** = AUTO (กลับไปใช้ผลตรวจอารมณ์จากกล้อง)
- มีปุ่มเลือกแบบเดียวกันที่แผง Emotion / Hormone / Fault ใน GUI

หมายเหตุสำหรับเวอร์ชันเดิม: เคยจำกัดการกด Numpad เฉพาะตอน GUI มีโฟกัส แต่เวอร์ชันแก้ไขปัจจุบันรองรับ Global Numpad บน Windows เมื่อเปิด Num Lock ตามหมายเหตุด้านบน (บนระบบอื่นยังใช้แบบ GUI-focused)

Manual ไม่ต้องเริ่มกล้อง แต่ยังต้อง **CONNECT → ENABLE MOTION → GO CENTER (รอสำเร็จ) → START CPG** ก่อนมอเตอร์จึงเคลื่อนที่ การเลือก Personality อย่างเดียวไม่สั่งให้มอเตอร์หมุน ระหว่าง RUNNING เปลี่ยนท่าได้ทันที โดยยังคงระบบผสมฮอร์โมน, interpolation, slew limit, การจำกัดมุม, Emergency แบบเดิม

เมื่อกด Numpad 0 แล้วกล้องไม่มีข้อมูลสด ระบบคืนค่า Neutral ตาม watchdog เดิม และจะเปลี่ยนอารมณ์จากกล้องได้เมื่อมีข้อมูลที่ผ่าน gate

## ขอบเขตการแก้

- `app.py` เพิ่ม GUI buttons, key binding และการแสดง Manual / AUTO
- `maincpg_adapter.py` เพิ่ม thread-safe input override API แยกจากกล้อง
- `cpg_engine.py` รองรับ override โหมดโดย **ไม่เปลี่ยน** สมการ SO(2), ความเร็ว, โปรไฟล์ หรือค่าที่ผู้ใช้จูน
- `tests/test_numpad_personality.py` เพิ่มการทดสอบ Numpad, เปลี่ยนโหมด, กล้อง timeout, ความปลอดภัยของขั้นตอน Center
- ปรับ expectation speed ใน `tests/test_mapping.py` ให้ตรง baseline 150 ตามที่อยู่ใน ZIP ล่าสุด
- `robot_config.json` และ `cpg_profile.json` **byte-identical** กับต้นฉบับ

## การทดสอบ

ผ่าน 93 tests พร้อม 23 subtests ด้วย virtual display และ fake ServoBus; ผ่าน integrated GUI mock smoke test โดยใช้ test doubles ของ `servo_calibration_gui` ที่ไม่ได้รวมใน ZIP สำหรับใช้งานจริง ทั้งหมดนี้ไม่ได้ยืนยันการเคลื่อนที่จริง ควรทดสอบแบบไม่มีโหลดและมี Emergency พร้อมใช้ก่อน
