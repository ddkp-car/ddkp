# Release — Camera Face Search / Tracking / 20s Standby / GPIO LED

ฐานที่ใช้: `robot_cpg_gui_NUMPAD_KEYBOARD_FIXED_2026-11-12.zip`

เพิ่ม:
- `face_search.py` และ `face_search_config.json`: Safe SEARCHING → TRACKING → STANDBY 20 วินาที → SEARCHING
- MediaPipe ส่ง `face_x`, `face_y` (normalized 0–1) ให้ระบบติดตามใบหน้า
- `START CAMERA` เปิดระบบ Auto Search เมื่อครบขั้นตอน Connect/Enable/Go Center และมีเฟรมล่าสุดจริง
- Search ใช้ Servo J1, J2–J4 ใช้ Standby; Track ใช้ J1 หันตามหน้า ส่วน J2–J4 เข้าสมการ SO(2) / Hormone / Pose เดิม
- แสดงสถานะการค้นหาและตำแหน่ง X ของใบหน้าใน GUI
- `GPIO / LED (Pi)` และ `led_gpio.py` + `led_gpio.json`: ช่อง BCM PWM Mono/RGB เตรียมไว้ โดยค่าเริ่มต้น `enabled=false`
- การตรวจกล้องหมดเวลา, การตรวจ Servo ไม่ถึงปลายทาง, กฎ Emergency/Center เดิม

คงเดิม **ทุกไบต์**: `robot_config.json`, `cpg_profile.json`, `keyboard_hotkeys.py` (Speed 150)

> หมายเหตุ: J2 +45°, J3 -90° คือมุม OFFSET จาก Neutral 90°; J4 120° เป็นองศาสัมบูรณ์
> ขอบเขตที่จูนไว้เดิมบังคับให้ใช้ (J1,J2,J3,J4)=(90°,120°,60°,120°) ในท่า Standby
> ไม่มีการเพิ่มช่วงมุม Servo เพื่อสั่ง 135° หรือ 0° โดยข้าม safe envelope เดิม

ทดสอบ: pytest 110 passed, mock GUI smoke passed, mock runtime Camera→Tracking→Stop→Center passed.
ยังไม่เคยตรวจการกวาดมุมและการขับ GPIO บนฮาร์ดแวร์จริง
