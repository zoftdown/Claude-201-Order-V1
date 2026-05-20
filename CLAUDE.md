# CLAUDE.md — Order System (ร้านพิมพ์เสื้อ)

> ⚠️ **อ่านก่อนแก้โค้ดทุกครั้ง** — ไฟล์นี้คือ single source of truth
> ห้ามลบ/ทับฟีเจอร์ที่ลิสต์ใน "Features ที่มีอยู่จริง" โดยไม่ได้รับคำสั่งชัดเจน
> เมื่อแก้ template/view ต้องคงฟีเจอร์เดิมที่อยู่ในไฟล์นั้นไว้ครบ (ดู "กฎกันของเดิมหาย")

## Project Overview
ระบบจัดการใบออร์เดอร์สำหรับร้านพิมพ์เสื้อ
- Web app สำหรับใช้ในร้าน (staff only, มีระบบ login)
- Django + PostgreSQL
- **URL:** https://dr89.cloud/order/
- Deploy บน **Hostinger VPS KVM1** (฿249/เดือน แผน 12 เดือน)
- Server location: มาเลเซีย (ping ~41ms จากไทย)
- OS: Ubuntu

---

## Tech Stack
- **Backend:** Python 3.11+, Django 4.x
- **Database:** PostgreSQL (dev fallback: SQLite)
- **Frontend:** Django Templates + Bootstrap
- **WSGI:** Gunicorn (port 8100)
- **Static files:** WhiteNoise (CompressedManifestStaticFilesStorage)
- **Reverse proxy:** Nginx
- **File storage:** Local media folder (รูปภาพ)
- **Export:** PDF / print view (ใบออร์เดอร์พิมพ์ได้)

---

## Project Structure
```
config/           # Django project settings, urls, wsgi
orders/           # Main app (models, views, forms, urls)
templates/        # HTML templates
  base.html       # มี CSS class .special-note (color: red; font-weight: bold)
  orders/         # order_list, order_form, order_detail, order_print
static/           # Source static files
staticfiles/      # collectstatic output (gitignored)
media/            # Uploaded images (gitignored)
backups/          # DB backups (gitignored — ห้าม commit ข้อมูลลูกค้า)
deploy/           # Production deployment configs
  nginx.conf      # Nginx location block for /order
  gunicorn.conf.py
  order.service   # Systemd service
  setup.sh        # One-click VPS setup script
.env.example      # Template for environment variables
```

---

## Data Models

### Order (ใบออร์เดอร์)
| Field | Type | หมายเหตุ |
|---|---|---|
| order_number | CharField | รันอัตโนมัติ เช่น 6903-1, 6903-45 (ขึ้นใหม่ทุกเดือน) |
| created_date | DateField | วันที่สร้าง |
| print_date | DateField | วันที่พิมพ์เสื้อ (nullable) |
| source | CharField | choices: เพจเสื้อเนินสูง / เพจเสื้อคนงาน / เฮีย&เจ๊ / หน้าร้าน / เพจปักผ้า / LINE OA / เพจร้าน Yada / เพจเสื้อทุเรียน / Shopee / Tiktok |
| customer_name | CharField | ชื่อลูกค้า |
| customer_link | CharField | Facebook URL หรือเบอร์โทร |
| shirt_name | CharField | ชื่องาน/ชื่อเสื้อ เช่น "แผงกั๊วเป้า" |
| fabric_spec | TextField | spec ผ้า (แสดงเฉพาะ source=เพจเสื้อคนงาน) |
| special_note | TextField | คำสั่งพิเศษ (แสดงสีแดง) |
| **is_urgent** | BooleanField | **ใบงานด่วน** (default=False) — ติ๊กในหน้า create/edit, แสดง 🚨 หน้าเลข order + แถวสีแดงในหน้า list + banner ในหน้า detail + ลอยขึ้นบนสุดของ list ✅ ทำจริงแล้ว (models.py + forms.py + views.py order_by('-is_urgent','-id')) |
| total_price | DecimalField | ยอดรวม |
| deposit | DecimalField | มัดจำ |
| delivery_method | CharField | choices: รับเอง / ส่ง |
| shipping_address | TextField | ที่อยู่จัดส่ง |
| status | CharField | choices: รอดำเนินการ / กำลังผลิต / เสร็จแล้ว / ส่งแล้ว |
| **stage** | CharField/Int | **ขั้นตอนการผลิต** (timeline) — พิมพ์ / โรล / ตัด / คัด / ส่งเย็บ / รีด+แพ็ค / ลูกค้ารับ *(ยืนยันโครงสร้างจริงใน models.py)* |

### OrderItem (รายการในใบออร์เดอร์)
1 Order มีได้หลาย Item (ไม่จำกัด)

