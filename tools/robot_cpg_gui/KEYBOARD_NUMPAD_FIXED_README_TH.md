# แก้ไข Numpad กดจากคีย์บอร์ดจริง (12 พฤศจิกายน 2569)

ใช้ไฟล์ `robot_cpg_gui_NUMPAD_PERSONALITY_BASELINE_2026-11-12.zip` เวอร์ชันเดิมเป็นต้นฉบับ แก้เฉพาะส่วนรับแป้นพิมพ์ โดยไม่ได้แก้สมการ CPG, Pose/Gain, Hardware Scale, Servo Speed หรือไฟล์ config

## ปุ่มลัดจากคีย์บอร์ด

- **Numpad 1** → Happy
- **Numpad 2** → Sad
- **Numpad 3** → Confused
- **Numpad 4** → Neutral
- **Numpad 0** → AUTO Camera

บน **Windows** เปิด **Num Lock** แล้วใช้แป้นตัวเลขด้านขวา โปรแกรมจะลงทะเบียน Global Numpad Hotkeys อัตโนมัติ กดได้โดยไม่ต้องคลิกเลือกปุ่ม GUI หรือโฟกัสหน้าต่างโปรแกรมก่อน (แม้กำลังดูหน้าต่างอื่น) มีข้อความสถานะ **Keyboard Numpad 0–4 ACTIVE (global)** ให้ตรวจสอบ

**ข้อควรระวัง:** เมื่อเปิด Global Numpad แอปจะรับปุ่ม Numpad 0–4 แม้กำลังใช้แอปอื่นอยู่ หากต้องพิมพ์ตัวเลขในแอปอื่น ให้เอาเครื่องหมายถูกออกจาก `Global Numpad (Num Lock ON)` เพื่อคืนปุ่มให้ Windows ชั่วคราว หากมีแอปอื่นใช้ hotkey อยู่ก่อน จะมีข้อความ `Global Numpad unavailable` แสดงในโปรแกรม

ตอนกรอกค่าในช่องตัวเลขของ **CPG Settings** โปรแกรมจะพัก global hotkeys ให้โดยอัตโนมัติ และเปิดกลับเมื่อออกจากช่องกรอก เพื่อไม่ดักตัวเลขขณะจูนค่า

บน Linux/macOS หรือกรณี Global Hotkeys ของ Windows ใช้ไม่ได้ ยังสามารถกด Numpad ได้ขณะหน้าต่าง GUI มีโฟกัส โดยไม่ต้องกดปุ่มเลือก Personality ด้วยเมาส์ (รองรับ Num Lock เปิด/ปิดสำหรับ keysyms จาก Tk)

**การกด Numpad เป็นเพียงการเปลี่ยน Personality ไม่สั่งให้เริ่ม Servo โดยอัตโนมัติ** ต้อง CONNECT → ENABLE MOTION → GO CENTER (รอสำเร็จ) → START CPG ตามขั้นตอนเดิม และคง EMERGENCY TORQUE OFF เดิม

## วิธีใช้

1. แตก ZIP แล้วแทนที่ `tools/robot_cpg_gui` ในโปรเจกต์เดิม
2. เปิด Windows ด้วย `START_CPG_GUI.bat` หรือรัน `python -m tools.robot_cpg_gui.app --mock` เพื่อทดสอบ
3. ตรวจไฟ Num Lock ว่าเปิด และข้อความ `Keyboard Numpad 0–4 ACTIVE (global)`
4. กด Numpad 1, 2, 3, 4 และ 0 ตรวจหน้าจอว่า `MANUAL: ...` เปลี่ยนทันที
5. ทดสอบการสั่งมอเตอร์จริงภายใต้สภาพแวดล้อมปลอดภัยหลังจากการเลือกโหมดใน Mock ทำงานถูกต้อง

## ไฟล์ที่เปลี่ยน

- `app.py`: รับหมายเลขจากคีย์บอร์ด, แสดงสถานะ และหยุดรับเมื่อปิดแอป
- `keyboard_hotkeys.py`: ตัวรับ Numpad ของ Windows ผ่าน Win32 RegisterHotKey (ไม่ต้องติดตั้ง keyboard/pynput)
- `tests/test_keyboard_physical.py`: ทดสอบปุ่มที่ส่งผ่าน Tk และคิวจาก Win32
- `KEYBOARD_NUMPAD_FIXED_README_TH.md`: คู่มือใช้งาน

หมายเหตุ: ไม่มีการทดสอบ Windows หรือ Servo จริงในเครื่องทดสอบนี้ จึงยังต้องตรวจสอบปุ่มบน Windows และ hardware ของคุณ
