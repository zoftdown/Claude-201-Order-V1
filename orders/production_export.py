# -*- coding: utf-8 -*-
"""Export ใบออร์เดอร์เป็น JSON "ส่งเข้าผลิต" — เฉพาะเสื้อคนงาน (source=เพจเสื้อคนงาน).

ปุ่มอยู่หน้า order_detail → JS ฝั่ง browser เอา payload ที่ view สร้างจากที่นี่
(inject ผ่าน json_script) ไปทำไฟล์ <เลขใบงานออกแบบ>-order.json ให้ download.
คอ/แขน เป็นช่องพิมพ์อิสระ → normalize ที่นี่; ค่าที่ไม่เข้า pattern ไหนเลย
จะติด flag ใน `uncertain` ให้ JS เปิด popup ถามคนก่อน export.

โมดูลนี้อ่านอย่างเดียว ไม่แตะ DB / ใบงาน A4 / ใบมาสเตอร์.
"""

# ไซส์มาตรฐานที่ต้องมีใน sizes เสมอ (ค่า 0 ถ้าไม่มีในแบบ) — ชุดเดียวกับ
# DEFAULT_SIZES ในฟอร์ม; label อื่น (5XL, เด็ก S, ...) ต่อท้ายตามที่กรอกจริง
STANDARD_SIZE_KEYS = ['S', 'M', 'L', 'XL', '2XL', '3XL', '4XL']

WORKER_SOURCE = 'เพจเสื้อคนงาน'

COLLAR_CHOICES = ['round', 'v', 'polo']
SLEEVE_CHOICES = ['short', 'long']


def normalize_collar(text):
    """(value, confident) — 'วี'/'V' → v · 'โปโล'/'polo' → polo · 'กลม'/'round' → round.
    ไม่เข้า pattern ไหนเลย (เช่น คอกีฬา, ค่าว่าง) → เดา round แต่ confident=False
    ให้ popup ถามคนยืนยันก่อน."""
    t = (text or '').strip().lower()
    # เช็ค วี ก่อนเสมอ: "คอปกวี"/"ปกวี"/"คอปก+วี" ต้องได้ v มั่นใจ ไม่ติด uncertain —
    # pattern ผลิตใช้คอวีแล้วเย็บปกเพิ่มหน้างาน (ใบ A4 ยังโชว์ข้อความดิบตามที่กรอก
    # ให้ช่างเย็บรู้ว่าใส่ปก — normalize มีผลแค่ payload ส่งผลิต). ห้ามเพิ่ม pattern
    # "ปก" → polo หรือสลับลำดับเช็ค ไม่งั้นคอปกวีจะกลายเป็น polo เงียบๆ
    if 'วี' in t or 'v' in t:
        return 'v', True
    if 'โปโล' in t or 'polo' in t:
        return 'polo', True
    if 'กลม' in t or 'round' in t:
        return 'round', True
    return 'round', False


def normalize_sleeve(text):
    """(value, confident) — 'สั้น'/'short' → short · 'ยาว'/'long' → long.
    อื่นๆ (แขนกุด, ค่าว่าง) → เดา long แต่ confident=False ให้ popup ถามคน."""
    t = (text or '').strip().lower()
    if 'สั้น' in t or 'short' in t:
        return 'short', True
    if 'ยาว' in t or 'long' in t:
        return 'long', True
    return 'long', False


def _sizes_dict(variant):
    """แปลง sizes JSON list ของ variant → dict {label: qty} โดยมีไซส์มาตรฐาน
    ครบทุกตัว (0 ถ้าไม่มี). label มาตรฐานพิมพ์เล็ก/ช่องว่างเพี้ยน → รวมเข้า
    ตัวมาตรฐาน; label อื่นคงตามที่กรอก. label ซ้ำ → บวก qty รวมกัน."""
    out = {k: 0 for k in STANDARD_SIZE_KEYS}
    for s in (variant.sizes or []):
        if not isinstance(s, dict):
            continue
        label = (s.get('label') or '').strip()
        if not label:
            continue
        if label.upper() in out:
            label = label.upper()
        try:
            qty = int(s.get('qty') or 0)
        except (TypeError, ValueError):
            qty = 0
        out[label] = out.get(label, 0) + qty
    return out


def _safe_filename_stem(text):
    """กันอักขระที่ใช้ในชื่อไฟล์ไม่ได้ (/, \\, :, ...) — แทนด้วย _"""
    bad = '\\/:*?"<>|'
    return ''.join('_' if c in bad else c for c in text.strip())


def build_production_export(order):
    """คืน dict {payload, uncertain, filename} สำหรับ inject ลงหน้า detail.

    - payload: JSON ตาม spec โปรแกรมผลิต (items = ทุก ShirtVariant ของทุก
      OrderItem เรียงตามลำดับ; collar/sleeve ใส่ค่าเดาไว้แล้วแม้ไม่มั่นใจ)
    - uncertain: [{index, field, raw, guess}] — แบบที่ normalize ไม่มั่นใจ
      (JS เปิด popup ให้คนเลือกทับค่าใน payload ก่อน download)
    - filename: "<เลขใบงานออกแบบ>-order.json" (design ว่าง → JS เตือน ไม่ export)
    """
    design = (order.design_doc_number or '').strip()
    items = []
    uncertain = []
    for oitem in order.items.all():
        # ออร์เดอร์หลายลาย: design ต่อ item = เลขของรายการเอง (ถ้ากรอก) ไม่งั้น
        # เลขระดับใบ — ระดับบนยังส่ง "design" เดิมเพื่อ backward compat
        item_design = oitem.effective_design_doc
        for variant in oitem.variants.all():
            idx = len(items)
            collar, collar_ok = normalize_collar(variant.collar)
            sleeve, sleeve_ok = normalize_sleeve(variant.sleeve)
            if not collar_ok:
                uncertain.append({'index': idx, 'field': 'collar',
                                  'raw': variant.collar, 'guess': collar})
            if not sleeve_ok:
                uncertain.append({'index': idx, 'field': 'sleeve',
                                  'raw': variant.sleeve, 'guess': sleeve})
            items.append({
                'design': item_design,
                'collar': collar,
                'sleeve': sleeve,
                'color': variant.color,
                'pocket': bool(variant.pocket),
                'sizes': _sizes_dict(variant),
            })

    payload = {
        'order': order.order_number,
        'design': design,
        'type': 'worker',
        'customer': order.customer_name,
        'fabric': order.fabric_spec,
        'note': order.special_note,
        'items': items,
    }
    return {
        'payload': payload,
        'uncertain': uncertain,
        'filename': f'{_safe_filename_stem(design)}-order.json' if design else '',
        # ไม่ได้ติ๊ก "จากโปรแกรม Mockup" → JS เตือนว่าอาจไม่มีไฟล์ zip ก่อน export
        # (เตือนอย่างเดียว ไม่ block; ไม่ใส่ใน payload เพราะโปรแกรมผลิตไม่รู้จัก field นี้)
        'from_mockup': bool(order.from_mockup),
    }
