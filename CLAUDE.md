# CLAUDE.md — Order System (ร้านพิมพ์เสื้อ)

## Project Overview
ระบบจัดการใบออร์เดอร์สำหรับร้านพิมพ์เสื้อ
- Web app สำหรับใช้ในร้าน (staff only)
- Django + PostgreSQL
- **URL:** https://dr89.cloud/order/
- Deploy บน **Hostinger VPS KVM1** (฿249/เดือน แผน 12 เดือน)
- Server location: มาเลเซีย (ping ~41ms จากไทย)
- OS: Ubuntu

---

## Tech Stack
- **Backend:** Python 3.11+, Django 4.x
- **Database:** PostgreSQL (dev fallback: SQLite)
- **Frontend:** Django Templates + Tailwind CSS (หรือ Bootstrap)
- **WSGI:** Gunicorn (port 8100)
- **Static files:** WhiteNoise (CompressedManifestStaticFilesStorage)
- **Reverse proxy:** Nginx
- **File storage:** Local media folder (รูปภาพ)
- **Authentication:** Django built-in auth + Groups (admin/staff)
- **Export:** PDF (ใบออร์เดอร์พิมพ์ได้)

---

## Project Structure
```
config/           # Django project settings, urls, wsgi
orders/           # Main app (models, views, forms, urls)
templates/        # HTML templates
  registration/   # login.html (Django auth)
  orders/         # order_list, order_form, order_detail, order_print
static/           # Source static files
staticfiles/      # collectstatic output (gitignored)
media/            # Uploaded images (gitignored)
  designs/YYYY/MM/
deploy/           # Production deployment configs
  nginx.conf
  gunicorn.conf.py
  order.service
  setup.sh
.env.example
```

---

## Authentication & Permissions

