# CLAUDE.md — Order System (ร้านพิมพ์เสื้อ)

> **Version:** V3.2 · อัปเดตล่าสุด 2026-07-24 · migration ล่าสุด `0023_userpin` (PIN ต่อคน) · feature ล่าสุด: **login ด้วย PIN ประจำตัว** (กรอก PIN ช่องเดียว → login เป็น user ตัวเอง; จัดการ PIN ในหน้า "จัดการ user"; fallback `/login/classic/`) · ก่อนหน้า: เฟส 5 dashboard สถิติร้าน — **ครบทุกเฟสของแผน CRM แล้ว** (เฟส 1-5: โปรไฟล์ลูกค้า 0019 → งานชุด 0020 → เชื่อม Brief 0021 → tag/export 0022 → dashboard) · **หมายเหตุ:** หน้า list = โซนด่วนตีกรอบบนสุด + list วันปกติ (ใบด่วนโชว์ซ้ำ 2 ที่) — **ไม่ใช่ tab** (tab เคย revert ไปแล้ว อย่าทำซ้ำ)

## Auth: login ด้วย PIN ประจำตัว (V3.2 · 2026-07-24)
- **หน้า `/login/` = ช่อง PIN ช่องเดียว** (`orders.views.pin_login`, template `registration/login.html`) —
  พนักงานกรอก PIN 4–8 หลักของตัวเอง → map ผ่าน model **`UserPin`** (OneToOne→User, `pin` unique,
  เก็บ plaintext โดยตั้งใจ — แอดมินเปิดดูให้คนที่ลืมได้) → `login()` เป็น user นั้น →
  `created_by`/`last_login`/log ทุกอย่างแยกรายคนเหมือนเดิม; PIN ผิดหน่วง 0.5s; รองรับ `?next=`
- **จัดการ PIN ที่หน้า "จัดการ user"** (`/manage/users/`, admin เท่านั้น): ตาราง user มีคอลัมน์ PIN
  (โชว์เลขตรงๆ — ไว้บอกพนักงานที่มาขอ/ลืม), ฟอร์ม เพิ่ม/แก้ user มีช่อง PIN
  (validate ตัวเลข 4–8 หลัก + กันซ้ำข้ามคน ด้วย `_validate_pin`; เว้นว่างตอนแก้ = ถอน PIN)
- **fallback `/login/classic/`** = username/password เดิม (LoginView, template `login_classic.html`,
  มีลิงก์เล็กใต้ฟอร์ม PIN) — กันล็อกทั้งร้านตอน PIN ยังไม่ถูกตั้ง / superuser ยังเข้าได้เสมอ;
  Django admin `/admin/` login แยกของตัวเองตามปกติ
