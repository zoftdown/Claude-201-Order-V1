# CLAUDE.md — Order System (ร้านพิมพ์เสื้อ)

> **Version:** V2.3 · อัปเดตล่าสุด 2026-05-31 · migration ล่าสุด `0015_alter_order_production_place` (เพิ่ม "ร้านตูน" ใน production_place choices) · feature ล่าสุด: หน้า list 2 tab (ด่วน / เรียงตามวันปกติ)

## Project Overview
ระบบจัดการใบออร์เดอร์สำหรับร้านพิมพ์เสื้อ
- Web app สำหรับใช้ในร้าน (staff only, มี login)
- Django + PostgreSQL
- **URL:** https://dr89.cloud/order/
- Deploy บน **Hostinger VPS KVM1** (มาเลเซีย, ping ~41ms จากไทย)
- OS: Ubuntu

---

## Tech Stack
- **Backend:** Python 3.11+, Django 6.0.x
- **Database:** PostgreSQL (production), SQLite (dev fallback อัตโนมัติถ้าไม่มี DB env vars)
- **Frontend:** Django Templates + **Bootstrap 5.3.3** (CDN: cdn.jsdelivr.net) — *ไม่ใช่ Tailwind*
- **WSGI:** Gunicorn (bind localhost:8100)
- **Static files:** WhiteNoise (CompressedManifestStaticFilesStorage)
- **Reverse proxy:** Nginx
- **File storage:** Local media folder (รูปดีไซน์ + รูปมาสเตอร์ + รูปที่เซ็นแล้ว) — ย่อรูปอัตโนมัติด้วย **Pillow** (ด้านยาวสุด ≤1600px)
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
templates/orders/ # order_list, order_form, order_detail, order_print, order_pick, order_master
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
| **production_place** | CharField | ผลิตที่: ผลิตเอง / ร้านแอม / ร้านแบ้งค์ (default ผลิตเอง) — outsource (≠ผลิตเอง) แสดงสีเขียวใน list/detail/print/ใบมาสเตอร์ |
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
| **signed_image** | ImageField | รูปที่เซ็นแล้ว (upload signed/YYYY/MM/), 1 รูป/ใบ, nullable, ย่อรูปอัตโนมัติ ≤1600px (หลักฐานทุกฝ่ายเซ็นตรวจ) |

> นอกจากนี้ Order ยังมี field สาย production-floor (Phase 1.6–1.8): `print_done_at / roll_done_at / cut_done_at / sort_done_at / sent_to_tailors_at / packed_at / shipped_at / awaiting_pickup_at`, `needs_repair / repair_done_at`, และ M2M `tailors` — ใช้กับ dept dashboard + QR update stage + StageLog

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

### MasterImage (รูปมาสเตอร์ — 1 Order มีได้หลายรูป)
| Field | Type | หมายเหตุ |
|---|---|---|
| order | FK → Order | related_name='master_images' |
| image | ImageField | รูปมาสเตอร์ (upload masters/YYYY/MM/), ย่อรูปอัตโนมัติ ≤1600px |
| order_index | int | ลำดับ |
- ใช้พิมพ์ **"ใบมาสเตอร์"** ให้ทีมเซ็นตรวจ (ก่อนเซ็น) — แยกจาก `OrderItem.design_image` (รูปดีไซน์) และ `Order.signed_image` (หลังเซ็น) โดยสิ้นเชิง

### Image downscale helper (ใช้ร่วม)
- `downscale_image_field(field, max_side=1600, quality=85)` — **module-level helper ใน models.py** เรียกใน `save()` ของทั้ง `MasterImage.image` และ `Order.signed_image` (ไม่เขียนซ้ำ)
- ย่อด้านยาวสุด ≤1600px (รูปเล็กกว่าปล่อยไว้ ไม่ re-encode), JPEG q85 / PNG optimize, EXIF transpose, แก้ในไฟล์เดิม (local fs)
- มี try/except กันพังทุกชั้น: ไม่มี PIL / `field.path` ใช้ไม่ได้ / เปิดไฟล์ไม่ได้ → return เงียบ ไม่ block การ save

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
- หน้า list: filter status + **2 tab (ด่วน / เรียงตามวันปกติ)**
- **Tab หน้า list (`?tab=`)** — แก้ปัญหาใบด่วนถูกยกไปกองบนสุดจน "หลุด" ลำดับวันปกติ คนพิมพ์มองข้าม:
  - **ด่วน** (default, `tab=urgent`) — เฉพาะ `is_urgent=True`, เรียง `-created_date, -id`
  - **เรียงตามวันปกติ** (`tab=normal`) — **ทุกใบ** inline by date (`-created_date, -id` เท่านั้น ไม่มี `-is_urgent`) ใบด่วนคงลำดับวัน + badge "ด่วน"
  - ค่าแปลก/ว่าง → fallback `urgent`. status filter + search `q` คง tab (hidden input `tab` ในฟอร์มค้นหา + ต่อ `&tab=` ทุก status link รวมปุ่ม "ทั้งหมด")
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

