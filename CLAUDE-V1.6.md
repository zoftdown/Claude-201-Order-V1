# Phase 1.6 — Production Stage Tracking + QR Update

> **Goal:** ให้ฝ่ายผลิตอัปเดทสถานะงานได้ผ่านการสแกน QR บนใบงาน โดยไม่ต้อง login จริง — ใช้ "เลือกแผนกครั้งเดียวแล้วจำใน cookie"

---

## 1. Auth Model (Hybrid)

ระบบ Phase 1.6 มี 2 channel access แยกกัน:

| Channel | กลุ่มผู้ใช้ | วิธี auth |
|---|---|---|
| **Office** | Admin (2-3 คน นอกร้าน), Staff (ในร้าน) | Django login (จาก Phase 1.5) |
| **Production** | ฝ่ายผลิตในร้าน ~7-8 คน | เลือกแผนก → cookie 1 ปี (ไม่ต้อง login) |

### Production access flow

1. พนักงานเปิด `/order/<order_number>/update/` ครั้งแรก (จากการสแกน QR)
2. ถ้ายังไม่มี cookie `production_dept` → redirect ไปหน้า `/order/select-department/`
3. หน้านั้นแสดงปุ่มใหญ่ๆ 6 ปุ่ม (พิมพ์ / โรล / ตัด / คัด / ส่งเย็บ / รีด+แพ็ค)
4. กดเลือก → set cookie `production_dept=<slug>` อายุ 365 วัน → redirect กลับ
5. ครั้งต่อไปสแกน QR → เข้าหน้า update ตรงๆ ไม่ต้องเลือกอีก

### URL guard

- หน้า `/order/<n>/update/` และ `/order/dept/<slug>/` → ตรวจ cookie ก่อน, ไม่มีก็ redirect
- หน้า list/create/edit/admin → ใช้ Django login (Phase 1.5)
- มีปุ่ม "เปลี่ยนแผนก" ในมุมหน้า update เผื่อกดเลือกผิด

### ข้อจำกัดที่ผู้ใช้รับทราบ

- ไม่มีการระบุตัวผู้กด → log บันทึกแค่ **แผนก** + **timestamp**
- มือถือร้านที่ใช้รวมกันก็ใช้ cookie แผนกเดียว ไม่มีปัญหา
- ความปลอดภัยพึ่ง: URL ของระบบไม่หลุด + QR อยู่บนใบงานเท่านั้น

---

## 2. Workflow & Stages

ลำดับ stage หลัก (เส้นตรง):

```
1. พิมพ์  →  2. โรล  →  3. ตัด  →  4. คัด  →  5. ส่งเย็บ  →  6. รีด+แพ็ค
```

### Action ที่กดได้ในแต่ละ stage

| Stage | slug | Action ปุ่ม | ผลที่เกิด |
|---|---|---|---|
| 1. พิมพ์ | `print` | **พิมพ์เสร็จ** | set `print_done_at` |
| | | **ซ่อมเสร็จ** (โผล่เมื่อ `needs_repair=True`) | clear `needs_repair`, set `repair_done_at` |
| 2. โรล | `roll` | **โรลเสร็จ** | set `roll_done_at` |
| 3. ตัด | `cut` | **ตัดเสร็จ** | set `cut_done_at` |
| 4. คัด | `sort` | **ครบ** | set `sort_done_at` |
| | | **ส่งซ่อม** | set `needs_repair=True`, log timestamp |
| 5. ส่งเย็บ | `sew` | **ส่งให้ <คนเย็บ>** (เลือก checkbox หลายชื่อ) | set `sent_to_tailors_at`, save tailor list |
| 6. รีด+แพ็ค | `pack` | **รีดแพ็คแล้ว** | set `packed_at` |
| | | **ส่งแล้ว** (โผล่หลัง packed) | set `shipped_at` |
| | | **รอมารับ** (โผล่หลัง packed) | set `awaiting_pickup_at` |

