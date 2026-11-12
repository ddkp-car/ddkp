# LED 3 สายบน Raspberry Pi 5 — GPIO12 / Physical Pin 32

## วงจร (สำหรับ LED 5V WS2812B / NeoPixel เท่านั้น)

- DIN/Signal: Raspberry Pi 5 **GPIO12 (BCM12), ขาจริง 32**, ผ่านตัวแปลงระดับ logic เช่น **74AHCT125** (3.3V เป็น 5V) และ R 330–470Ω ทางสายสัญญาณ
- VCC/+: 5V จากแหล่งจ่ายที่เหมาะกับจำนวน LED; ถ้าเป็นหลายเม็ดให้ใช้แหล่งจ่ายแยก
- GND/−: GND และต่อร่วมกับ GND ของ Raspberry Pi (เช่น ขาจริง 6)
- ห้ามต่อสาย Data เข้าขา 5V หรือเอา 5V ไปต่อ GPIO12 โดยตรง
- **LED แบบ 3 สายไม่ได้แปลว่า WS2812 เสมอไป**: ต้องอ่านรุ่น/แรงดันก่อนเปิดใช้งาน

## ไลบรารีและอุปกรณ์บน Pi 5

ใช้ RP1 PIO ผ่าน `Adafruit-Blinka-Raspberry-Pi5-Neopixel` ไม่ใช่ SPI/GPIO10 หรือ software PWM บน GPIO12

```bash
ls -l /dev/pio0
python -m pip install Adafruit-Blinka-Raspberry-Pi5-Neopixel adafruit-circuitpython-pixelbuf
```

หากไม่มี `/dev/pio0` ให้ตรวจเวอร์ชัน Raspberry Pi OS, kernel และ firmware ตามคู่มือของ Adafruit ก่อน ไม่ต้องเปิด LED output หรือใช้อัปเดต kernel ทดสอบโดยไม่ได้ตรวจ

หาก `/dev/pio0` มีอยู่แต่ permission denied ให้ตรวจกลุ่ม `gpio` และสิทธิ์อุปกรณ์ตามคู่มือ (โดยไม่ต้องรันโปรแกรมควบคุม Servo ด้วย root)

## ตั้งค่าใน GUI

เปิดแท็บ `GPIO / LED (Pi)`:

- `LED Type = ws2812_pio`
- Data = GPIO12 / physical pin 32 (คงที่)
- `Number of addressable LED pixels` = จำนวนเม็ดจริง (ค่าเริ่มต้น 1 เพื่อไม่เดาจำนวน)
- `Pixel order = GRB` (ถ้าสีผิดลอง RGB)
- `Max brightness = 0.25` หรือลดเหลือ 0.10 ตอนเริ่ม
- ติ๊ก Enable GPIO output **หลังยืนยันรุ่น LED, จ่ายไฟและวงจรแล้ว** จากนั้น Save และรีสตาร์ตโปรแกรม

หากต้องการทดสอบไฟโดยไม่สั่งมอเตอร์/กล้อง ให้รันจาก parent ของ `tools`:

```bash
python -m tools.robot_cpg_gui.led_test_pi --pixels 8 --brightness 0.10
```

เปลี่ยน `8` ให้ตรงจำนวนจริงก่อนสั่งทดสอบ โปรแกรมจะทดสอบแดง เขียว น้ำเงิน ขาว และ Rainbow แล้วดับไฟเมื่อจบ

### หมายเหตุ

- ไม่ได้เปลี่ยนการทำงาน Servo, CPG, Face Tracking, Numpad หรือ Stage 2
- ขณะ CPG ทำงาน โค้ดใช้ `snapshot.light` เดิม สถานะ Stop/Emergency จะสั่ง LED เป็น OFF
- โหมด SPI (GPIO10/ขาจริง19) คงไว้สำหรับทางเลือกอื่น แต่ **ค่าเริ่มต้นและ GUI ใช้ PIO GPIO12** ตามการต่อสายปัจจุบัน
- GPIO output ค่าเริ่มต้นเป็น `enabled:false` เพื่อไม่เปิดโดยไม่ได้ยืนยันฮาร์ดแวร์

อ้างอิง: https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/using-neopixels-on-the-pi-5