- อย่าสับสนกับ PIN อื่นในระบบ: `DepartmentPIN` (gate cookie แผนก, sha256) · `STATS_PIN` (หน้าสถิติ) — คนละเรื่องกัน

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
| customer_name | CharField | ชื่อลูกค้า (ไม่แสดงในหน้า list แล้ว) — ยังเป็น source of truth ของข้อความบนใบงาน |
| customer_link | CharField | Facebook URL หรือเบอร์โทร |
| **customer** | FK→Customer | โปรไฟล์ลูกค้า (SET_NULL, migration 0019) — ใบเก่า=null ปล่อยไว้ (ไม่ backfill), ใบใหม่ผูกอัตโนมัติตอน save ผ่าน `_resolve_customer` |
| **parent_order** | FK→self | ใบเพิ่มชี้ "ใบแรกของชุด" (root) เสมอ (SET_NULL, migration 0020) — สร้างผ่านปุ่ม "สร้างใบเพิ่มจากใบนี้" (`/create/?from=<pk>`, `_resolve_parent_order` flatten ไป root ไม่ทำ chain) · `group_orders()` คืนทุกใบในชุด ([] = ใบเดี่ยว) ใช้ gate แบนเนอร์ detail/print · badge "งานชุด" teal `#0c7c92` ใน list (annotate `child_count` กัน N+1) |
| **brief_job_id** | IntegerField | id ของ Job ฝั่งระบบ Brief (nullable, migration 0021) — เซ็ตเมื่อเลือกจาก autocomplete ช่องเลขใบงานออกแบบ (JS เคลียร์เมื่อพิมพ์เอง, `_apply_brief_job`); มีค่า = ลิงก์ "🎨 เปิดใบงานออกแบบ" ใน detail + ยิง `order_ref` กลับฝั่ง Brief ตอน save (`_push_order_ref_to_brief`, best-effort ไม่ block) |
| shirt_name | CharField | ชื่องาน/ชื่อเสื้อ |
| **designer_name** | CharField | คนออกแบบ (กราฟิกที่ทำดีไซน์ — คนละคนกับ created_by; blank ได้) แสดง detail/print/form |
| **design_doc_number** | CharField | เลขใบงานออกแบบ (อ้างอิงงานออกแบบ; blank ได้) แสดง detail/print/form |
| fabric_spec | TextField | spec ผ้า (แสดงเฉพาะ source=เพจเสื้อคนงาน) |
| special_note | TextField | คำสั่งพิเศษ (แสดงสีแดง) |
| total_price / deposit | DecimalField | ยอดรวม / มัดจำ |
| delivery_method | CharField | รับเอง / ส่ง |
| shipping_address | TextField | ที่อยู่จัดส่ง |
| status | CharField | รอดำเนินการ / กำลังผลิต / เสร็จแล้ว / ส่งแล้ว |
| **is_urgent** | BooleanField | ใบงานด่วน → badge แดง + เรียงขึ้นบน |
| **waiting_confirm** | BooleanField | รอลูกค้าคอนเฟิร์ม (default False, migration 0018) → print/pick โดน overlay ดำ `rgba(0,0,0,0.45)` + ตัวขาว "รอคอนเฟิร์ม" กลางหน้า (ทั้งจอ+`@media print`) + ซ่อนปุ่ม action (เหลือปุ่มกลับ) กันพิมพ์/ผลิตก่อนยืนยัน + badge ม่วง `#6a1b9a` ใน list |
| **created_at** | DateTimeField | auto_now_add, nullable (ใบเก่า backfill = เที่ยงคืนของ created_date) |
| **updated_at** | DateTimeField | auto_now, nullable |
| **created_by** | FK→auth.User | SET_NULL, คนสร้างใบ (ใบเก่า=null แสดง "-") |
| **printed_at** | DateTimeField | nullable, เวลาที่กดปุ่ม "พิมพ์ใบงานแล้ว" (ใบเก่า backfill=created_date) |
| **signed_image** | ImageField | รูปที่เซ็นแล้ว (upload signed/YYYY/MM/), 1 รูป/ใบ, nullable, ย่อรูปอัตโนมัติ ≤1600px (หลักฐานทุกฝ่ายเซ็นตรวจ) |
| **extra_note** | TextField | โน้ตในช่อง "เพิ่มเติม" (blank ได้) — แสดงเด่นกรอบแดง 2 ชั้น ตัวใหญ่ใน print/detail (เตือนคนพิมพ์). *แอดมินเลือกพิมพ์เน้นย้ำเองในช่องนี้ — ไม่มี checklist สำเร็จรูป (เคยทำแล้วถอนออก ดู Lessons/Decisions)* |

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

### ExtraImage (รูปในช่อง "เพิ่มเติม" — 1 Order มีได้หลายรูป)
| Field | Type | หมายเหตุ |
|---|---|---|
| order | FK → Order | related_name='extra_images' |
| image | ImageField | รูปเพิ่มเติม (upload extras/YYYY/MM/), ย่อรูปอัตโนมัติ ≤1600px |
| order_index | int | ลำดับ |
- เลียนแบบ `MasterImage` ทุกอย่าง (multi-slot uploader + วางคลิปบอร์ดได้, save() เรียก `downscale_image_field`). save ผ่าน `_save_extra_images()` (name="extra_images", delete_extra) — ไม่ใช่ formset

### ExtraNameRow (ตารางรันชื่อ-เบอร์ ในช่อง "เพิ่มเติม" — export CSV เข้าโปรแกรม nesting)
| Field | Type | หมายเหตุ |
|---|---|---|
| order | FK → Order | related_name='extra_name_rows' |
| size / number / name | CharField | ไซส์ / เบอร์ / ชื่อ (blank ได้) |
| order_index | int | ลำดับ |
- save ผ่าน `_save_extra_name_rows()` — **wipe-and-recreate** จาก parallel POST arrays (`extra_size[] / extra_number[] / extra_name[]`); แถวที่ทั้ง 3 ช่องว่างหมด → ข้าม
- export CSV ที่ `/order/<id>/extra-csv/` (view `order_extra_csv`) — ดู CSV charset ใน Lessons ข้อ 13

### Image downscale helper (ใช้ร่วม)
- `downscale_image_field(field, max_side=1600, quality=85)` — **module-level helper ใน models.py** เรียกใน `save()` ของ `MasterImage.image`, `ExtraImage.image`, และ `Order.signed_image` (ไม่เขียนซ้ำ)
- ย่อด้านยาวสุด ≤1600px (รูปเล็กกว่าปล่อยไว้ ไม่ re-encode), JPEG q85 / PNG optimize, EXIF transpose, แก้ในไฟล์เดิม (local fs)
- มี try/except กันพังทุกชั้น: ไม่มี PIL / `field.path` ใช้ไม่ได้ / เปิดไฟล์ไม่ได้ → return เงียบ ไม่ block การ save