### Login
- ใช้ Django built-in authentication (ยกเลิก HTTP Basic Auth ของ nginx)
- หน้า login: `/order/login/` (ใช้ Django's `LoginView`)
- ทุก view ครอบด้วย `@login_required` (หรือ `LoginRequiredMixin`)
- มีปุ่ม logout ที่มุมขวาบน พร้อมชื่อ user ที่ login อยู่
- Redirect หลัง login → `/order/` (order list)

### User Roles (Django Groups)
| Group | สิทธิ์ |
|---|---|
| **admin** | สร้าง + แก้ไข + **ลบ** order + จัดการ user ผ่าน Django admin |
| **staff** | สร้าง + แก้ไข order (**ลบไม่ได้**) |

### Implementation
- ใช้ Django Groups ชื่อ `admin` และ `staff`
- Superuser ถือว่าเป็น admin โดยปริยาย
- Template: ซ่อนปุ่ม "ลบ" ถ้า user ไม่ใช่ admin — `{% if user.groups.all|has_group:"admin" or user.is_superuser %}`
- View: ใน `delete_order` view ให้เช็ค permission ด้วย — ถ้าไม่มีสิทธิ์ให้ return 403
- เพิ่ม management command หรือใช้ Django admin จัดการ user + group

### Legacy Orders (ออร์เดอร์เก่าก่อน schema ใหม่)
- **ใช้ URL เดิม** `/order/<id>/` — ไม่แยก path
- เพิ่ม field `Order.schema_version` (int, default=2 สำหรับ order ใหม่, =1 สำหรับ order เก่า)
- Migration data script: set `schema_version=1` ให้ทุก order ที่มีอยู่ตอนนี้
- Order ที่ `schema_version=1`:
  - Detail view / Print view: แสดงได้ปกติ (backward compatible)
  - **ซ่อนปุ่ม "แก้ไข"** และ **ซ่อนปุ่ม "ลบ"** ใน detail/list view
  - Form view: redirect กลับ detail พร้อมข้อความ "ออร์เดอร์นี้เป็นข้อมูลเก่า ไม่สามารถแก้ไขได้"
- Order ที่ `schema_version=2` (ใหม่): ใช้ structure ใหม่ แก้ไขได้ปกติ

---

## Data Models

### Order (ใบออร์เดอร์) — *ไม่เปลี่ยน field เดิม*
| Field | Type | หมายเหตุ |
|---|---|---|
| order_number | CharField | รันอัตโนมัติ เช่น 6903-1, 6903-45 |
| created_date | DateField | |
| print_date | DateField | nullable |
| source | CharField | choices: เพจเสื้อเนินสูง / เพจเสื้อคนงาน / เฮีย&เจ๊ / หน้าร้าน / เพจปักผ้า / LINE OA / เพจร้าน Yada / เพจเสื้อทุเรียน / Shopee / Tiktok |
| customer_name | CharField | |
| customer_link | CharField | |
| shirt_name | CharField | |
| fabric_spec | TextField | แสดงเฉพาะ source=เพจเสื้อคนงาน |
| special_note | TextField | แสดงสีแดง |
| total_price | DecimalField | |
| deposit | DecimalField | |
| delivery_method | CharField | รับเอง / ส่ง |
| shipping_address | TextField | |
| status | CharField | รอดำเนินการ / กำลังผลิต / เสร็จแล้ว / ส่งแล้ว |
| **schema_version** | **IntegerField** | **ใหม่: default=2, legacy=1** |
| **created_by** | **FK → User** | **ใหม่: nullable (legacy order ไม่มี)** |

### OrderItem (รายการเสื้อ) — *ปรับ structure ใหม่*
> **เปลี่ยนบทบาท**: จากเดิม 1 item = 1 รายการเสื้อครบ (รูป+คอ+แขน+สี+ไซส์)
> → ใหม่: 1 item = **1 รูปดีไซน์** (พร้อมมีหลาย variant ข้างใน)

| Field | Type | หมายเหตุ |
|---|---|---|
| order | FK → Order | |
| design_image | ImageField | รูปดีไซน์ (optional — บางรายการยังไม่มี mockup) |
| order_index | IntegerField | ลำดับแสดง (1, 2, 3, ...) |
| note | CharField(blank) | หมายเหตุของรูปนี้ เช่น "ยังไม่มี mockup" |

### ShirtVariant (แบบเสื้อในรูปเดียวกัน) — **ใหม่**
> 1 OrderItem มีได้หลาย ShirtVariant
> ตัวอย่าง: รูปเดียว → variant 1 แขนสั้น สีดำ M=1 XL=1, variant 2 แขนยาว สีดำ M=2 L=5 XL=3

| Field | Type | หมายเหตุ |
|---|---|---|
| item | FK → OrderItem | |
| sleeve_type | CharField | แขนสั้น / แขนยาว / แขนกุด |
| collar_type | CharField | คอกลม / คอวี / โปโล / คอปกวี / คอกีฬา / อื่นๆ |
| color | CharField | กรอกอิสระ |
| sizes | JSONField | `[{"label": "S", "qty": 5}, {"label": "M", "qty": 10}]` |
| variant_index | IntegerField | ลำดับแสดง |

### CustomItem (รายการอื่นๆ ที่ไม่ใช่เสื้อ) — **ใหม่**
> แยก section จากเสื้อ เช่น กระเป๋าผ้า, ของพรีเมี่ยม

| Field | Type | หมายเหตุ |
|---|---|---|
| order | FK → Order | |
| image | ImageField | optional |
| name | CharField | ชื่อสินค้า เช่น "กระเป๋าผ้าลายโลโก้" |
| quantity | IntegerField | จำนวน |
| note | TextField(blank) | หมายเหตุ |
| order_index | IntegerField | ลำดับแสดง |

---

## Features

### ✅ Phase 1 — Core (เสร็จแล้ว)
- [x] สร้าง/แก้ไขใบออร์เดอร์
- [x] รันเลข order อัตโนมัติ
- [x] เพิ่ม/ลบ OrderItem แบบ dynamic
- [x] แนบรูปดีไซน์แต่ละ item
- [x] แสดง fabric_spec เฉพาะเมื่อ source = เพจเสื้อคนงาน
- [x] ค้นหา order
- [x] Print view ใบออร์เดอร์
- [x] หน้า list order + filter status
- [x] Production deployment config

### 🛠️ Phase 1.5 — กำลังทำ (Iteration นี้)

#### 1. Authentication ด้วย Django
- [ ] ติดตั้ง `django.contrib.auth` URLs (`/order/login/`, `/order/logout/`)
- [ ] สร้าง template `registration/login.html`
- [ ] ครอบทุก view ด้วย `login_required`
- [ ] สร้าง Groups: `admin`, `staff` (via migration หรือ management command)
- [ ] แสดงชื่อ user + ปุ่ม logout ใน header ทุกหน้า
- [ ] ซ่อนปุ่มลบ order จาก staff
- [ ] ยกเลิก HTTP Basic Auth ใน nginx config (ถ้ามี)

#### 2. Print view — รูปใหญ่ + layout ใหม่ (ข้อ 2)
เปลี่ยน layout จากแบบ side-by-side (รูปซ้าย / รายละเอียดขวา) เป็น **stacked (รูปบน / รายละเอียดล่าง)**

Layout ใหม่ของแต่ละ OrderItem:
```
┌─────────────────────────────────────┐
│                                     │
│         [รูปดีไซน์ใหญ่]              │
│         (max-width เต็ม card,        │
│          aspect-ratio คงที่)         │
│                                     │
├─────────────────────────────────────┤
│ Variant 1:                          │
│   คอกลมแขนสั้น สีดำ                  │
│   M=3  XL=6  3XL=3                  │
│   รวม 12 ตัว                        │
│ ─────────────────────────────────   │
│ Variant 2:                          │
│   คอกลมแขนยาว สีน้ำเงิน              │
│   M=2  L=5  XL=3                    │
│   รวม 10 ตัว                        │
└─────────────────────────────────────┘
```

- [ ] รูปใช้พื้นที่ด้านบนเต็มความกว้าง card (รูปเดิมเล็กไป ฝ่ายผลิตดูไม่ชัด)
- [ ] รายละเอียด variant แสดงด้านล่าง แยกด้วยเส้นคั่น
- [ ] แสดง "รวม X ตัว" ต่อ variant + "รวมทั้งหมด Y ตัว" ต่อ item
- [ ] ถ้าไม่มีรูป → แสดง placeholder พร้อมข้อความ "ยังไม่มี mockup"
- [ ] Mobile-responsive: รูปใหญ่ขึ้นเมื่อเปิดบนมือถือ

#### 3 + 4. 1 รูป → หลาย variant (ShirtVariant)
- [ ] สร้าง model `ShirtVariant` ใหม่ (ดู Data Models)
- [ ] Migration: สร้างตาราง `shirtvariant`
- [ ] Data migration script:
  - สำหรับ order ที่ `schema_version=1` (เก่า): ไม่ต้องย้ายข้อมูล, field เก่าใน OrderItem (sleeve_type, collar_type, color, sizes) ยังคงอยู่เพื่อ display
  - สำหรับ order ใหม่: field เก่าใน OrderItem จะไม่ถูกใช้งาน (อาจ null หรือ leave as-is)
- [ ] Form: เปลี่ยน OrderItem form ให้มี nested formset ของ ShirtVariant
- [ ] UI form ใน order_form.html:
  ```
  รายการที่ 1: [รูปดีไซน์]
    ├─ แบบที่ 1: [คอ][แขน][สี][ไซส์...]
    ├─ แบบที่ 2: [คอ][แขน][สี][ไซส์...]
    └─ [+ เพิ่มแบบเสื้อในรูปนี้]
  [+ เพิ่มรายการใหม่ (รูปใหม่)]
  ```
- [ ] JavaScript: dynamic add/remove variant ภายใน item (คล้ายกับที่เพิ่ม item ได้อยู่แล้ว)
- [ ] validate: 1 item ต้องมีอย่างน้อย 1 variant (ถ้าเป็นเสื้อ)

#### 5. Custom items (รายการอื่นๆ)
- [ ] สร้าง model `CustomItem` (ดู Data Models)
- [ ] Form: เพิ่ม section "รายการอื่นๆ (ไม่ใช่เสื้อ)" ในหน้าสร้าง/แก้ไข
- [ ] แต่ละ custom item มี: รูป (optional) + ชื่อ + จำนวน + หมายเหตุ
- [ ] Detail/Print view: แสดงเป็น section แยก หัวข้อ "เสื้อ" กับ "อื่นๆ"
- [ ] Custom item ไม่นับรวมใน "รวมทั้งหมด X ตัว" ของเสื้อ (แสดงแยก "อื่นๆ: X ชิ้น")

### 🔜 Phase 2 — เพิ่มเติม (ยังไม่ทำ)
- [ ] Export PDF ใบออร์เดอร์
- [ ] สถิติ/รายงาน (ยอดขาย, จำนวนเสื้อ)
- [ ] วันส่งเย็บ / วันนัดลูกค้า
- [ ] ประวัติการแก้ไข order

---

## UI/UX Notes

### Order Form (create/edit)
- Header: ชื่อ user + ปุ่ม logout (มุมขวาบน)
- Section: ข้อมูลลูกค้า (เหมือนเดิม)
- **Section: "เสื้อ"** — รายการ OrderItem + ShirtVariant ซ้อนกัน
  - ปุ่ม "+ เพิ่มแบบเสื้อในรูปนี้" อยู่ภายใน item
  - ปุ่ม "+ เพิ่มรายการ (รูปใหม่)" อยู่ท้าย section
- **Section: "อื่นๆ"** — รายการ CustomItem
  - ปุ่ม "+ เพิ่มรายการอื่นๆ"
- Footer: ยอดเงิน + ปุ่มบันทึก

### Order Detail / Print View
- Layout แบบ stacked (รูปบน รายละเอียดล่าง) — **รูปใหญ่สำหรับฝ่ายผลิต**
- Section "เสื้อ" แสดงก่อน แล้วตามด้วย section "อื่นๆ" (ถ้ามี)
- คำสั่งพิเศษแสดงด้วยสีแดง (เหมือนเดิม)
- Print view ต้องมี: เลข order, วันที่, ชื่อลูกค้า, รูปดีไซน์ (ใหญ่), variant ทั้งหมด, custom items, ยอดเงิน
- mobile-friendly (staff + ฝ่ายผลิตใช้มือถือในร้านได้)

### Order List
- แสดงเลข order, วันที่, ลูกค้า, ยอดรวม, status, badge "เก่า" ถ้า schema_version=1
- ปุ่ม "แก้ไข" ซ่อนถ้าเป็น legacy order
- ปุ่ม "ลบ" แสดงเฉพาะ admin และไม่ใช่ legacy order

---

## Order Number Format
`{ปี พ.ศ. 2 หลัก}{เดือน 2 หลัก}-{running 1-999}` เช่น `6903-1`, `6903-45`, `6903-999`
- ขึ้น running ใหม่ทุกเดือน
- ไม่ pad ศูนย์หน้าเลข running

---

## Deployment

### Production URL
`https://dr89.cloud/order/`

### Environment Variables (ดู .env.example)
| Variable | ค่าใน Production |
|---|---|
| SECRET_KEY | (auto-generated by setup.sh) |
| DEBUG | False |
| ALLOWED_HOSTS | dr89.cloud |
| FORCE_SCRIPT_NAME | /order |
| DB_NAME | order_db |
| DB_USER | order_user |
| DB_PASSWORD | (ตั้งเอง) |

### Deploy Commands
```bash
# First time
sudo bash deploy/setup.sh

# After code update
rsync -av --exclude='.git' --exclude='db.sqlite3' --exclude='media/' ./ root@dr89.cloud:/opt/order/
ssh root@dr89.cloud "cd /opt/order && venv/bin/python manage.py migrate && venv/bin/python manage.py collectstatic --noinput && systemctl restart order"
```

### Migration สำหรับ Phase 1.5
```bash
# บน VPS:
cd /opt/order
venv/bin/python manage.py migrate                           # สร้าง ShirtVariant + CustomItem + schema_version
venv/bin/python manage.py set_legacy_schema                 # management command: set schema_version=1 ให้ order เก่า
venv/bin/python manage.py create_default_groups             # สร้าง Groups: admin, staff
venv/bin/python manage.py createsuperuser                   # สร้าง admin user
# สร้าง staff user ผ่าน Django admin ที่ /order/admin/
```

### Nginx Config (ยกเลิก Basic Auth)
ลบบรรทัด `auth_basic` และ `auth_basic_user_file` ออกจาก nginx.conf
(Django จะจัดการ authentication แทน)

### Service Management
```bash
systemctl status order
systemctl restart order
journalctl -u order -f
```

---

## Decisions Log
- ใช้ Django เพราะ migration ง่าย เพิ่ม field ได้โดยไม่ยุ่งยาก
- staff only ไม่มี customer login (phase 1)
- รูปเก็บ local media ก่อน ไม่ใช้ cloud storage
- ไม่ใช้ DRF — ใช้ Django Templates แบบ traditional
- OrderItem sizes ใช้ JSONField แทน individual qty fields — ยืดหยุ่นกว่า
- Sub-path deploy ที่ `/order/` ด้วย FORCE_SCRIPT_NAME
- WhiteNoise serve static files แทน nginx
- Gunicorn bind localhost:8100, nginx reverse proxy
- **[Phase 1.5]** เปลี่ยน auth จาก HTTP Basic → Django built-in เพื่อรองรับหลาย user + สิทธิ์
- **[Phase 1.5]** แยก ShirtVariant ออกจาก OrderItem: 1 รูปใส่ได้หลายแบบ ไม่ต้องอัปรูปซ้ำ
- **[Phase 1.5]** แยก CustomItem เป็น model ต่างหาก เพื่อรองรับสินค้าที่ไม่ใช่เสื้อ (กระเป๋าผ้า ฯลฯ)
- **[Phase 1.5]** ออร์เดอร์เก่า (schema_version=1): ไม่ migrate ข้อมูล, แสดง read-only, ใช้ URL เดิม
- **[Phase 1.5]** Print view: เปลี่ยน layout เป็นรูปบน/รายละเอียดล่าง เพราะรูปเล็กไป ฝ่ายผลิตดูไม่ชัด

---

## Local Development
```bash
# Setup
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Run
python manage.py migrate
python manage.py create_default_groups
python manage.py createsuperuser
python manage.py runserver

# เพิ่ม field ใหม่
python manage.py makemigrations
python manage.py migrate
```