### Flow ซ่อม (loop กลับ)

```
[คัด] → กด "ส่งซ่อม" → needs_repair = True
                          ↓
                    [คิวที่ฝ่ายพิมพ์]
                          ↓
                  ฝ่ายพิมพ์กด "ซ่อมเสร็จ"
                          ↓
              needs_repair = False, กลับเข้า stage [คัด]
```

หมายเหตุ: รายละเอียด "ขาดอะไร" **ยังไม่เก็บใน Phase 1.6** — กดแค่ flag ว่ามีซ่อมก็พอ

---

## 3. Data Model Changes

### 3.1 เพิ่ม Model `Tailor` (คนเย็บ outsource)

```python
class Tailor(models.Model):
    name = models.CharField(max_length=100)        # ชื่อเล่น/ชื่อจริง
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)  # เลิกใช้ก็ปิด
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
```

- จัดการผ่าน Django admin เท่านั้น (admin/staff เพิ่ม/ลบ)
- คาดว่ามี 6-7 คน

### 3.2 เพิ่ม fields ใน Order

```python
# stage timestamps (null = ยังไม่เสร็จ stage นั้น)
print_done_at        = models.DateTimeField(null=True, blank=True)
roll_done_at         = models.DateTimeField(null=True, blank=True)
cut_done_at          = models.DateTimeField(null=True, blank=True)
sort_done_at         = models.DateTimeField(null=True, blank=True)
sent_to_tailors_at   = models.DateTimeField(null=True, blank=True)
packed_at            = models.DateTimeField(null=True, blank=True)
shipped_at           = models.DateTimeField(null=True, blank=True)
awaiting_pickup_at   = models.DateTimeField(null=True, blank=True)

# repair flag
needs_repair         = models.BooleanField(default=False)
repair_done_at       = models.DateTimeField(null=True, blank=True)  # latest repair completion

# tailors (M2M เพราะ 1 order ส่งหลายเจ้าได้)
tailors              = models.ManyToManyField(Tailor, blank=True, related_name='orders')
```

### 3.3 เพิ่ม Model `StageLog` (audit trail)

เก็บประวัติทุกการกดปุ่ม เผื่อต้องไล่หลัง

```python
class StageLog(models.Model):
    order      = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='stage_logs')
    department = models.CharField(max_length=20)  # 'print', 'roll', 'cut', 'sort', 'sew', 'pack'
    action     = models.CharField(max_length=30)  # 'print_done', 'sort_repair', 'pack_shipped', etc.
    note       = models.TextField(blank=True)     # เผื่อใช้ future
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
```

> ไม่เก็บ user เพราะระบบไม่ระบุตัวตน — รู้แค่แผนก

### 3.4 เพิ่ม `schema_version` (จาก Phase 1.5)

ใช้แยก legacy order — ออเดอร์เก่าไม่มี stage tracking ให้ display เป็นแบบเดิม

---

## 4. URLs & Views

### URL pattern ใหม่

```
/order/select-department/         # หน้าเลือกแผนก (set cookie)
/order/<order_number>/update/     # หน้าอัปเดทสถานะ (สแกน QR มา)
/order/dept/<slug>/                # dashboard ของแผนก
/order/clear-department/           # ล้าง cookie (debug/เปลี่ยนแผนก)
```

### หน้า `/order/select-department/`

- 6 ปุ่มใหญ่ๆ เต็มจอ (mobile-first): พิมพ์, โรล, ตัด, คัด, ส่งเย็บ, รีด+แพ็ค
- กดแล้ว set cookie + redirect ไป `?next=` ถ้ามี ไม่งั้นไป dept dashboard

### หน้า `/order/<n>/update/`

