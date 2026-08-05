"""Middleware ของแอป orders."""


class NoStoreCacheMiddleware:
    """ส่ง Cache-Control: no-store ให้ทุกหน้า dynamic

    กันปัญหา Chrome คืนหน้าเก่าจาก back/forward cache (กด Back จากหน้าแก้ไข
    กลับมาหน้า list แล้วเห็นคำสั่งพิเศษ/badge ค่าเก่า จนกว่าจะกด F5 เอง)
    no-store ทำให้ browser ยิง request ใหม่เสมอ ไม่เก็บ snapshot

    ต้องอยู่ใต้ WhiteNoiseMiddleware ใน MIDDLEWARE — WhiteNoise ตอบ static
    ก่อนถึงตัวนี้ ไฟล์ static จึงยัง cache ได้ตามเดิม (media ใน prod ก็ไม่ผ่าน
    Django อยู่แล้ว nginx serve ตรง)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not response.has_header('Cache-Control'):
            response['Cache-Control'] = 'no-store'
        return response
