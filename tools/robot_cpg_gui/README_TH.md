# Camera CPG + SCS15 GUI

โปรแกรมนี้รวม USB Camera, hormone/CPG แบบ Local และ SCS15 IDs 0–3 ใน Pure Python process เดียว โดยใช้ `face_vision(maincpg)` เฉพาะฝั่งตรวจใบหน้า (MediaPipe) เท่านั้น เหมาะสำหรับรันบน Raspberry Pi 5 แล้วแสดง GUI ที่คอมผ่าน `ssh -X` โดยไม่ต้องใช้ ROS 2 ในรอบแรก

## ใหม่: ปรับ CPG ได้ใน GUI โดยไม่ต้องแก้ Python

CPG engine อยู่ภายใน `tools/robot_cpg_gui/cpg_engine.py` ไม่ import `face_vision.lamp_controller` อีกแล้ว ส่วนการประมวลผล USB Camera/MediaPipe ยังเรียก `face_vision.vision_core` จากโปรเจกต์เดิมตามปกติ

- เปิดแท็บ **CPG Settings** เลือก `Global & Input` หรือ `Neutral`, `Happy`, `Sad`, `Confused`
- ปรับ `SO(2) alpha`, `warmup_steps`, `oscillation_blend_s`, `max_slew_deg_s`, เกณฑ์ตรวจอารมณ์, hormone rise/decay และ joint min/max ได้
- ปรับ `phi`, gains J1–J4, จุด keyframe มุมของแต่ละข้อ, สี LED, กราฟความสว่าง, และช่วง fade หัว J4 ได้ครบ กด `+ Add keyframe` หรือ `−` เพื่อเพิ่ม/ลบแถว
- กด `VALIDATE` เพื่อตรวจสอบก่อน (ไม่สั่งมอเตอร์), `RELOAD SAVED` อ่านไฟล์อีกครั้ง, `RESET TO PASTED DEFAULTS` คืนค่าตั้งต้นในหน้าจอ และ `SAVE & APPLY` เพื่อบันทึก/สลับเครื่องยนต์ CPG ใหม่
- **ปรับ/Apply ได้เฉพาะ DISCONNECTED, CONNECTED หรือ ARMED ที่ขยับกลับ Center เรียบร้อยแล้ว** เท่านั้น ห้ามปรับขณะ RUNNING, CENTERING, STOPPING, FAULT หรือ EMERGENCY
- ไฟล์ค่าจริงคือ `tools/robot_cpg_gui/cpg_profile.json`; ใช้ไฟล์อื่นได้ด้วย `--cpg-profile /path/to/my_cpg.json`
- `SAVE & APPLY` ตรวจทุกค่าก่อน แล้วเขียนไฟล์ JSON แบบ atomic และสลับ controller ที่เตรียมไว้ทั้งหมดในครั้งเดียว ไม่ส่งคำสั่ง Servo ใด ๆ และต้องได้รับเฟรมจากกล้องใหม่ก่อน START CPG
- **ค่าที่เปลี่ยนได้ในหน้านี้เป็นส่วน CPG software เท่านั้น**; ค่า Center, ทิศ Servo, `speed`, `max_offset_raw`, `cpg_hardware_scale` และ nominal conversion ยังคงอยู่ใน `robot_config.json`

### มุม 1 องศา สั่ง Servo เท่าไร?

ค่า default: `nominal_degrees_per_count = 180/1024 = 0.17578125°/raw` ดังนั้น

```
raw_offset = round(servo_direction * (q_deg - 90) / 0.17578125 * cpg_hardware_scale)
raw_offset = clamp(raw_offset, -max_offset_raw, +max_offset_raw)
```

หนึ่งองศาของ CPG ≈ `5.69 raw` เมื่อ scale = `1.0` หรือ `5.12 raw` เมื่อ scale = `0.9` ก่อนปัดเศษ Servo จริงยังถูก clamp ด้วย `safe_min`/`safe_max` ของ ID นั้น