- ส่วนบน: ข้อมูลย่อ order (เลข, ชื่อลูกค้า, ชื่องาน, รูปดีไซน์ย่อ)
- ส่วนกลาง: **ปุ่มของแผนกตัวเอง**เท่านั้น (อ่าน cookie)
  - แสดง action ที่กดได้ตาม state ปัจจุบัน เช่น
    - แผนกพิมพ์: ถ้ายังไม่กด `print_done` → ปุ่ม "พิมพ์เสร็จ"
    - แผนกพิมพ์: ถ้า `needs_repair=True` → ปุ่ม "ซ่อมเสร็จ" (โผล่เพิ่ม)
    - แผนกคัด: ปุ่ม "ครบ" + ปุ่ม "ส่งซ่อม" (กดได้ตลอดถ้ายังไม่ sort_done)
- ส่วนล่าง: timeline เล็กๆ แสดง stage ไหนเสร็จเมื่อไหร่ (เห็นเฉพาะ stage ก่อนหน้า)
- ปุ่ม "เปลี่ยนแผนก" มุมขวาบน

### หน้า `/order/dept/<slug>/`

ตามที่ตกลง dashboard เห็น 3 อย่าง:

1. **งาน stage ของฉันที่ยังไม่เสร็จ** — list orders ที่ stage ก่อนหน้าเสร็จแล้ว แต่ stage ฉันยัง
   - เช่น dept=roll → เห็น orders ที่ `print_done_at IS NOT NULL AND roll_done_at IS NULL`
2. **งานซ่อม** (เฉพาะแผนกพิมพ์) — orders ที่ `needs_repair=True`
3. **จำนวนงานรอรวมทั้งร้าน** — counter ทุก stage ในแถบบน

แต่ละแถวคลิกได้ → ไปหน้า update

---

## 5. QR Code บนใบงาน

### Library

ใช้ `qrcode` (Python) — ติดตั้งเพิ่มใน `requirements.txt`:

```
qrcode[pil]>=7.4
```

### การ generate

ในหน้า print view:
- generate QR เป็น SVG inline (ไม่ต้องเก็บไฟล์)
- URL: `https://dr89.cloud/order/<order_number>/update/`
- ขนาด ~3x3 cm มุมขวาบนใบงาน
- error correction level: M (พอสำหรับใบกระดาษ)

### Code pattern

```python
import qrcode
import qrcode.image.svg

def generate_qr_svg(order_number, base_url):
    url = f"{base_url}/order/{order_number}/update/"
    factory = qrcode.image.svg.SvgImage
    img = qrcode.make(url, image_factory=factory, box_size=10, border=2)
    return img.to_string(encoding='unicode')
```

template ใส่ `{{ qr_svg|safe }}` ในมุมขวาบน

---

## 6. UX / UI Notes

### หน้า update (mobile-first)

- ปุ่ม action ขนาดใหญ่ (min-height 60px) แตะง่ายตอนใส่ถุงมือ
- สีปุ่มแยก:
  - ปุ่ม "เสร็จ" → เขียว
  - ปุ่ม "ส่งซ่อม" → แดง
  - ปุ่ม secondary (ส่งแล้ว/รอมารับ) → ฟ้า
- กดแล้วมี toast แจ้ง + auto refresh หลัง 1 วินาที
- ปุ่ม disable + greyed out ถ้ากดไปแล้ว (กันกดซ้ำ)

### หน้าเลือกแผนก

- ออกแบบเป็น grid 2 columns × 3 rows บนมือถือ
- ใช้ icon + ชื่อแผนกตัวใหญ่
- background สีอ่อนๆ ของแต่ละแผนก เพื่อให้จำง่าย

### Dashboard แผนก

- เปิดได้จาก URL ตรง หรือลิงก์มุมหน้า update
- แสดง list แบบ card — เลข order + ชื่อลูกค้า + เวลารอ
- เรียงตาม urgency (รอนานสุดอยู่บน)

### Print view

- QR มุมขวาบน, ขนาด ~3x3 cm
- ข้างๆ QR เขียน "สแกนเพื่ออัปเดทสถานะ"