### Customer (โปรไฟล์ลูกค้า — เฟส 1 CRM, migration 0019)
| Field | Type | หมายเหตุ |
|---|---|---|
| name | CharField | ชื่อลูกค้า |
| facebook_link | CharField | ลิงก์ FB (blank ได้) |
| phone | CharField | เบอร์โทร (blank ได้) |
| note | TextField | โน้ต (blank ได้) |
| **tags** | M2M→CustomerTag | กลุ่มลูกค้า (เฟส 4, migration 0022) |
| created_at / updated_at | DateTimeField | auto |
- **เกิดอัตโนมัติตอน save ใบงาน** ผ่าน `_resolve_customer(request, order)`: customer_id จาก autocomplete → match ชื่อ+ลิงก์ตรงเป๊ะ → สร้างใหม่. สร้างมือได้จากปุ่ม "+ เพิ่มลูกค้า" หน้า `/customers/`
- หน้า: `/customers/` (list+ค้นหา+filter กลุ่ม+เพิ่ม+export, `customer_list`) · `/customers/<id>/` (โปรไฟล์: แก้ข้อมูล+กลุ่ม+ตารางราคา+ประวัติใบงาน, `customer_detail`) · API `/api/customers/?q=` (`customer_search_api`, JSON 10 คนแรก พร้อม prices — ใช้ทำ autocomplete) — ทั้งหมด `@login_required` (viewer cookie เข้าไม่ได้ เพราะเห็นราคา)
- navbar ปุ่ม "👥 ลูกค้า" (เฉพาะ user login)

### CustomerTag (กลุ่มลูกค้า — เฟส 4, migration 0022)
| Field | Type | หมายเหตุ |
|---|---|---|
| name | CharField(50) | unique เช่น "ลูกค้าประจำ", "โรงเรียน" |
- **สร้าง:** พิมพ์ในช่อง `new_tags` หน้าโปรไฟล์ (คั่น comma ได้, `get_or_create` แล้วติ๊กให้เลย) · **ติ๊กเข้า/ออก:** checkbox ในโปรไฟล์ (`_save_customer_tags` ใช้ `tags.set`) · **ลบ tag:** ผ่าน Django admin เท่านั้น
- **filter:** chips หน้า `/customers/` (`?tag=<id>` ทำงานร่วม `?q=` — `_filtered_customers` ใช้ร่วมกับ export ให้ผลตรงกัน)
- **export CSV:** `/customers/export/` (`customer_export_csv`) ตาม filter ปัจจุบัน — คอลัมน์ ชื่อ/ลิงก์/เบอร์/กลุ่ม/จำนวนใบ/สั่งล่าสุด/โน้ต, BOM ตัวเดียวต้นไฟล์ตาม Lessons ข้อ 13, ชื่อไฟล์ไทยผ่าน `filename*=UTF-8''`

### CustomerPrice (ราคาประจำตัวลูกค้า — หลายแถว/คน)
| Field | Type | หมายเหตุ |
|---|---|---|
| customer | FK → Customer | related_name='prices' |
| label / price | CharField / Decimal | เช่น "คอกลมแขนสั้น" 120 |
| order_index | int | ลำดับ |
- save แบบ **wipe-and-recreate** จาก parallel arrays (`price_label[]/price_value[]`) ใน `_save_customer_prices` — pattern เดียวกับ ExtraNameRow. แถวราคาไม่ใช่ตัวเลข → ข้าม
- **ฟอร์มใบงาน:** เลือกลูกค้าจาก autocomplete → ปุ่มราคาใต้กล่องเงิน (`#customer-price-panel`) กด = `total_price := จำนวนตัวรวม × ราคา` (client-side, แก้มือทับได้). หน้าแก้ไข inject ราคาผ่าน `{{ customer_prices|json_script }}` (view ส่ง `_customer_prices_payload`)
- **hidden `customer_id`** ในฟอร์ม: JS เซ็ตเมื่อเลือกจาก dropdown, เคลียร์เมื่อพิมพ์ชื่อเอง (server จะ match/สร้างใหม่)

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
- หน้า list: filter status + ค้นหา (**list เดียว ไม่มี tab**)
- **หน้า list = 2 โซน (V2.5):** view เรียง `order_by('-created_date', '-id')` (วันปกติ) แล้ว `list(orders)` + แยก `urgent_orders` ใน Python (query เดียว) + `prefetch_related('items','items__variants')` ตัด N+1 ของ `total_qty`
  - **โซน "⚠️ งานด่วน"** กรอบแดง 3px บนสุด — แสดงใบด่วนทั้งหมด
  - **โซน "ทั้งหมด (เรียงตามวันที่)"** — ใบด่วน**โชว์ซ้ำ**ในวันของมันด้วย (ไฮไลต์ `urgent-row` ชมพู) เพื่อกันตกหล่นตอนไล่ทำตามวัน
  - `{% regroup orders by created_date %}` → zebra เขียวสลับเข้ม/อ่อน (`date-group-a/b`) + เส้นคั่นเขียวทุกขอบวัน (`group-start`)
  - **2 partial ใช้ร่วมทั้ง 2 โซน:** `_order_list_head.html` (หัวตาราง) + `_order_row_cells.html` (cells)
  - ⚠️ **ไม่ใช่ tab** — โซนด่วน = card กรอบ (เคยลอง 2 tab commit 2163f75 แล้ว revert 498f358 → **อย่าทำ tab ซ้ำ**)