**ตัวอย่าง:** J1 ต้องการจาก 90° ไป 100°, `scale=0.9` จะขยับประมาณ `51 raw` (ถ้าไม่ชน limit) ส่วน gain เป็น **แอมพลิจูดการแกว่งของสูตร CPG เป็นองศา** ไม่ได้แปลว่าสั่งเต็ม gain ในทุกเฟรม เพราะมี hormone weighting, การเริ่มแกว่งแบบ blend, slew limit เริ่มต้น `20°/s`, และ software joint limits เข้ามากำหนดเป้าหมาย

⚠️ ที่ 20 Hz, `20°/s` จำกัดการเปลี่ยนคำสั่งเพียง `1°` ต่อรอบ ซึ่งเท่ากับประมาณ `5–6 raw` ที่ scale 1.0 ตัวเลขนี้ไม่ใช่ความเร็วหรือมุมจริงของกลไก ต้องวัดการเคลื่อนที่จริงก่อนเพิ่มพารามิเตอร์

### อัปเดตโฟลเดอร์ในโปรเจกต์เดิม

นำ **ทั้งโฟลเดอร์** `robot_cpg_gui` จาก ZIP ไปวางทับที่ `tools/robot_cpg_gui` ใน `Emotion_Robotic_Lamp_Windows` แล้วรันตามเดิม ต้องเก็บ `tools/servo_calibration_gui` และ `face_vision(maincpg)` (เฉพาะ vision/โมเดล) จากโปรเจกต์เดิมไว้ด้วย อย่าลบโฟลเดอร์ที่ใช้ควบคุม Servo และโมเดลกล้อง

ทดสอบแบบ mock ก่อน:

```bash
python -m tools.robot_cpg_gui.app --mock --smoke-test
python -m tools.robot_cpg_gui.app --mock
```

### ความเข้ากันได้ของไฟล์ฮาร์ดแวร์

ข้อทดสอบเก่าใน ZIP คาดหวัง Servo direction `-1,-1,+1,+1` แต่ `robot_config.json` ที่แนบมาตั้งไว้ `-1,+1,-1,+1` ดังนั้นเวอร์ชันนี้ **คงค่าจากไฟล์จริงไว้** และแก้ test mapping ให้ตรงกับค่าในไฟล์ โดยไม่ได้เปลี่ยนทิศการหมุนของ J2/J3 สำหรับฮาร์ดแวร์

## ความปลอดภัยที่ตั้งไว้

- จุดอ้างอิงคือ Center raw `410, 566, 593, 413`
- ทิศ raw **อัปเดตล่าสุด** คือ `-1, -1, -1, +1` (J1–J4 ตามลำดับ) โดยกลับ J2 ตามที่ร้องขอ; ตรวจทิศกับแขนจริงก่อนเปิด Torque
- ข้อต่อทั้ง 4 เป็น direct drive 1:1 ไม่มีอัตราทด: CPG `90°` ตรงกับ Center ของแต่ละ ID
- CPG ใช้สเกล nominal ของ SCS15 `180°/1024 raw` และจำกัดช่วงทำงานไว้ `±171 raw` (ประมาณ `±30°`) จาก Center
- Servo speed ปัจจุบัน `150`; control loop `20 Hz`
- ไม่มีปุ่ม Home เพราะ Home ของหัว ID3 เท่ากับขอบต่ำสุด `0`
- `STOP → CENTER` สั่งกลับ Center ช้า ๆ และยังถือ Torque
- `GO CENTER` และ `STOP → CENTER` จะอ่าน feedback จนหยุดจริง ถ้าไม่ถึง Center ภายใน 5 วินาทีจะเข้า FAULT และพยายามตัด Torque
- หลัง `ENABLE MOTION` ต้องกด `GO CENTER` และรอให้สำเร็จก่อนทุกครั้ง จึงจะกด `START CPG` ได้
- `EMERGENCY TORQUE OFF` ไม่สั่งท่ากลับ แต่ตัด Torque ทุก ID ทันทีที่ runtime loop รับ event
- serial fault หรือการปิดโปรแกรมจะใช้เส้นทาง Torque Off เดียวกับ Emergency; หน้าต่างจะไม่ปิดจนกว่า serial owner จะยืนยันว่าหยุดแล้ว
- frame กล้องที่เก่ากว่า 0.5 วินาทีจะไม่ทำให้ CPG พร้อมทำงาน และ camera timeout จะกลับ Neutral
- ห้ามเปิด Feetech FD และ GUI นี้พร้อมกัน เพราะจะแย่ง serial port

