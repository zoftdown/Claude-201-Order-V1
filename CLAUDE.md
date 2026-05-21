# CLAUDE.md — Order System (ร้านพิมพ์เสื้อ)

## Project Overview
ระบบจัดการใบออร์เดอร์สำหรับร้านพิมพ์เสื้อ
- Web app สำหรับใช้ในร้าน (staff only, มี login)
- Django + PostgreSQL
- **URL:** https://dr89.cloud/order/
- Deploy บน **Hostinger VPS KVM1** (มาเลเซีย, ping ~41ms จากไทย)
- OS: Ubuntu

---

## Tech Stack
- **Backend:** Python 3.11+, Django 4.x
- **Database:** PostgreSQL (production), SQLite (dev fallback อัตโนมัติถ้าไม่มี DB env vars)
- **Frontend:** Django Templates + **Bootstrap 5.3.3** (CDN: cdn.jsdelivr.net) — *ไม่ใช่ Tailwind*
- **WSGI:** Gunicorn (bind localhost:8100)
- **Static files:** WhiteNoise (CompressedManifestStaticFilesStorage)
- **Reverse proxy:** Nginx
- **File storage:** Local media folder (รูปดีไซน์)
- **Timezone:** USE_TZ=True, TIME_ZONE='Asia/Bangkok' (DB เก็บ UTC, แสดงผลแปลงเป็นไทย)

---

## Repo & Environments
- **GitHub:** github.com/zoftdown/Claude-201-Order-V1 (branch หลัก = `main`)
- **เครื่อง dev:** F:\CLAUDE\... (Windows 11) ทั้งที่ร้านและที่บ้าน
- **VPS:** /opt/order
- ⚠️ **main = production = GitHub ต้องตรงกันเสมอ** (เคยมีปัญหา branch แตกจาก production ทำให้เกือบเว็บล่ม — ดู Lessons)
- `.gitignore` กัน: db.sqlite3, media/, .env, staticfiles/, backups/ (ห้ามให้ข้อมูลลูกค้าเข้า git)

---

## Project Structure
```
config/           # Django settings, urls, wsgi
orders/           # Main app (models, views, forms, urls, migrations)
templates/orders/ # order_list, order_form, order_detail, order_print
static/ staticfiles/ media/   # (3 อันท้าย gitignored)
deploy/           # nginx.conf, gunicorn.conf.py, order.service, setup.sh
.env / .env.example
```

---

## Data Models

### Order (ใบออร์เดอร์)
| Field | Type | หมายเหตุ |
|---|---|---|
| order_number | CharField | auto เช่น 6905-391 (format ด้านล่าง) |
| created_date | DateField | วันที่สร้าง (เก็บแค่วันที่ ไม่มีเวลา) |
| print_date | DateField | วันที่พิมพ์เสื้อ (nullable) |
| source | CharField | เพจเสื้อเนินสูง / เพจเสื้อคนงาน / เฮีย&เจ๊ / หน้าร้าน / เพจปักผ้า / LINE OA / เพจร้าน Yada / เพจเสื้อทุเรียน / Shopee / Tiktok |
| customer_name | CharField | ชื่อลูกค้า (ไม่แสดงในหน้า list แล้ว) |
| customer_link | CharField | Facebook URL หรือเบอร์โทร |
| shirt_name | CharField | ชื่องาน/ชื่อเสื้อ |
| fabric_spec | TextField | spec ผ้า (แสดงเฉพาะ source=เพจเสื้อคนงาน) |
| special_note | TextField | คำสั่งพิเศษ (แสดงสีแดง) |
| total_price / deposit | DecimalField | ยอดรวม / มัดจำ |
| delivery_method | CharField | รับเอง / ส่ง |
| shipping_address | TextField | ที่อยู่จัดส่ง |
| status | CharField | รอดำเนินการ / กำลังผลิต / เสร็จแล้ว / ส่งแล้ว |
| **is_urgent** | BooleanField | ใบงานด่วน → badge แดง + เรียงขึ้นบน |
| **created_at** | DateTimeField | auto_now_add, nullable (ใบเก่า backfill = เที่ยงคืนของ created_date) |
| **updated_at** | DateTimeField | auto_now, nullable |
| **created_by** | FK→auth.User | SET_NULL, คนสร้างใบ (ใบเก่า=null แสดง "-") |
| **printed_at** | DateTimeField | nullable, เวลาที่กดปุ่ม "พิมพ์ใบงานแล้ว" (ใบเก่า backfill=created_date) |

