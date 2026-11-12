# การรวมโปรเจกต์ CPG เวอร์ชัน Pi 5 — GPIO10 (ขาจริง 19)

- ฐานโค้ด: `robot_cpg_gui(4).zip` ที่ผู้ใช้แก้ล่าสุด นำไปแทน `tools/robot_cpg_gui` ในโปรเจกต์เต็ม
- คง `runtime.py` เวอร์ชันล่าสุดที่อนุญาตคลาดเคลื่อน Center `POSITION_TOLERANCE = 20` raw, ตรวจมุมจริงคงที่ก่อนผ่าน, ไม่ข้ามเงื่อนไข Torque หรือ Safe Range
- คง `robot_config.json`, `cpg_profile.json`, `face_search_config.json`, `emotion_stage_config.json` เดิมทุกไบต์
- LED เปลี่ยนเฉพาะ `led_gpio.json` เป็น `mode: ws2812_spi`, `data_bcm: 10` และคง `enabled: true` จากไฟล์ล่าสุดของผู้ใช้
- ใช้ SPI0 MOSI BCM10 = ขาจริง 19 (ไม่ใช่ขาจริงหมายเลข 10); GND ร่วม และไฟ 5V ให้ LED **เฉพาะเมื่อยืนยันชนิด 5V WS2812/NeoPixel แล้ว**
- ใช้ตัวแปลงระดับสัญญาณ 3.3V -> 5V ที่ขา DIN ของ LED เช่น 74AHCT125; หลีกเลี่ยงจ่ายกำลัง LED หลายเม็ดจาก Pi; ติดตั้งตัวต้านทาน 330–470 โอห์มที่ DATA
- โหมด SPI ไม่ต้องใช้ `/dev/pio0` แต่ต้องมี `/dev/spidev0.0`; ตรวจบน Pi ด้วย `ls -l /dev/spidev0.0`
- บน Ubuntu 24.04 ของ Pi 5 ถ้ายังไม่เปิด SPI0 ให้เปิด `dtparam=spi=on` ใน `/boot/firmware/config.txt` แล้วรีบูต (ตรวจไฟล์ก่อนเพิ่มซ้ำ)
- ติดตั้ง dependencies: `python3 -m pip install -r tools/robot_cpg_gui/requirements-pi.txt` ใน venv และใช้สิทธิ์ `spi` ตามที่เครื่องกำหนด
- ทดสอบไฟแยกโดยไม่ขยับมอเตอร์: `python3 -m tools.robot_cpg_gui.led_test_pi --pixels 1 --brightness 0.10`
- เปิดมอเตอร์จริงเฉพาะเมื่อผ่านการตรวจพอร์ต/เซอร์โวและใช้ GUI ที่มี X11 Display: `python3 -m tools.robot_cpg_gui.app --port /dev/ttyACM0 --camera-index 0`
- ไฟล์ `requirements-led-pio-optional.txt` สำหรับโหมด PIO GPIO12 เดิมเท่านั้น ไม่ใช่โหมดที่เลือกในเวอร์ชันนี้
- ยังไม่ได้ยืนยันการทำงานบนไฟ LED/Servo จริงของผู้ใช้