| Field | Type | หมายเหตุ |
|---|---|---|
| order | ForeignKey | → Order |
| design_image | ImageField | รูปดีไซน์ (optional) |
| sleeve_type | CharField | choices: แขนสั้น / แขนยาว / แขนกุด |
| collar_type | CharField | choices: คอกลม / คอวี / โปโล / คอปกวี / คอกีฬา / อื่นๆ |
| color | CharField | สีเสื้อ (กรอกอิสระ) |
| sizes | JSONField | `[{"label": "S", "qty": 5}, {"label": "M", "qty": 10}, ...]` |

> หมายเหตุ: 1 OrderItem (1 รูปดีไซน์) รองรับได้หลาย "แบบ" (variant: คอ/แขน/สี/ไซส์) — เห็นในหน้าแก้ไข "แบบที่ 1, 2, 3..." ใต้แต่ละรูป

---

## Features ที่มีอยู่จริง (อย่าทำหาย)

### ✅ Core
- [x] สร้าง/แก้ไขใบออร์เดอร์ + รันเลข order อัตโนมัติ
- [x] เพิ่ม/ลบ OrderItem แบบ dynamic (ไม่จำกัด) + แนบรูปดีไซน์ + วางจากคลิปบอร์ด
- [x] หลาย "แบบ" ต่อ 1 รูป (variant: คอ/แขน/สี/ไซส์)
- [x] แสดง fabric_spec เฉพาะเมื่อ source = เพจเสื้อคนงาน
- [x] ค้นหา order (ชื่อลูกค้า / ชื่อเสื้อ / เลข order)
- [x] Print view ใบออร์เดอร์ (พิมพ์ได้)
- [x] Production deployment (nginx, gunicorn, systemd)

### ✅ หน้า List
- [x] **🚨 ตัวเตือนด่วน** — order ที่ติ๊ก is_urgent มี icon 🚨 สีแดงหน้าเลข order **และลอยขึ้นบนสุด** ⚠️ ห้ามทำหาย
- [x] Filter status (ทั้งหมด / รอดำเนินการ / กำลังผลิต / เสร็จแล้ว / ส่งแล้ว)
- [x] **คอลัมน์คำสั่งพิเศษ** — แสดง special_note สีแดง (class `.special-note`) ถัดจากชื่อลูกค้า, word-wrap, max-width 220px, โชว์ทั้ง desktop+mobile
- [x] คอลัมน์: เลข Order / วันที่ / แหล่ง / ชื่อเสื้อ / ชื่อลูกค้า / คำสั่งพิเศษ / จำนวน (+ปุ่มแก้ไข)

### ✅ ระบบ User / สิทธิ์
- [x] **Django login** (แทน HTTP Basic Auth เดิม) — มีปุ่ม "ออกจากระบบ"
- [x] **จัดการ user** (หน้า admin จัดการ user) — แยกสิทธิ์ admin vs staff (Groups)
- [x] **PIN 4 หลัก** ก่อนตั้ง department cookie *(feat: require 4-digit PIN)*
- [x] **Read-only department "ดู/ค้นหา"** — viewer ดูได้อย่างเดียว แก้ไม่ได้

### ✅ Dashboard / Timeline
- [x] **Stage progress timeline** ในหน้า detail (เหนือ section รายการ) — พิมพ์→โรล→ตัด→คัด→ส่งเย็บ→รีด+แพ็ค→ลูกค้ารับ
- [x] **Department dashboard** — inline search + per-order stage action

### 🔜 Phase 2 — เพิ่มเติม
- [ ] Export PDF ใบออร์เดอร์ (จริงจัง)
- [ ] สถิติ/รายงาน (ยอดขาย, จำนวนเสื้อ)
- [ ] วันส่งเย็บ / วันนัดลูกค้า
- [ ] ประวัติการแก้ไข order

---

## กฎกันของเดิมหาย (สำคัญมาก)