**Properties:**
- `recently_edited` — แก้จริงภายใน 24 ชม. (updated_at ห่าง created_at >1 นาที AND updated_at อยู่ใน 24 ชม.) → badge "แก้ใบงาน"
- `not_printed` — printed_at is None → badge "ยังไม่พิมพ์ใบงาน"
- `created_time_display` — เวลาสร้างแบบ HH:MM (เวลาไทยผ่าน timezone.localtime); คืน None ถ้า = 00:00 (ซ่อนใบเก่า)

### OrderItem (รายการในใบ — 1 Order มีหลาย item, แต่ละ item = 1 รูปดีไซน์)
| Field | Type | หมายเหตุ |
|---|---|---|
| order | FK → Order | |
| design_image | ImageField | รูปดีไซน์ (upload designs/YYYY/MM/) |
| order_index | int | ลำดับ |

### ShirtVariant ("แบบ" — 1 OrderItem มีหลาย variant)
| Field | Type | หมายเหตุ |
|---|---|---|
| item | FK → OrderItem | |
| collar / sleeve / color | CharField | คอ / แขน / สี |
| sizes | JSONField | `[{"label":"S","qty":5}, ...]` |
| note | TextField | หมายเหตุแบบ |
| order_index | int | ลำดับ |

### อื่นๆ
- **Tailor** — ช่างเย็บ (M2M กับ Order)
- **DepartmentPIN** — PIN 4 หลัก (singleton, sha256) gate cookie แผนก
- **StageLog** — log การเปลี่ยน stage

---

## Features (สถานะปัจจุบันบน production)

### ✅ เสร็จแล้ว
- สร้าง/แก้ไขใบออร์เดอร์ + รันเลข auto + login (Django auth + Groups)
- OrderItem หลาย item, แต่ละ item มีหลาย ShirtVariant (คอ/แขน/สี/ไซส์)
- แนบรูปดีไซน์, fabric_spec เฉพาะเพจเสื้อคนงาน
- **custom_search** — ค้นหา (หน้าแยก)
- **user-management** — จัดการ user (admin)
- หน้า list: filter status, เรียง -is_urgent ขึ้นบน
- **คอลัมน์หน้า list (เรียงซ้าย→ขวา):** วันที่ | เวลา | เลข Order | สถานะ(badge) | ชื่อเสื้อ | แหล่ง | คำสั่งพิเศษ(แดง) | จำนวน | คนลงข้อมูล | [แก้ไข]
- **Badge สถานะ (Bootstrap, ใส่หลายอันพร้อมกันได้):**
  - ด่วน (bg-danger แดง) ← is_urgent
  - แก้ใบงาน (bg-warning text-dark ส้ม) ← recently_edited
  - ยังไม่พิมพ์ใบงาน (bg-secondary เทา) ← not_printed
  - ไม่มี → แสดง "—" / *หมายเหตุ: "มาใหม่" เคยมีแต่ถอดออกแล้ว (รกเกินไป)*
- **คอลัมน์เวลา** — เวลาสร้าง HH:MM (เวลาไทย); ใบเก่า (00:00) ซ่อนเป็น "—"
- **Print view (order_print.html):**
  - layout ต่อ item = **80:20** — รูปดีไซน์ 80% + variant แบบแรก 20% (แถวบน); variant ที่เหลือลงแถวล่าง CSS Grid auto-fit (`repeat(auto-fit, minmax(140px,1fr))`)
  - **ปุ่ม "บันทึกภาพ"** — html2canvas 1.4.1 ถ่าย div.page-a4 เป็น PNG, auto-crop ขอบขาวทุกด้าน (~20px padding). *หมายเหตุ: .price-box ต้องเป็น display:table (ไม่ใช่ inline-block) ไม่งั้น html2canvas ไม่ render ตัวเลขแดง*
  - **ปุ่ม "พิมพ์ใบงานแล้ว"** — POST mark printed_at, ซ่อนตอนพิมพ์จริง (อยู่ใน .print-controls ไม่ใช่ .page-a4)
