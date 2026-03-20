# ระบบออร์เดอร์ร้านพิมพ์เสื้อ

ระบบจัดการใบออร์เดอร์สำหรับร้านพิมพ์เสื้อ — Django + SQLite

---

## Setup บนเครื่องใหม่

### 1. ติดตั้ง Python

ดาวน์โหลด Python 3.11+ จาก https://www.python.org/downloads/

ตอนติดตั้ง **ติ๊กถูก "Add Python to PATH"**

ตรวจสอบ:
```
python --version
```

### 2. ดาวน์โหลดโปรเจค

คัดลอกโฟลเดอร์โปรเจคทั้งหมดไปยังเครื่องใหม่ เช่น `C:\OrderSystem\`

### 3. ติดตั้ง packages

เปิด Command Prompt (cmd) แล้ว cd เข้าโฟลเดอร์โปรเจค:

```
cd C:\OrderSystem
pip install -r requirements.txt
```

### 4. สร้างฐานข้อมูล

```
python manage.py migrate
```

### 5. (ทำครั้งเดียว) สร้าง Admin user

```
python manage.py createsuperuser
```
กรอก username, email (ข้ามได้), password

### 6. รัน server

```
python manage.py runserver 0.0.0.0:8000
```

เปิดเบราว์เซอร์ไปที่: **http://127.0.0.1:8000/**

ถ้าจะเข้าจากมือถือ/เครื่องอื่นในร้าน ให้ใช้ IP ของเครื่อง เช่น **http://192.168.1.6:8000/**

### 7. แก้ ALLOWED_HOSTS (ถ้าเปลี่ยน IP)

แก้ไฟล์ `config/settings.py` บรรทัด:

```python
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '192.168.1.6']
```

เปลี่ยน `192.168.1.6` เป็น IP จริงของเครื่องที่ร้าน

ดู IP ได้โดยพิมพ์ `ipconfig` ใน cmd แล้วดูที่ IPv4 Address

---

## การใช้งาน

| URL | หน้า |
|---|---|
| `/` | รายการออร์เดอร์ทั้งหมด |
| `/create/` | สร้างออร์เดอร์ใหม่ |
| `/1/` | ดูรายละเอียดออร์เดอร์ |
| `/1/edit/` | แก้ไขออร์เดอร์ |
| `/admin/` | หน้า Admin |

---

## โครงสร้างไฟล์

```
OrderSystem/
├── config/          ← ตั้งค่า Django
│   ├── settings.py
│   └── urls.py
├── orders/          ← แอพหลัก
│   ├── models.py    ← โมเดล Order, OrderItem
│   ├── views.py
│   ├── forms.py
│   └── urls.py
├── templates/       ← หน้าเว็บ
├── media/           ← เก็บรูปดีไซน์ (สร้างอัตโนมัติ)
├── db.sqlite3       ← ฐานข้อมูล (สร้างหลัง migrate)
├── manage.py
└── requirements.txt
```

---

## หมายเหตุ

- **ไม่ต้องลง PostgreSQL** — ใช้ SQLite ที่มากับ Python ได้เลย
- **ไฟล์ db.sqlite3** คือฐานข้อมูล — ถ้าจะย้ายข้อมูลก็แค่ copy ไฟล์นี้ไปด้วย
- **โฟลเดอร์ media/** เก็บรูปดีไซน์ — ถ้าย้ายเครื่องก็ copy ไปด้วย
