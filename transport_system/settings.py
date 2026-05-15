"""
Django settings for transport_system project.
"""

import os
from importlib.util import find_spec
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

try:
    import dj_database_url
except ImportError:  # pragma: no cover - optional locally until deployment deps are installed
    dj_database_url = None

HAS_WHITENOISE = find_spec("whitenoise") is not None

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def env_int(name, default=0):
    value = os.getenv(name, str(default)).strip()
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-x*a(pwffis2o7z6mc(gm+#$y6-s^3lhw1gv&9_z!7e)g(@1=s-",
)

DEBUG = env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    "127.0.0.1,localhost,testserver",
)


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'camions',
    'chauffeurs',
    'dashboard',
    'clients',
    'prospects',
    'commandes',
    'livraisons',
    'maintenance',
    'documents',
    'operations',
    'depenses',
    'utilisateurs',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if HAS_WHITENOISE:
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

ROOT_URLCONF = 'transport_system.urls'

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
                'utilisateurs.context_processors.user_access',
            ],
        },
    },
]

WSGI_APPLICATION = 'transport_system.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

database_url = os.getenv("DATABASE_URL")
if database_url and dj_database_url:
    DATABASES["default"] = dj_database_url.parse(
        database_url,
        conn_max_age=600,
        ssl_require=not DEBUG,
    )
elif not DEBUG:
    raise ImproperlyConfigured("DATABASE_URL est obligatoire en production.")


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'fr-fr'

TIME_ZONE = os.getenv('DJANGO_TIME_ZONE', 'Africa/Conakry')

USE_I18N = True

USE_TZ = True

USE_THOUSAND_SEPARATOR = True
THOUSAND_SEPARATOR = " "
NUMBER_GROUPING = 3
DECIMAL_SEPARATOR = ","


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
if HAS_WHITENOISE:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
CSRF_FAILURE_VIEW = "transport_system.error_views.csrf_failure"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 3600)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)

LOGIN_URL = "/comptes/connexion/"
LOGIN_REDIRECT_URL = "/"