## กราฟ

- Servo Feedback แสดงมุมข้อต่อแบบ absolute ในกรอบเดียวกับ CPG: Center = `90°`, ช่วงกราฟปรับอัตโนมัติเพื่อให้เห็น J1 กวาดช่วงกว้าง, คำนวณจากค่ามาตรฐาน SCS15 `180°/1024 raw` และแสดง raw จริงในตาราง
- Main CPG Output แสดงคำสั่ง J1–J4 หน่วยองศาจาก main CPG
- มุม Servo เป็นเพียงค่าประมาณจนกว่าจะวัด linkage calibration จริง ค่า raw ในตารางเป็นข้อมูลหลัก
- ทั้งสองกราฟเก็บช่วงเวลาประมาณ 10 วินาทีล่าสุด

## เลือกกล้อง USB

- กด `SCAN CAMERAS` เพื่อทดลองกล้อง index `0–9` โปรแกรมจะแสดงเฉพาะตัวที่เปิดและอ่านภาพได้
- เลือก `Camera <index> (<backend>)` จาก dropdown แล้วจึงกด `START CAMERA`
- `--camera-index` เป็นกล้องที่ต้องการเลือกไว้ก่อน ถ้าพบระหว่าง Scan; ถ้าไม่พบจะเลือกกล้องตัวแรก
- ระหว่างกล้องทำงานหรือกำลังหยุด ปุ่ม Scan และ dropdown จะถูกล็อกเพื่อไม่ให้เกิด camera owner ซ้อนกัน
- โหมด Mock จะแสดง `Mock Camera` และไม่แตะกล้องจริง

## ทดสอบ Mock บน Windows ก่อน

```powershell
cd D:\Emotion_Robotic_Lamp_Windows
python -m tools.robot_cpg_gui.app --mock
```

หรือดับเบิลคลิก `START_CPG_GUI_MOCK.bat` กล้องจำลองจะเปลี่ยน Neutral/Happy/Sad/Confused ทุก 4 วินาที และ Servo เป็น in-memory mock

Smoke test ที่ไม่เข้า mainloop:

```powershell
python -m tools.robot_cpg_gui.app --mock --smoke-test
```

## เตรียม Raspberry Pi

ยืนยันก่อนว่าใช้ OS 64-bit แล้วติดตั้ง Tk/X11/venv ตามชื่อแพ็กเกจของ OS ที่ใช้งาน ตัวอย่าง Raspberry Pi OS/Debian:

```bash
sudo apt update
sudo apt install python3-venv python3-tk xauth
cd ~/Emotion_Robotic_Lamp_Windows
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r tools/robot_cpg_gui/requirements-pi.txt
```

เพิ่มสิทธิ์ serial และ video แล้ว logout/login หนึ่งรอบ:

```bash
sudo usermod -aG dialout,video "$USER"
```

