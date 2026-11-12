# J3 Standby −45° (จาก Center) — 12 พฤศจิกายน 2569

ฐาน: `robot_cpg_gui_J1_WIDE_SCAN_J2_REVERSED_2026-11-12.zip`

- ปรับ `face_search_config.json` -> `standby_offsets_deg` จาก `[45, -90, 30]` เป็น `[45, -45, 30]` (เรียง J2, J3, J4)
- J3 ใช้ **มุม OFFSET −45° จาก Center 90°** ดังนั้นเป้าหมาย nominal คือ **45°** ไม่ใช่ absolute −45°
- Servo ID2: `center raw=593`, `direction=-1`, `nominal_degrees_per_count=180/1024`, scale=1.0; เมื่อเป้าหมาย 45° คำสั่งโดยประมาณคือ raw **849** (อยู่ภายใน `safe_min=165 .. safe_max=1023`)
- เปิดช่วงขยับ J3 ลงถึงเป้าหมายนี้เฉพาะ **SEARCHING / STANDBY** และระหว่างการเปลี่ยนเข้าสู่ Tracking หรือ Manual แบบจำกัดอัตราการเปลี่ยนมุม เพื่อไม่ให้กระโดดตอนข้าม 60°
- กลับไปใช้ CPG/Manual ปกติเมื่อพ้นช่วงเปลี่ยนท่า: J3 ยังถูกจำกัดด้วย CPG profile และ `max_offset_raw=171` ตามเดิม
- J1 กวาดมุมกว้าง, J2 กลับทิศ, Servo Speed 150, CPG profile, Numpad, GPIO และกล้องคงเดิมทั้งหมด

**ความปลอดภัย:** มุม 45° ของ J3 แม้อยู่ใน safe raw envelope ที่ตั้งไว้ แต่ยังไม่ได้ตรวจยืนยันกับกลไกจริง ให้ลองพื้นที่โล่ง/รองรับแขน เตรียม Emergency Torque Off และตรวจว่าข้อต่อไม่ชนหรือดึงสายไฟตึง หากมุมทางกายภาพไม่ตรงกับทิศที่ต้องการ ให้หยุดทดสอบก่อน