1. **ก่อนแก้ template/view ใดๆ** ให้อ่านไฟล์นั้นทั้งไฟล์ก่อน แล้วลิสต์ฟีเจอร์ที่มีอยู่ออกมา — ห้ามแก้แบบไม่ดูของเดิม
2. **เปลี่ยนเฉพาะส่วนที่ผู้ใช้สั่ง** ส่วนอื่นในไฟล์ต้องเหมือนเดิมเป๊ะ (เช่น เพิ่มคอลัมน์ใหม่ ห้ามลบ icon 🚨 หรือคอลัมน์อื่น)
3. **หลังแก้เสร็จ** ตรวจว่าฟีเจอร์ในลิสต์ "Features ที่มีอยู่จริง" ที่เกี่ยวกับไฟล์นั้น ยังอยู่ครบ
4. **ทุกครั้งที่เพิ่ม/แก้ฟีเจอร์** → อัปเดต CLAUDE.md ส่วน "Features ที่มีอยู่จริง" ในงานเดียวกัน ก่อน commit
5. ทดสอบด้วย test client + rollback ไม่ให้ dev DB โดนแตะ

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
> ⚠️ เครื่อง dev (Windows) **ไม่มี rsync** และ WSL ไม่มี distro → ใช้ `scp` แทน
```bash
# แก้ไฟล์เดียว/ไม่กี่ไฟล์ (template-only / view-only) — ไม่ต้อง migrate/collectstatic
scp <path/file> root@dr89.cloud:/opt/order/<path/file>
ssh root@dr89.cloud "cd /opt/order && systemctl restart order"

# ถ้าแก้ model (เพิ่ม field) — ต้อง migrate ด้วย
ssh root@dr89.cloud "cd /opt/order && venv/bin/python manage.py migrate && systemctl restart order"

# ถ้าแก้ static files — ต้อง collectstatic ด้วย
ssh root@dr89.cloud "cd /opt/order && venv/bin/python manage.py collectstatic --noinput && systemctl restart order"
```
> ⚠️ Caveat ของ scp ทีละไฟล์: ถ้า VPS ตามหลัง repo หลายไฟล์ จะ sync ไม่ครบ
> ทางเลือกระยะยาว: เปลี่ยน VPS เป็น `git pull` หรือ script scp ที่ mirror exclude list

### Service Management
```bash
systemctl status order        # เช็คสถานะ
systemctl restart order       # restart หลังแก้โค้ด
journalctl -u order -f        # ดู logs
```

---

## Git Workflow
- **Repo:** github.com/zoftdown/Claude-201-Order-V1 (remote `origin`)
- **Branch หลัก:** `main`
- **Claude Code ใช้ git worktree** — แต่ละ task อยู่ branch `claude/...` ของตัวเอง, primary tree เป็น `main`
- เครื่องร้าน ↔ บ้าน: commit → push origin main → อีกเครื่อง pull
- **commit เฉพาะไฟล์ที่แก้** (`git add <file>` ไม่ใช่ `git add .`) — กัน CLAUDE-V2.md / _nul / db.sqlite3 หลุดขึ้น git
- ไฟล์ห้าม commit: `backups/`, `media/`, `staticfiles/`, `db.sqlite3`, `_nul`, ข้อมูลลูกค้า

---

## UI/UX Notes
- ใบออร์เดอร์แสดงแบบ card ต่อ item — รูป + ประเภท + สี + ตารางไซส์
- คำสั่งพิเศษ (special_note) แสดงด้วยสีแดง — class `.special-note` ใน base.html (color: red; font-weight: bold)
- ใบงานด่วน (is_urgent) — 🚨 หน้าเลข order + ขึ้นบนสุด list
- Print view ต้องมี: เลข order, วันที่, ชื่อลูกค้า, รูปดีไซน์, จำนวนแต่ละไซส์, ยอดเงิน, QR code
- Print: รูปอยู่ซ้าย ตารางไซส์ขวา — variant block spacing แน่นเพื่อให้ 1 หน้าได้ 2-3 แบบ
- mobile-friendly (staff ใช้มือถือในร้าน)

---

## Order Number Format
`{ปี พ.ศ. 2 หลัก}{เดือน 2 หลัก}-{running 1-999}` เช่น `6903-1`, `6903-45`, `6903-999`
- ขึ้น running ใหม่ทุกเดือน (เริ่ม 1 ใหม่)
- ไม่ pad ศูนย์หน้าเลข running

---

## Decisions Log
- ใช้ Django เพราะ migration ง่าย เพิ่ม field ได้โดยไม่ยุ่งยาก
- staff only — ใช้ Django login + Groups (admin/staff), ไม่มี customer login
- รูปเก็บ local media ก่อน ไม่ใช้ cloud storage
- ไม่ใช้ DRF — ใช้ Django Templates แบบ traditional
- OrderItem sizes ใช้ JSONField แทน individual qty fields — ยืดหยุ่นกว่า
- Sub-path deploy ที่ `/order/` ด้วย FORCE_SCRIPT_NAME
- WhiteNoise serve static files แทน nginx
- Gunicorn bind localhost:8100, nginx reverse proxy
- เครื่อง dev ไม่มี rsync → deploy ด้วย scp ทีละไฟล์ (key-based auth ตั้งไว้แล้ว)
- Claude Code ใช้ git worktree (หลาย branch พร้อมกัน)

---

## Local Development
```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

python manage.py migrate
python manage.py runserver      # ใช้ SQLite อัตโนมัติถ้าไม่มี DB env vars

# เพิ่ม field ใหม่
# 1. แก้ models.py
python manage.py makemigrations
python manage.py migrate
```