- **คอลัมน์หน้า list (reorder priority ซ้าย→ขวา):** วันที่(ย่อ `j M` บนมือถือ) | เลข Order | ชื่อเสื้อ | สถานะ(badge) | แหล่ง | เวลา | จำนวน | คนลงข้อมูล | คำสั่งพิเศษ(แดง) | [แก้ไข] — `table-responsive` เลื่อนแนวนอนบนมือถือ (priority อยู่ซ้าย)
- **Badge สถานะ (Bootstrap, ใส่หลายอันพร้อมกันได้):**
  - ด่วน (bg-danger แดง) ← is_urgent
  - รอคอนเฟิร์ม (ม่วง `#6a1b9a` inline style) ← waiting_confirm
  - แก้ใบงาน (bg-warning text-dark ส้ม) ← recently_edited
  - ยังไม่พิมพ์ใบงาน (bg-secondary เทา) ← not_printed
  - ไม่มี → แสดง "—" / *หมายเหตุ: "มาใหม่" เคยมีแต่ถอดออกแล้ว (รกเกินไป)*
- **คอลัมน์เวลา** — เวลาสร้าง HH:MM (เวลาไทย); ใบเก่า (00:00) ซ่อนเป็น "—"
- **Print view (order_print.html):**
  - layout ต่อ item **ตามจำนวน variant (V2.6, ใช้ทั้ง print+pick):** ≤2 กรอบ → รูปดีไซน์ ~72% + กรอบเรียงแนวตั้ง column ขวา ~28% (รูปเป็นพระเอก ฝ่ายผลิตต้องเห็นดีไซน์ชัด) / 3+ กรอบ → รูปเดี่ยวจัดกลาง 78% (`.solo-image`) + กรอบทั้งหมดลง grid ล่าง **4 ช่อง/แถว** (มือถือ 2 ช่อง). ตอนพิมพ์ รูปโดนเพดานสูง (single 120mm / multi-items 80mm / pick 150mm) กันล้นหน้า — ไม่มี rule บีบความกว้างรูปแล้ว (rule 50% multi-items + 60% print เดิมถูกตัดทิ้ง)
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