ตรวจอุปกรณ์:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* /dev/video*
```

## เปิดผ่าน ssh -X

คอมต้องมี X server เช่น MobaXterm, VcXsrv หรือ Xming และ Pi ต้องเปิด X11 forwarding จากนั้น:

```bash
ssh -X USER@PI_IP
cd ~/Emotion_Robotic_Lamp_Windows
source .venv/bin/activate
bash tools/robot_cpg_gui/start_cpg_gui.sh --port /dev/ttyUSB0 --camera-index 0
```

ถ้า CH343 ถูกตรวจพบอัตโนมัติ สามารถละ `--port` ได้ โปรแกรมจะลอง `/dev/ttyUSB*` และ `/dev/ttyACM*` ถ้าตรวจไม่พบโปรแกรมจะรายงานให้ระบุ `--port` เองและจะไม่เดาพอร์ตให้

## ลำดับใช้งานฮาร์ดแวร์จริง

1. ปิด Feetech FD และใช้มือรองแขน/หัวไว้
2. จ่ายไฟ Servo และเปิดโปรแกรม
3. กด `SCAN CAMERAS` เลือก USB Camera แล้วกด `START CAMERA` รอให้ preview ขึ้น
4. กด `CONNECT`; โปรแกรมต้อง ping IDs 0–3 และตำแหน่งต้องอยู่ใน safe limits
5. ถ้าพบ Torque เดิมเปิดอยู่ ให้กด Emergency ก่อน แล้ว Connect ใหม่
6. กด `ENABLE MOTION`; runtime จะ Sync ตำแหน่งปัจจุบันก่อนเปิด Torque
7. กด `GO CENTER`
8. กด `START CPG`; คำสั่ง `60–120°` จะถูกแปลงเป็นมุมข้อต่อ direct drive 1:1 ภายในขอบเขต ±171 raw
9. ใช้ `STOP → CENTER` สำหรับหยุดปกติ
10. ใช้ `EMERGENCY TORQUE OFF` เมื่อมีเสียงผิดปกติ ชนโครง สายตึง หรือการเคลื่อนที่ไม่ตรงคาด

## คำสั่งอื่น

```bash
# ระบุโมเดล MediaPipe เอง
bash tools/robot_cpg_gui/start_cpg_gui.sh \
  --model-path "$PWD/face_vision(maincpg)/models/face_landmarker.task"

