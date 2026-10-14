"""
Django settings for config project.
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Environment-based settings ---
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-dev-only-key-change-in-production')
DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1', 'yes')
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

CSRF_TRUSTED_ORIGINS = os.environ.get(
    'CSRF_TRUSTED_ORIGINS',
    'https://dr89.cloud'
).split(',')

# --- Sub-path deployment (dr89.cloud/order/) ---
FORCE_SCRIPT_NAME = os.environ.get('FORCE_SCRIPT_NAME', '')  # '/order' in production

# --- เฟส 3: เชื่อมระบบ Brief (dr89.cloud/brief) ---
# BRIEF_API_BASE: ที่อยู่ internal ของ gunicorn ฝั่ง Brief (nginx ตัด /brief/ ให้
#   แล้ว path ภายในจึงไม่มี prefix) — dev ใช้ runserver 8600 ได้เลย
# BRIEF_API_TOKEN: shared token (ค่าเดียวกับ .env ฝั่ง Brief) — ว่าง = ปิดฟีเจอร์
# BRIEF_PUBLIC_BASE: URL ที่ browser ใช้เปิดใบงานออกแบบ (prod = https://dr89.cloud/brief)
BRIEF_API_BASE = os.environ.get('BRIEF_API_BASE', 'http://127.0.0.1:8600')
BRIEF_API_TOKEN = os.environ.get('BRIEF_API_TOKEN', '')
BRIEF_PUBLIC_BASE = os.environ.get('BRIEF_PUBLIC_BASE', 'http://127.0.0.1:8600')

# รหัสเข้าหน้า "สถิติร้าน" (tab stats ใน /reports/) — ชั้นเพิ่มจาก login/admin
# เพราะเป็นข้อมูลยอดขายรวมของร้าน. ใส่ถูกครั้งเดียวจำใน session.
STATS_PIN = os.environ.get('STATS_PIN', '265424')

# --- LINE Messaging API (app line_bot — webhook ที่ /linebot/webhook/) ---
# ทั้งคู่ได้จาก LINE Developers console > Messaging API; ว่าง = ลายเซ็นไม่ผ่าน/ไม่ reply
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
# userId ของแอดมินที่บอทจะ mention ตอนแจ้งผลอ่านใบงาน (comma-separated; ว่าง = ใช้ "@แอดมิน" ธรรมดา)
# เก็บ userId ได้จาก log 'line_bot' (INFO) ทุก event จะพิมพ์ groupId/userId ของผู้ส่ง
LINE_ADMIN_USER_IDS = [
    x.strip() for x in os.environ.get('LINE_ADMIN_USER_IDS', '').split(',') if x.strip()
]

# LLM อ่านเลขออร์เดอร์จากรูปใบงาน (RunningHub, OpenAI-compatible API) — ว่าง = ปิดการอ่านรูป
RUNNINGHUB_API_KEY = os.environ.get('RUNNINGHUB_API_KEY', '')
RUNNINGHUB_BASE_URL = os.environ.get('RUNNINGHUB_BASE_URL', 'https://llm.runninghub.ai/v1')
RUNNINGHUB_MODEL = os.environ.get('RUNNINGHUB_MODEL', 'qwen/qwen3.8-flash-next')

# --- Authentication redirects ---
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'order_list'
LOGOUT_REDIRECT_URL = 'login'

# ปิด browser แล้ว session หลุด — ต้อง login ใหม่ทุกครั้งที่เปิดเครื่อง/เปิด browser ใหม่
# (sessionid เป็น session cookie ไม่ตั้ง Expires/Max-Age; ไม่แตะ cookie แผนกผลิต
# ซึ่งเป็น cookie แยกของ DepartmentPIN)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'orders',
    'line_bot',  # label 'linebot' (ชื่อโฟลเดอร์เลี่ยงชนกับ package linebot ของ SDK)
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # no-store ทุกหน้า dynamic (กัน browser โชว์หน้าเก่าตอนกด Back) —
    # ต้องอยู่ใต้ WhiteNoise เพื่อไม่แตะ static files
    'orders.middleware.NoStoreCacheMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# --- Database ---
if os.environ.get('DATABASE_URL') or os.environ.get('DB_NAME'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'order_db'),
            'USER': os.environ.get('DB_USER', 'order_user'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# --- Cache ---
# DatabaseCache แทน LocMem (default) เพราะ gunicorn รัน 2 worker — บอท LINE เก็บ "รายการรอ"
# (รูปใบงานที่อ่านเลขได้ รอข้อความยืนยัน) ไว้ใน cache ต้องเห็นกันข้าม worker.
# ตารางสร้างด้วย `manage.py createcachetable` (ไม่ใช่ migration) — ต้องรันบน VPS ตอน deploy
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'th'
TIME_ZONE = 'Asia/Bangkok'
USE_I18N = True
USE_TZ = True

# --- Static files ---
STATIC_URL = (FORCE_SCRIPT_NAME or '') + '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# --- Media files ---
MEDIA_URL = (FORCE_SCRIPT_NAME or '') + '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Production security ---
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'

# --- Logging: log ของ line_bot (INFO) ออก stderr → gunicorn → journalctl -u order ---
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '[%(asctime)s] %(levelname)s %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
    },
    'loggers': {
        'line_bot': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}