**เพิ่มล่าสุด (V2.4 · 2026-06-21):**
- **ช่อง "เพิ่มเติม" (1 บล็อก/ใบ)** — ต่อจากรายการเสื้อ ก่อน "รวมทั้งหมด X ตัว" ทั้ง order_form / detail / print:
  - **extra_note** — โน้ตอิสระ แสดงเด่นมาก: **กรอบแดง 2 ชั้น** (outer solid 3px #d00 + inner dashed 2px) พื้น `#fff5f5` ตัวแดง `#d00` หนา ตัวใหญ่ (print 1.9em / detail 1.4em) เตือนคนพิมพ์ให้ระวัง. แสดงเฉพาะเมื่อมีค่า. **แอดมินพิมพ์เน้นย้ำเองในช่องนี้** (เลือกแนวนี้แทน checklist สำเร็จรูป)
  - **ExtraImage** (หลายรูป/ใบ) — multi-slot uploader + วางคลิปบอร์ด (reuse `.paste-image-btn` delegate + pattern เดียวกับรูปมาสเตอร์), ย่ออัตโนมัติ
  - **ExtraNameRow** — ตารางรันชื่อ-เบอร์ (add/remove แถว client-side) + **export CSV** ปุ่มในใบ print → `/order/<id>/extra-csv/` (เข้าโปรแกรม nesting; charset ดู Lessons ข้อ 13)
  - **detail + print แสดง 2 คอลัมน์** (รูปซ้าย / text ขวา; print ใช้ flex-wrap, detail ใช้ Bootstrap row). gate บล็อก = `extra_note OR extra_images OR extra_name_rows`
- **order_form: 3 card ท้ายแยกสีพื้น** (เพิ่มเติม=ฟ้า `#eef5ff` / รูปมาสเตอร์=เขียว `#eefaf0` / รูปที่เซ็นแล้ว=เหลือง `#fdf6e3`) + border-left 4px ให้แยกด้วยตาทันที
- ⚠️ **checklist สำเร็จรูป — เคยทำแล้วถอนออก (อย่าทำซ้ำ):** เคยเพิ่ม `extra_checklist` JSONField + UI 4 ข้อ default ติ๊กได้ แต่แอดมินไม่เอา ขอพิมพ์เน้นใน note เองแทน → ลบทั้งหมด (migration 0017 add แล้ว unapply+ลบไฟล์ → history จบที่ 0016 ตรง prod)

**เพิ่มล่าสุด (V2.5 · 2026-06-25):**
- **field คนออกแบบ + เลขใบงานออกแบบ** (`designer_name` / `design_doc_number`, blank ได้) — คนละคนกับ `created_by`; แสดงใน order_form (ฟิลด์กรอก), detail (panel ซ้าย), print (หัวใบงานเล็กๆ). migration 0017
- **order_list redesign** — โซนด่วนตีกรอบ + ด่วนโชว์ซ้ำ + zebra เขียว + responsive (ดูหัวข้อ list ด้านบน)
- **order_detail layout 2 ฝั่ง** — panel ข้อมูลฟ้า (ซ้าย) + กล่องเงินทองเด่น (ขวา) + แถวเงื่อนไขเต็มกว้างล่าง; gate `is_viewer` เดิม (viewer ไม่เห็นเงิน)
- **order_form polish** — การ์ด "ข้อมูลออร์เดอร์" accent ฟ้า / "รายการเสื้อ" เขียวมิ้นต์ + กล่องเงินทอง (เข้าชุด detail) + โซนข้อมูลจัด 2 ฝั่ง (ฟิลด์ซ้าย/เงิน+คำสั่งพิเศษขวา). **เก็บ id เดิมครบ** (`id_total_price`/`id_deposit`/`remaining-display`/toggle) JS ไม่กระทบ
- **หน้าใหม่ "สรุปใบงานรายวัน"** (`/order/daily-summary/`, view `daily_summary`, `@viewer_or_login_required`) — หัวหน้างานดูออร์เดอร์รวมแต่ละวัน กันตกหล่น:
  - `?date=YYYY-MM-DD` (default วันนี้) + ปุ่มเลื่อนวัน (◀ date-picker ▶ วันนี้) + นับใบ/ตัวของวัน
  - **grid 2 คอลัมน์/ใบ:** รูป thumbnail (รูปแรกของ item แรก) + ข้อมูล (เลข order ลิงก์ detail · ชื่อเสื้อ · total_qty · แหล่ง · คำสั่งพิเศษแดง · ด่วน/ผลิตที่ outsource)
  - badge แหล่ง = กรอบดำพื้นขาว (ประหยัดหมึกตอนพิมพ์)
  - **ปุ่มพิมพ์ A4** + `@media print` (ซ่อน navbar/controls ด้วย `d-print-none`, ไม่ตัด card ข้ามหน้า)
  - navbar เพิ่มปุ่ม "📋 สรุปรายวัน" (staff + viewer)
- **print/detail polish (2026-06-26):**
  - **โน้ตในแบบ (ShirtVariant.note) เด่นขึ้น** — กรอบแดง + พื้น `#fff5f5` + ตัวใหญ่: print/pick ที่ `.variant-note` CSS (ใช้ partial `_variant_block.html` ร่วม), detail ที่กล่อง inline. แสดงเฉพาะมี note
  - **คนออกแบบเด่นใน print** — `.designer-box` กรอบน้ำเงิน ชื่อ 14pt หนา (`#0d47a1`) + เลขใบงานออกแบบในกรอบเดียวกัน
  - **หัวใบงาน print จัดกลาง** — QR + เลขออร์เดอร์ + วันที่ + แหล่ง + คนออกแบบ เป็นกลุ่มกลางหน้า (`.header-spacer`/`.header-main`/`.header-price`) กัน**แม็กมุมซ้ายบังเลขใบงาน**
  - **ตัด icon 🎨 (ออกแบบ) + 📝 (โน้ต) ออกทุกที่** (แอดมินว่าไม่สวย)

**เพิ่มล่าสุด (V2.6 · 2026-07-02):**
- **กรอบรวมต่อ variant (`.variant-total`)** — โชว์เลขล้วน (ตัด "รวม = " ออก) + ลดเหลือ 11pt (จาก 16pt); กรอบ per-box total มาจาก commit 91576a7 (2026-07-01)
- **สถานะ "รอคอนเฟิร์ม" (`waiting_confirm`, migration 0018)** — pattern เดียวกับใบงานด่วน:
  - order_form: checkbox กรอบม่วงวางคู่กรอบแดงใบงานด่วน (flex 2 ช่อง บนสุดของฟอร์ม)
  - print + pick: **overlay ดำโปร่งแสง `rgba(0,0,0,0.45)` คลุมเต็มหน้า** + "รอคอนเฟิร์ม" ขาว 44pt กลางหน้า (flex center, ทั้งจอ + `@media print`) — เคยลองแบบจางทั้งหน้า opacity 0.6 + แถบเทา แล้วเปลี่ยนเป็นโทนดำ
  - print + pick: **ซ่อนปุ่ม action ทั้งหมด** (พิมพ์ใบงาน/บันทึกภาพ/พิมพ์ใบมาสเตอร์/CSV/พิมพ์ใบงานแล้ว/พิมพ์ใบคัด) เหลือแค่ปุ่มกลับ — กันพิมพ์/ผลิตก่อนลูกค้ายืนยัน
  - list: badge ม่วง `#6a1b9a` ผ่าน `_order_row_cells.html` (โชว์ทั้ง 2 โซน)
- **layout ใบงานตามจำนวนกรอบ (print + pick):** ≤2 กรอบ = รูปซ้าย 72:28 / 3+ กรอบ = รูปกลาง 78% + grid 4 ช่อง/แถว (ดูรายละเอียดหัวข้อ Print view ด้านบน) — เช็คแล้วการแบ่งหน้าถูก (`break-inside: avoid` ยกทั้งรายการขึ้นหน้าใหม่ ไม่ตัดกลางกรอบ)

**เพิ่มล่าสุด (V2.7 · 2026-07-15): โปรไฟล์ลูกค้า เฟส 1+1.5**
- **Customer + CustomerPrice + Order.customer FK** (ดู Data Models) — additive ล้วน ใบเก่า 1,257 ใบไม่ถูกแตะ (customer=null)
- **หน้า /customers/** — รายชื่อ+ค้นหา (ชื่อ/ลิงก์/เบอร์) + จำนวนใบ + สั่งล่าสุด + badge ราคา + ปุ่ม "+ เพิ่มลูกค้า" (POST ชื่ออย่างเดียว → เข้าโปรไฟล์ไปเติมต่อ)
- **หน้าโปรไฟล์ /customers/<id>/** — แก้ข้อมูล + ตารางราคา add/remove แถว (wipe-and-recreate) + ประวัติใบงานของลูกค้าคนนั้น (ลิงก์เข้า detail)
- **ฟอร์มใบงาน:** พิมพ์ชื่อลูกค้า → dropdown แนะนำลูกค้าเดิม (แสดงลิงก์/เบอร์/จำนวนราคา) เลือกแล้วเติมชื่อ+ลิงก์+ผูกโปรไฟล์ · ไม่เลือก = server match ชื่อ+ลิงก์เป๊ะ หรือสร้างโปรไฟล์ใหม่ให้เอง (ฐานลูกค้าโตเองจากการใช้งานปกติ)
- **ปุ่มคำนวณราคา:** ลูกค้ามีราคาประจำตัว → ปุ่มราคาโผล่ใต้กล่องเงิน กด = ยอดรวม := จำนวนตัวรวม × ราคา (ไม่บังคับ แก้มือทับได้)
- **แผนเฟสถัดไป (คุยไว้ 2026-07-15):** เฟส 2 = ใบงานชุดเดียวกัน reference กัน (`parent_order` self-FK + ปุ่ม "สร้างใบเพิ่มจากใบนี้" + แบนเนอร์เตือนใน detail/print) · เฟส 3 = เชื่อมระบบ Brief (dr89.cloud/brief — Job.order_ref มีอยู่แล้วรอ UI, ทำ endpoint JSON ฝั่ง Brief + proxy ผ่าน 127.0.0.1:8600 + autocomplete ที่ช่อง design_doc_number) · เฟส 4 = tag/กลุ่มลูกค้า + export CSV · เฟส 5 = dashboard สถิติ

**เพิ่มล่าสุด (V2.8 · 2026-07-16): เฟส 2 ใบงานชุดเดียวกัน**
- **`Order.parent_order`** self-FK (ดู Data Models) — additive, ใบเก่าไม่ถูกแตะ
- **ปุ่ม "➕ สร้างใบเพิ่มจากใบนี้"** ใน detail → `/create/?from=<pk>`: ฟอร์มขึ้นแบนเนอร์ teal "กำลังสร้างใบเพิ่มของชุด X" + prefill ข้อมูลชุดเดิม (แหล่ง/ผลิตที่/ลูกค้า/ชื่องาน/คนออกแบบ/เลขใบงานออกแบบ/spec ผ้า/วิธีรับ — **ไม่ก๊อปเงิน+คำสั่งพิเศษ**) + ผูกโปรไฟล์ลูกค้าเดิม (ปุ่มราคาโชว์ทันที) + hidden `parent_order_id`
- **แบนเนอร์ชุด** detail (ลิงก์คลิกได้ ใบนี้=ตัวหนา) + print (`.group-orders-banner` ใน `.page-a4` — ติดทั้งตอนพิมพ์และปุ่มบันทึกภาพ): "🧩 งานชุดเดียวกัน N ใบ: ..."
- **badge "งานชุด"** (teal `#0c7c92`) ใน `_order_row_cells.html` — โชว์ทั้งใบแรก (มี child) และใบเพิ่ม (มี parent)
- ลบใบแรกของชุด → SET_NULL ใบเพิ่มกลายเป็นใบเดี่ยว (ไม่ cascade)

**เพิ่มล่าสุด (V2.9 · 2026-07-16): เฟส 3 เชื่อมระบบ Brief (dr89.cloud/brief)**
- **ช่อง "เลขใบงานออกแบบ" = autocomplete** — พิมพ์ D-xxx/ชื่อลูกค้า → dropdown ใบงานจากระบบ Brief (เลข·ลูกค้า·สถานะ·รอบ + badge เตือนถ้าใบนั้นมีออร์เดอร์อยู่แล้ว) → เลือก = เติมเลข + ผูก `brief_job_id` · Brief ล่ม/ไม่ตั้ง token → dropdown เงียบ ฟอร์มใช้ได้ปกติ
- **proxy `/order/api/brief-jobs/`** (`brief_jobs_api`, `@login_required`) → เรียก internal API ฝั่ง Brief ผ่าน `BRIEF_API_BASE` (localhost) พร้อม `X-Api-Token` — token ไม่หลุดไป browser. ฝั่ง Brief: `GET /api/jobs/?q=` + `POST /api/jobs/<id>/order-ref/` (token-gated, ดู CLAUDE.md repo Claude-203)
- **ลิงก์สองทาง:** save ใบงานที่ผูก brief_job_id → ยิงเลขออร์เดอร์ไปเซ็ต `Job.order_ref` ฝั่ง Brief อัตโนมัติ (best-effort) · ฝั่ง Brief โชว์ช่อง "เลขออร์เดอร์" + ลิงก์ ↗ กลับมาระบบ Order · detail ฝั่ง Order มีลิงก์ "🎨 เปิดใบงานออกแบบ ↗"
- **settings ใหม่ (.env):** `BRIEF_API_BASE` (default `http://127.0.0.1:8600`) · `BRIEF_API_TOKEN` (shared กับ .env ฝั่ง Brief; ว่าง+DEBUG=false = ปิดฟีเจอร์) · `BRIEF_PUBLIC_BASE` (prod = `https://dr89.cloud/brief`)
- ใบเพิ่ม (เฟส 2) ก๊อป `brief_job_id` จากใบแรกให้ด้วย (hidden field ในฟอร์ม)

**เพิ่มล่าสุด (V3.0 · 2026-07-16): เฟส 4 tag/กลุ่มลูกค้า + export CSV**
- **CustomerTag + Customer.tags M2M** (ดู Data Models) — additive
- **หน้าโปรไฟล์:** การ์ด "🏷️ กลุ่มลูกค้า" — checkbox กลุ่มเดิม + ช่องพิมพ์กลุ่มใหม่ (คั่น comma) save รวมกับฟอร์มเดิม
- **หน้า /customers/:** แถบ chips filter กลุ่ม (+จำนวนคน) ทำงานร่วมค้นหา + คอลัมน์กลุ่ม + ปุ่ม "📥 Export CSV" ตาม filter ปัจจุบัน — ไว้ดึงรายชื่อกลุ่มส่งข่าวส่วนลด/ของขวัญ

**เพิ่มล่าสุด (V3.1 · 2026-07-16): เฟส 5 dashboard สถิติร้าน**
- **tab "📈 สถิติร้าน"** ใน `/reports/` (admin-only เหมือน tab อื่น) — `_report_stats_context()` รวมยอด 12 เดือนล่าสุดใน app layer รอบเดียว (total_qty อยู่ใน JSON sizes นับใน DB ไม่ได้; prefetch ตัด N+1; group ลูกค้าด้วย `customer_name` ข้อความ ให้ครอบคลุมใบเก่าที่ไม่มีโปรไฟล์)
- **เนื้อหา:** การ์ดสรุปเดือนนี้ (ใบ/ตัว/ยอดเงิน/เฉลี่ยต่อใบ) · กราฟแท่งรายเดือน 3 ตัว (ยอดเงิน teal / ใบงาน / จำนวนตัว น้ำเงิน — **คนละกราฟคนละแกน ไม่ทำ dual-axis**) · แหล่งที่มาแท่งแนวนอนเรียงมาก→น้อย · ตารางลูกค้า Top 10 · ตารางรายเดือน (มุมมองตัวเลขของกราฟ)
- **Chart.js 4.4.1 ผ่าน jsdelivr CDN** (ชุดเดียวกับ Bootstrap) + ข้อมูล inject ผ่าน `json_script` — label เดือนแบบไทย "ก.ค. 69" (พ.ศ. 2 หลัก ชุดเดียวกับเลข order)
- **ล็อกด้วยรหัสอีกชั้น (2026-07-17):** เปิด tab สถิติต้องใส่รหัสก่อน (นอกจาก login+admin เพราะเป็นยอดขายรวมร้าน) — `STATS_PIN` ใน .env (default `265424`), เทียบด้วย `constant_time_compare`, ใส่ถูกจำใน `request.session['stats_unlocked']` (หมดตอน logout/session หมดอายุ; แต่ละ browser ใส่ครั้งเดียว)

### 🔜 ค้าง / อนาคต
- [ ] merge tool ลูกค้าซ้ำ (ตอนนี้กันซ้ำด้วย match ชื่อ+ลิงก์เป๊ะ + autocomplete เท่านั้น)
- [ ] **Task system V1** — ระบบงาน/มอบหมายงาน (ยังไม่เริ่ม)
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

12. **`git hash-object` MISMATCH หลัง scp บน Windows = false alarm** — Windows checkout เป็น CRLF + บางไฟล์มี BOM, แต่ VPS เก็บดิบ → `git hash-object` ฝั่ง Windows ทำ CRLF→LF normalization เลยได้ hash ต่างจาก VPS **ทั้งที่เนื้อไฟล์เหมือนกัน** (scp คัดลอก byte-exact). อย่าด่วนสรุปว่า "VPS ถูกแก้มือ/scp พัง" — ยืนยันด้วย `git diff --no-index --ignore-all-space` (ดูเนื้อจริง) หรือ semantic check (grep โค้ดที่เพิ่ม / `makemigrations --check`) ก่อนตัดสิน. **อย่าใช้ PowerShell `Out-File`/pipe สร้างไฟล์ที่ python จะ exec** — มันเติม BOM (U+FEFF) ทำ SyntaxError. *(เทียบ hash VPS↔local ให้ `tr -d '\r'` ก่อน sha256sum ทั้งสองฝั่ง = ตัด false alarm CRLF)*

13. **CSV ภาษาไทยใน Django: ใช้ `charset=utf-8` + เขียน `﻿` เอง 1 ครั้ง — ห้าม `utf-8-sig`** — `HttpResponse` re-encode **ทุก** `resp.write()` ด้วย charset ของ response. ถ้าตั้ง `content_type='text/csv; charset=utf-8-sig'` → codec แปะ BOM หน้า **ทุก** chunk (BOM หน้าทุกแถว CSV) → ไฟล์เสีย เปิด Excel เพี้ยน. วิธีถูก: `content_type='text/csv; charset=utf-8'` แล้ว `resp.write('﻿')` เองครั้งเดียวก่อนเขียน header (BOM ตัวเดียวต้นไฟล์ ให้ Excel/โปรแกรม nesting อ่านไทยถูก). ใช้ใน `order_extra_csv`

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
- ช่อง "เพิ่มเติม" reuse pattern เดิม: `ExtraImage` เลียนแบบ `MasterImage` (multi-slot + paste + downscale), `ExtraNameRow` save แบบ wipe-and-recreate จาก parallel arrays (เหมือนไม่มี formset)
- **เน้นย้ำคนพิมพ์ใช้ note อิสระ (กรอบแดง 2 ชั้น) ไม่ใช่ checklist สำเร็จรูป** — เคยทำ checklist (4 ข้อ default ติ๊กได้) แล้วแอดมินไม่เอา เพราะงานจริงหลากหลายเกินจะ fix เป็นข้อๆ พิมพ์เองยืดหยุ่นกว่า → ถอดออก (history จบที่ migration 0016)
- list เดียว urgent-first (ไม่มี tab) — เคยลอง 2 tab แล้ว revert (แอดมินไม่เอา)

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