# ตรวจ GUI บน Pi โดยไม่แตะ hardware
bash tools/robot_cpg_gui/start_cpg_gui.sh --mock
```

## สิ่งที่ยังไม่ถือว่าผ่าน Hardware Acceptance

- USB camera + MediaPipe บน Pi OS เครื่องจริง
- Sync Write ต่อเนื่อง 20 Hz กับ Servo จริง
- การขยับทิศลบของทุก joint จาก Center
- power supply ขณะ Servo ทั้ง 4 ตัวขยับพร้อมกัน
- ตรวจมุมจริงด้วยโปรแทรกเตอร์เพื่อยืนยัน nominal SCS15 `180°/1024 raw` และบันทึก correction ราย joint ถ้าจำเป็น
- พฤติกรรมเมื่อถอด USB, ไฟตก และ X11/SSH หลุด

## อัปเดตสมการ CPG (12 พฤศจิกายน 2569)

แก้การหมุน SO(2) และการแปลงออสซิลเลเตอร์เป็นมุม J1-J4
ตามไฟล์สมการล่าสุดที่ผู้ใช้แนบมา โดย **J1/J3 ใช้ o1 (J3 เครื่องหมายลบ)**
และ **J2/J4 ใช้ o2**; รวมการค่อย ๆ Fade การแกว่ง J4,
Hormone Euler และระยะเวลา pose ของแต่ละ mode.
ดูรายละเอียดใน `CPG_EQUATIONS_UPDATE_2026-11-12_TH.md`.

**สำคัญ:** ยังใช้ค่าตั้งมอเตอร์และโปรไฟล์ที่อยู่ใน ZIP ล่าสุดของผู้ใช้เดิม
โดยเฉพาะ Happy J4 ที่ 5 วินาที = 120° ไม่ได้เปลี่ยนเป็น 105° ตามตัวอย่างสมการ

## เลือก Personality ด้วยแป้น Numpad (เวอร์ชันนี้)

ตั้งต้นจากไฟล์ `robot_cpg_gui(3).zip` ของผู้ใช้: ไม่เปลี่ยนค่า `robot_config.json`, `cpg_profile.json`, สมการ SO(2), อัตรารัน, Speed 150 หรือค่า Center / ทิศทาง Servo

| Numpad | Personality | ผล |
|---|---|---|
| `1` | Happy | ใช้ Pose, Gain, Phi, Hormone ที่ตั้งไว้ของ Happy |
| `2` | Sad | ใช้ค่าของ Sad และ Head Fade ตามโปรไฟล์เดิม |
| `3` | Confused | ใช้ค่าของ Confused |
| `4` | Neutral | ใช้ค่าของ Neutral |
| `0` | AUTO Camera | ยกเลิก Manual แล้วกลับไปอ่านอารมณ์จากกล้อง |

ปุ่มใช้ได้เฉพาะเมื่อหน้าต่างโปรแกรมมีโฟกัส (ไม่ใช่ปุ่มลัดทั่วระบบ); รองรับ NumLock เปิดหรือปิด ในหน้าต่างเดียวกันยังมีปุ่มกดสำหรับเลือกทั้งหมดโดยไม่ต้องมีแป้น Numpad หากกำลังพิมพ์ค่าในช่อง Settings จะไม่แย่งคีย์ไปเปลี่ยนอารมณ์

**วิธีทดสอบ:** เปิดโปรแกรม เลือก `1–4` ก่อน/หลัง Start CPG ได้ ถ้าไม่ใช้กล้องสามารถกด Numpad เลือกโหมด Manual แล้วทำ `CONNECT` → `ENABLE MOTION` → `GO CENTER` → รอ `ARMED` และ Center สำเร็จ → `START CPG` ได้ โดยไม่ต้อง Start Camera ถ้ากำลัง RUNNING สามารถเปลี่ยน Personality ได้ทันที แต่ระบบยังคง Hormone interpolation, max slew และขอบเขตมุมเดิม

กด `0` เพื่อสลับกลับ AUTO; หากกล้องหยุดอยู่หรือข้อมูลกล้องหมดอายุ ระบบจะเลือก Neutral ไม่บังคับการเคลื่อนไหวจากค่ากล้องค้าง ในสถานะ FAULT, EMERGENCY และ CLOSED จะไม่รับการเลือกใหม่ ปุ่ม Numpad **ไม่** สั่ง CONNECT, ARM, CENTER หรือ START Servo โดยอัตโนมัติ และไม่ใช่ทางลัด Emergency (ใช้ปุ่ม Emergency เดิม)


## เพิ่มในเวอร์ชันนี้: Face Search / Tracking / GPIO LED

อ่านไฟล์ `CAMERA_FACE_SEARCH_GPIO_TH.md` สำหรับลำดับการทำงานหลัง START CAMERA, มุม Standby, การสแกน, การกลับเข้า CPG, การปรับทิศทางการติดตาม และการเตรียม GPIO ของ Raspberry Pi.

**การตั้งค่า CPG และ Servo จาก ZIP ที่ยืนยันแล้วไม่ถูกเปลี่ยน** และ GPIO ปิดไว้เป็นค่าเริ่มต้น

## เพิ่มเติม J1 กวาดกว้าง และกลับทิศ J2 (12 พ.ย. 2569)

- J2 กลับขั้วมุมจาก `direction=+1` เป็น `-1` ใน `robot_config.json`
- Face Search J1 ใช้ขอบเขต safe_min/safe_max เฉพาะ AUTO Camera; CPG / Manual ปกติยังคงขอบเขต ±171 raw
- กำหนด `scan_use_full_safe_range=true`, `scan_target_span_deg=180.0` ใน `face_search_config.json`
- **J1 รุ่นตั้งค่าปัจจุบันทำได้ราว 153° (raw 0..870) ไม่ใช่ 180° เต็ม**; ดู `J1_WIDE_SCAN_J2_REVERSED_TH.md` ก่อนเพิ่ม safe range โดยยังไม่มีการยืนยันฮาร์ดแวร์จริง