---

## 7. Implementation Roadmap

แบ่งเป็น sub-step เพื่อให้ทดสอบทีละชิ้น:

### Step 1: Foundation (~1-2 ชั่วโมง)
- [ ] เพิ่ม `qrcode` ใน requirements.txt
- [ ] สร้าง model `Tailor` + migration
- [ ] เพิ่ม stage timestamp fields ใน Order + migration
- [ ] สร้าง model `StageLog` + migration
- [ ] register `Tailor` ใน Django admin

### Step 2: Department selection (~1 ชั่วโมง)
- [ ] view + template `/order/select-department/`
- [ ] middleware/decorator ตรวจ cookie + redirect
- [ ] หน้า `/order/clear-department/` สำหรับ debug

### Step 3: Update view (~2-3 ชั่วโมง)
- [ ] view + template `/order/<n>/update/`
- [ ] business logic: เช็ค state → แสดงปุ่มที่กดได้
- [ ] handler รับ POST จากปุ่ม → update field + write StageLog
- [ ] toast feedback + auto refresh
- [ ] ส่วนแสดง tailor selection (multi-checkbox) สำหรับ stage ส่งเย็บ

### Step 4: Dashboard (~2 ชั่วโมง)
- [ ] view + template `/order/dept/<slug>/`
- [ ] query งานที่รอ stage นี้
- [ ] query งานซ่อม (เฉพาะแผนกพิมพ์)
- [ ] counter รวมทุก stage

### Step 5: QR on print view (~30 นาที)
- [ ] เพิ่ม helper `generate_qr_svg`
- [ ] แก้ template print view ใส่ QR

### Step 6: Testing & polish (~1-2 ชั่วโมง)
- [ ] test local ด้วย ngrok → สแกน QR ด้วยมือถือจริง
- [ ] ลองทุก flow รวม flow ซ่อม
- [ ] ตรวจ mobile responsive
- [ ] deploy VPS

**รวมประมาณ 8-10 ชั่วโมง** — ทำเป็น session ใหญ่ๆ 2-3 รอบ

---

## 8. Open Questions / Future Phase

- **Phase 1.7 (อนาคต):** เปิดให้คนเย็บ outsource login เข้าระบบเอง อัปเดทสถานะรับ/ส่งคืน
- **Phase 1.7+:** track รายละเอียด "ขาดชิ้นไหน" ตอนส่งซ่อม (เพิ่ม repair_note field)
- **Phase 2:** stat "งานเฉลี่ยใช้เวลากี่ชั่วโมงต่อ stage", "งานไหนซ่อมบ่อย"
- **Phase 2:** ส่งแจ้งเตือน LINE/SMS เมื่อสถานะเปลี่ยน
- **ความปลอดภัย:** ถ้าโดน abuse หนักๆ พิจารณาเปลี่ยนเป็น login จริง

---

## 9. Decisions Log (สำหรับ Phase 1.6)

- ไม่ระบุตัวผู้กดปุ่ม → ลด UX friction, log แค่แผนก
- โรล กับ คัด แยก stage (จากเดิมเข้าใจว่ารวม)
- ส่งเย็บ เก็บแค่ "ส่งให้ใครบ้าง" — ไม่ต้องเก็บการรับคืน (Phase 1.6)
- ซ่อม ไม่เก็บ note → กด flag อย่างเดียว
- รีด+แพ็ค มี 3 action: รีดแพ็คแล้ว / ส่งแล้ว / รอมารับ (รีดแพ็คก่อนแล้วเลือก 1 ใน 2)
- Cookie `production_dept` อายุ 365 วัน — long enough ไม่ต้องเลือกบ่อย
- QR ใช้ `qrcode` library generate inline SVG — ไม่ต้องเก็บไฟล์
- StageLog ไม่ผูก user → ระบบ anonymous ตามที่ตั้งใจ