- Production deployment (nginx, gunicorn, systemd)

### 🔜 ค้าง / อนาคต
- [ ] **เปลี่ยน VPS deploy เป็น git pull** (เลิก scp) — กันปัญหา branch ไม่ตรง
- [ ] ลบ db.sqlite3 ขยะบน VPS (`rm -f /opt/order/db.sqlite3`)
- [ ] ตั้ง git ที่บ้าน (clone main)
- [ ] ใบเก่าที่ is_urgent=True ผิด (จาก checkbox เก่าที่ค้าง) — เคลียร์ถ้าต้องการ (backfill is_urgent=False)
- [ ] Export PDF, สถิติ/รายงาน

---

## Deployment

### Deploy ปัจจุบัน = scp (rsync ใช้ไม่ได้บน Windows dev)
**Template เปลี่ยนอย่างเดียว (ไม่แตะ model):**
```bash
git add <files> && git commit -m "..." && git push origin main
scp templates/orders/<file>.html root@dr89.cloud:/opt/order/templates/orders/
ssh root@dr89.cloud "cd /opt/order && systemctl restart order"
curl -sI https://dr89.cloud/order/ | head -1   # คาดหวัง HTTP/1.1 302 Found = healthy
```

**Model เปลี่ยน (ต้อง migrate):**
```bash
scp orders/models.py orders/views.py root@dr89.cloud:/opt/order/orders/
scp orders/migrations/00XX_*.py root@dr89.cloud:/opt/order/orders/migrations/
# ⚠️ migrate ต้อง source .env ก่อน! ไม่งั้นไปโดน SQLite แทน Postgres
ssh root@dr89.cloud "cd /opt/order && set -a && . ./.env && set +a && venv/bin/python manage.py migrate && systemctl restart order"
curl -sI https://dr89.cloud/order/ | head -1
```

### Service Management
```bash
systemctl status order      # เช็คสถานะ
systemctl restart order     # restart หลังแก้โค้ด
journalctl -u order -f      # ดู logs
```

### Environment Variables (.env)
SECRET_KEY, DEBUG=False, ALLOWED_HOSTS=dr89.cloud, FORCE_SCRIPT_NAME=/order, DB_NAME=order_db, DB_USER=order_user, DB_PASSWORD

### Backup (DB)
**ชั้นใน — cron อัตโนมัติบน VPS:**
- Script `deploy/backup_db.sh` (= `/opt/order/backup_db.sh` บน VPS): source .env → `pg_dump -h … -w` (ดู Lessons ข้อ 7) → เขียน `/opt/order/backups/order_db_YYYYMMDD_HHMM.sql` → ลบไฟล์เก่ากว่า 90 วัน (`find -mtime +90 -delete`)
- Cron (root): `0 2 * * * /opt/order/backup_db.sh` — ทุกวันตี 2 (เวลา VPS). เช็ก `crontab -l`
- ⚠️ cron PATH น้อย (`/usr/bin:/bin`) — pg_dump ต้องอยู่ที่ `/usr/bin/pg_dump` (เป็นอยู่แล้ว). ทดสอบแบบ cron: `env -i PATH=/usr/bin:/bin /opt/order/backup_db.sh`
- `/opt/order/backups/` **gitignored** — ห้าม dump หลุดขึ้น git

**ชั้นนอก — ดึงลงเครื่อง dev + Google Drive (off-site, ทำเอง):**
```bash
ssh root@dr89.cloud "cd /opt/order && set -a && . ./.env && set +a && PGPASSWORD=\$DB_PASSWORD pg_dump -h \${DB_HOST:-localhost} -p \${DB_PORT:-5432} -w -U \$DB_USER \$DB_NAME > /tmp/order_db_dump.sql"
scp root@dr89.cloud:/tmp/order_db_dump.sql "C:\K6\CLAUDE\backups\order_db_$(date +%Y%m%d_%H%M).sql"
ssh root@dr89.cloud "rm -f /tmp/order_db_dump.sql"
# แล้วก๊อปไฟล์ใน C:\K6\CLAUDE\backups\ ขึ้น Google Drive
```