**เพิ่มล่าสุด (V2.3 · 2026-05-23):**
- **ผลิตที่ (production_place)** — เลือกผลิตเอง/ร้านแอม/ร้านแบ้งค์; outsource แสดงสีเขียว (list/detail/print/ใบมาสเตอร์)
- **custom_search ขยาย** — filter คนเย็บ + แหล่งที่มา + ผลิตที่ + ช่วงวันที่สร้าง (ทุก filter **AND** กัน; เว้นว่าง/ค่าไม่ถูกต้อง = ข้าม)
- **รูปมาสเตอร์ (MasterImage, หลายรูป/ใบ)** — order_form อัปโหลด**แบบหลายช่อง** (1 ช่อง = 1 รูป) + **วางคลิปบอร์ดได้ทุกช่อง** (workflow print screen จาก Photoshop เป็นหลัก), เพิ่ม/ลบช่องได้; รูปเดิมตอนแก้ไขใบไม่หาย
- **ใบมาสเตอร์ (`/order/<id>/master/`)** — รูปมาสเตอร์ใหญ่เต็มกว้างไล่ลงมา + ช่องเซ็น 6 ฝ่าย (วันที่/กราฟิก/วางพิมพ์/เลเซอร์/คนคัด/ชื่อลูกค้า) + คำเตือนเด่น **"⚠ ดูให้ดีก่อนเซ็น ⚠"** + หัว: เลข order + ชื่องาน (ไม่มี QR/ราคา). ปุ่มควบคุมอยู่นอก `.page-a4`
  - ปุ่มในใบงานเปลี่ยน **"พิมพ์ใบคัด" → "พิมพ์ใบมาสเตอร์"** (ชี้ `/master/` แทน `/pick/`). *ใบคัด (order_pick) ยังอยู่ในระบบ แต่ไม่มีปุ่มลิงก์แล้ว — เข้าได้แค่พิมพ์ URL `/pick/` ตรงๆ*
- **รูปที่เซ็นแล้ว (signed_image, 1 รูป/ใบ)** — order_form ช่องอัปโหลด (แยก card จากรูปมาสเตอร์) + วางคลิปบอร์ด + แทนที่/ลบได้, ย่อรูปอัตโนมัติ. หลักฐานว่าทุกฝ่ายตรวจ+เซ็นแล้ว
- **หน้า detail** — รูป item layout 50:50 (เสมอ); ใต้รายการเสื้อโชว์ **thumbnail รูปมาสเตอร์ + รูปที่เซ็นแล้ว คลิกขยาย** (lightbox ทำเอง CSS+JS เล็กๆ, gate รวม `master_images OR signed_image`, ไม่มีรูปก็ซ่อนทั้งหมด ไม่รก); มี stage progress timeline
- **ใบคัด (order_pick) layout 80:20** — 1 item/หน้า A4 (รูปดีไซน์ 80% + variant) เหมือน print view
- **หน้า list** — ชื่อเสื้อยาว truncate (ellipsis + hover tooltip)

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
ssh root@dr89.cloud "cd /opt/order && set -a && . ./.env && set +a && venv/bin/python manage.py migrate && venv/bin/python manage.py collectstatic --noinput && systemctl restart order"
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
- Cron (root): `0 2 * * * /opt/order/backup_db.sh` — ทุกวัน 02:00 เวลา VPS (UTC) ≈ 9 โมงเช้าไทย; เก็บย้อนหลัง 90 วัน. เช็ก `crontab -l`
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

