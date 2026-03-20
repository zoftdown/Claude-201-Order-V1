# CLAUDE.md — Order System (ร้านพิมพ์เสื้อ)

## Project Overview
ระบบจัดการใบออร์เดอร์สำหรับร้านพิมพ์เสื้อ
- Web app สำหรับใช้ในร้าน (staff only)
- Django + PostgreSQL
- Deploy บน **Hostinger VPS KVM1** (฿249/เดือน แผน 12 เดือน)
- Server location: มาเลเซีย (ping ~41ms จากไทย)
- OS: Ubuntu

---

## Tech Stack
- **Backend:** Python 3.11+, Django 4.x
- **Database:** PostgreSQL
- **Frontend:** Django Templates + Tailwind CSS (หรือ Bootstrap)
- **File storage:** Local media folder (รูปภาพ)
- **Export:** PDF (ใบออร์เดอร์พิมพ์ได้)

---

## Data Models

### Order (ใบออร์เดอร์)
| Field | Type | หมายเหตุ |
|---|---|---|
| order_number | CharField | รันอัตโนมัติ เช่น 6903-1, 6903-45 (ขึ้นใหม่ทุกเดือน) |
| created_date | DateField | วันที่สร้าง |
| print_date | DateField | วันที่พิมพ์เสื้อ (nullable) |
| source | CharField | choices: เพจเสื้อเนินสูง / เพจเสื้อคนงาน / เฮีย&เจ๊ / หน้าร้าน / เพจปักผ้า / LINE OA / เพจร้าน Yada / เพจเสื้อทุเรียน |
| customer_name | CharField | ชื่อลูกค้า |
| customer_link | CharField | Facebook URL หรือเบอร์โทร |
| shirt_name | CharField | ชื่องาน/ชื่อเสื้อ เช่น "แผงกั๊วเป้า" |
| fabric_spec | TextField | spec ผ้า (แสดงเฉพาะ source=เพจเสื้อคนงาน) เช่น "ผ้า 120 แกรม สีแดง มี 4 เหลี่ยมล้อมกรอบ" |
| special_note | TextField | คำสั่งพิเศษ (แสดงสีแดง) |
| total_price | DecimalField | ยอดรวม |
| deposit | DecimalField | มัดจำ |
| delivery_method | CharField | choices: รับเอง / ส่ง |
| status | CharField | choices: รอดำเนินการ / กำลังผลิต / เสร็จแล้ว / ส่งแล้ว |

### OrderItem (รายการในใบออร์เดอร์)
1 Order มีได้หลาย Item (ไม่จำกัด)

| Field | Type | หมายเหตุ |
|---|---|---|
| order | ForeignKey | → Order |
| design_image | ImageField | รูปดีไซน์ |
| shirt_type | CharField | choices: แขนสั้น / แขนยาว / โปโล / อื่นๆ |
| color | CharField | สีเสื้อ (กรอกอิสระ) เช่น "เขียว", "แดง" |
| qty_xs | IntegerField | จำนวนไซส์ XS (default 0) |
| qty_ss | IntegerField | SS |
| qty_s | IntegerField | S |
| qty_m | IntegerField | M |
| qty_l | IntegerField | L |
| qty_xl | IntegerField | XL |
| qty_2xl | IntegerField | 2XL |
| qty_3xl | IntegerField | 3XL |
| qty_4xl | IntegerField | 4XL |
| qty_5xl | IntegerField | 5XL |
| qty_6xl | IntegerField | 6XL |
| qty_custom_label | CharField | label ไซส์พิเศษ (กรอกเอง) |
| qty_custom | IntegerField | จำนวนไซส์พิเศษ |

---

## Features

### ✅ Phase 1 — Core (ทำก่อน)
- [ ] สร้าง/แก้ไขใบออร์เดอร์
- [ ] รันเลข order อัตโนมัติ
- [ ] เพิ่ม/ลบ OrderItem แบบ dynamic (ไม่จำกัดจำนวน)
- [ ] แนบรูปดีไซน์แต่ละ item
- [ ] แสดง fabric_spec เฉพาะเมื่อ source = เพจเสื้อคนงาน
- [ ] ค้นหา order (ค้นด้วยชื่อลูกค้า, ชื่อเสื้อ, เลข order)
- [ ] Export PDF ใบออร์เดอร์ (พิมพ์ได้)
- [ ] หน้า list order ทั้งหมด + filter status

### 🔜 Phase 2 — เพิ่มเติม
- [ ] สถิติ/รายงาน (ยอดขาย, จำนวนเสื้อ)
- [ ] วันส่งเย็บ / วันนัดลูกค้า
- [ ] ประวัติการแก้ไข order

---

## UI/UX Notes
- ใบออร์เดอร์แสดงแบบ card ต่อ item — รูป + ประเภท + สี + ตารางไซส์
- คำสั่งพิเศษแสดงด้วยสีแดง
- PDF export ต้องมี: เลข order, วันที่, ชื่อลูกค้า, รูปดีไซน์, จำนวนแต่ละไซส์, ยอดเงิน
- mobile-friendly (staff ใช้มือถือในร้านได้)

---

## Order Number Format
`{ปี พ.ศ. 2 หลัก}{เดือน 2 หลัก}-{running 1-999}` เช่น `6903-1`, `6903-45`, `6903-999`
- ขึ้น running ใหม่ทุกเดือน (เริ่ม 1 ใหม่)
- ไม่ pad ศูนย์หน้าเลข running

---

## Decisions Log
- ใช้ Django เพราะ migration ง่าย เพิ่ม field ได้โดยไม่ยุ่งยาก
- staff only ไม่มี customer login (phase 1)
- รูปเก็บ local media ก่อน ไม่ใช้ cloud storage
- ไม่ใช้ DRF (Django REST Framework) — ใช้ Django Templates แบบ traditional เพื่อความเรียบง่าย

---

## การเพิ่ม Field ใหม่
```bash
# 1. แก้ models.py เพิ่ม field
# 2. รัน:
python manage.py makemigrations
python manage.py migrate
# เสร็จ — database อัปเดทอัตโนมัติ
```