---

## Order Number Format
`{ปี พ.ศ. 2 หลัก}{เดือน 2 หลัก}-{running}` เช่น `6905-391`
- running ขึ้นใหม่ทุกเดือน (เริ่ม 1), ไม่ pad ศูนย์

---

## Lessons Learned (บทเรียนสำคัญ — อ่านก่อนแก้)

1. **migrate บน VPS ต้อง source .env เสมอ** — `set -a && . ./.env && set +a && ... migrate` ไม่งั้น manage.py ไปสร้าง/ใช้ SQLite เปล่าแทน Postgres (เคยพลาด, สร้าง db.sqlite3 ขยะ)

2. **main ต้อง = production เสมอ** — เคยมี production รัน branch อื่น (claude/...) ที่ไม่ตรง main → พอ scp จาก main ทับ → ฟีเจอร์หาย/เกือบล่ม. ตอนนี้ sync แล้ว แต่ตราบใดยังใช้ scp ต้องระวัง: **pull ก่อนทำงาน, push หลังเสร็จ, deploy VPS ผ่าน Claude Code เท่านั้น**

3. **backfill ใบเก่าต้องทำใน migration (RunPython) เลย** — ไม่งั้น field ใหม่ที่ใบเก่าได้ค่า default จะทำให้ badge ขึ้นผิดเต็มจอ (เคยเกิดกับ "มาใหม่" เขียวทั้ง 504 ใบ). printed_at/created_at backfill = created_date

4. **แยก commit ทีละฟีเจอร์** — อย่า bundle หลายงานใน commit เดียว ถ้าพังจะแยกไม่ออก

5. **html2canvas จุกจิก** — .price-box ต้อง display:table ไม่ใช่ inline-block (ไม่งั้น render ตัวเลขแดงไม่ติด). ปุ่มที่ไม่อยากให้ติดใน PNG ต้องอยู่นอก div.page-a4

6. **timezone** — created_at เก็บ UTC, ต้องใช้ timezone.localtime() แปลงเป็นไทยก่อนแสดง/เทียบเวลา (ไม่งั้นเพี้ยน 7 ชม.)

7. **pg_dump ต้องต่อ TCP (-h + PGPASSWORD + -w)** — `PGPASSWORD=$DB_PASSWORD pg_dump -h ${DB_HOST:-localhost} -p ${DB_PORT:-5432} -w -U $DB_USER $DB_NAME` ไม่งั้น default ไป Unix socket ที่ตั้ง **peer auth** → `FATAL: Peer authentication failed for user "order_user"` (peer = จับคู่ OS user `root` กับ DB role ไม่ตรง). ต้อง source .env ก่อนให้มี `$DB_*` ด้วย (เหมือนข้อ 1) — ต่อแบบเดียวกับที่ Django ต่อ (HOST=localhost + password). เคยพลาดตอน backup ก่อน deploy

---

## Decisions Log
- Django (migration ง่าย), staff-only + login, รูป local media
- ไม่ใช้ DRF — Django Templates ธรรมดา
- ShirtVariant แยกจาก OrderItem: 1 รูป → หลายแบบ (คอ/แขน/สี/ไซส์)
- sizes = JSONField (ยืดหยุ่น)
- Sub-path /order/ ด้วย FORCE_SCRIPT_NAME
- WhiteNoise serve static, Gunicorn :8100 + nginx reverse proxy
- Bootstrap (ไม่ใช่ Tailwind)
- legacy protection: ใบเก่า backfill ค่าให้ไม่ขึ้น badge ผิด

---

## Local Development
```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python manage.py migrate       # ใช้ SQLite อัตโนมัติถ้าไม่มี DB env vars
python manage.py runserver

# เพิ่ม field: แก้ models.py -> makemigrations -> migrate
```

### Git workflow (บ้าน <-> ร้าน <-> GitHub)
```bash
git pull            # ก่อนเริ่มงานเสมอ
# ...แก้โค้ด...
git add <files> && git commit -m "..." && git push origin main   # หลังเสร็จเสมอ
# deploy VPS ผ่าน Claude Code (scp) เท่านั้น
```