8. **ย่อรูปด้วย Pillow ต้องกันพังด้วย try/except** — `downscale_image_field()` ครอบ try/except ทุกชั้น (ไม่มี PIL / `field.path` ใช้ไม่ได้บน non-local storage / เปิดไฟล์ไม่ได้ → return เงียบ) เพื่อไม่ให้การย่อรูป block การ save ใบงาน. ย่อเฉพาะรูปด้านยาว >1600px (รูปเล็กกว่าปล่อยไว้ ไม่ re-encode เสียคุณภาพ). save() มี guard `if not field: return` → ใบที่ไม่มีรูป + ทุก stage-update แทบไม่เสีย cost

9. **reuse helper/component เดิม อย่าเขียนซ้ำ** — logic ย่อรูปดึงเป็น `downscale_image_field` module-level ใช้ร่วม MasterImage + signed_image; lightbox หน้า detail ใช้ตัวเดียว (`.master-thumb` + `#masterLightbox`) gate รวม master_images OR signed_image; clipboard paste ใช้ `.paste-image-btn` delegate ตัวเดิมทุกที่ (data-target ชี้ id ของ input). ก่อนเพิ่มของใหม่ เช็กก่อนว่ามี pattern เดิมให้ reuse ไหม

10. **verify ว่า commit ทำจริงครบก่อนสรุปว่าเสร็จ** — เคยเข้าใจว่า order_detail โชว์รูปมาสเตอร์แล้ว แต่จริงๆ ยังไม่มีทั้งใน git และ VPS (grep/checksum ยืนยันก่อน). ก่อน deploy ทุกครั้ง: `git fetch` + เทียบ HEAD กับ origin/main + `sha256sum` ไฟล์ VPS เทียบ local ก่อน scp ทับ (กันเขียนทับงานที่แก้มือบน VPS)

11. **PowerShell ≠ Bash — `&&` ใช้ไม่ได้ ห้าม batch คำสั่ง deploy** — เครื่อง dev เป็น Windows (PowerShell). ใน PowerShell 5.1 ตัว `&&`/`||` เป็น parser error และถ้าส่งหลายคำสั่งพร้อมกันใน turn เดียวแล้วอันใดอันหนึ่ง syntax พัง → **ทั้งชุดถูก cancel** (เคยทำ scp+migrate หาย เพราะ git diff ตัวก่อนหน้า quoting พังเลยยกเลิกหมด). **deploy ต้องรันทีละคำสั่ง sequential** + verify ผลแต่ละขั้นก่อนไปต่อ. ระวัง nested quoting ลึก (PowerShell→ssh→bash→python) — เลี่ยงโดยเขียนไฟล์ `.sql`/`.py` บน VPS แล้วรันด้วย `-f`/pipe แทนการยัด quote ซ้อน

12. **`git hash-object` MISMATCH หลัง scp บน Windows = false alarm** — Windows checkout เป็น CRLF + บางไฟล์มี BOM, แต่ VPS เก็บดิบ → `git hash-object` ฝั่ง Windows ทำ CRLF→LF normalization เลยได้ hash ต่างจาก VPS **ทั้งที่เนื้อไฟล์เหมือนกัน** (scp คัดลอก byte-exact). อย่าด่วนสรุปว่า "VPS ถูกแก้มือ/scp พัง" — ยืนยันด้วย `git diff --no-index --ignore-all-space` (ดูเนื้อจริง) หรือ semantic check (grep โค้ดที่เพิ่ม / `makemigrations --check`) ก่อนตัดสิน. **อย่าใช้ PowerShell `Out-File`/pipe สร้างไฟล์ที่ python จะ exec** — มันเติม BOM (U+FEFF) ทำ SyntaxError

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
- รูปอัปโหลด (มาสเตอร์/เซ็น) ย่ออัตโนมัติ ≤1600px ตอน save ผ่าน helper ร่วม — ลดขนาดรูปถ่ายมือถือ
- แยก 3 บทบาทของรูป: `OrderItem.design_image` (ดีไซน์) · `MasterImage` (ใบมาสเตอร์ ก่อนเซ็น, หลายรูป) · `Order.signed_image` (หลังเซ็น, 1 รูป)
- รูปมาสเตอร์อัปโหลดแบบหลายช่อง (ช่องละ 1 รูป ชื่อ field เดียวกัน → `getlist`) เพราะ workflow หลักคือวางจาก clipboard

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
