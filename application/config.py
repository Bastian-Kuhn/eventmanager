"""
Configuration
"""
#pylint: disable=too-few-public-methods, anomalous-backslash-in-string
import os
import secrets

class BaseConfig():
    """
    Generel System white Configuration.
    Can be overwritten later if needed.
    """
    RELEASE = '1.0.0'
    ENVIRONMENT = "DEBUG"

    ENABLE_SENTRY = False

    SECRET_KEY = "NICHTGEHEIMN"

    # If true, only Useres with the flag Admin can login on the website
    ADMIN_LOGIN_ONLY = False
    # Minimum length for user Passwords (not applied to admin panel)
    PASSWD_MIN_PASSWD_LENGTH = 8
    # Password needs special signs
    PASSWD_SPECIAL_CHARS = True
    # Password need numbers
    PASSWD_SPECIAL_DIGITS = False
    # There must be uppercase letters
    PASSWD_SEPCIAL_UPPER = False
    # There musst lowercase letters
    PASSWD_SEPCIAL_LOWER = True
    # How many of the PASSWD_SEPCIAL prefixt  options must apply
    PASSWD_SPECIAL_NEEDED = 1

    ADMIN_SESSION_HOURS = 8
    PERMANENT_SESSION_LIFETIME = ADMIN_SESSION_HOURS * 60 * 60
    SECURITY_MSG_LOGIN_MESSAGE = False

    BOOTSTRAP_SERVE_LOCAL = True
    BABEL_DEFAULT_LOCALE = "de"
    TIMEZONE = os.getenv("TZ", "Europe/Berlin")
    REQUEST_ID_UNIQUE_VALUE_PREFIX = "evenmng"

    MAIL_SENDER = ''
    MAIL_SERVER = ''
    MAIL_USE_TLS = True
    MAIL_USERNAME = ''
    MAIL_SUBJECT_PREFIX = ''
    MAIL_PASSWORD = ''

    REDIS_URL = "redis://localhost:6379"
    MONGODB_HOST = '127.0.0.1'
    SWAGGER_ENABLED = True

    SENTRY_DSN = ""

    SITENAME = "Eventmanager"
    MONGODB_DB = 'eventmanger'
    MONGODB_ALIAS = 'default'

class DockerBaseConfig(BaseConfig):
    """"
    Specific configuration for Docker Env
    """
    #SECRET_KEY = secrets.token_urlsafe(48)
    APPLY_HEADERS = False
    TEMPLATE_AUTO_RELOAD = False
    MONGODB_HOST = '172.17.0.1'
    REDIS_URL = "redis://172.17.0.1:6379"

class ComposeProdConfig(DockerBaseConfig):
    """
    Paths for Docker Compose
    """
    REDIS_URL = 'redis://redis:6379'
    MONGODB_HOST = 'mongo'
    MONGODB_PORT = 27017

class ProductionConfig(BaseConfig):
    """
    Production Configuration.
    """
    SECRET_KEY = secrets.token_urlsafe(48)
    ENVIRONMENT = "Prod"
    SESSION_COOKIE_SECURE = True
    ENABLE_SENTRY = True
    SWAGGER_ENABLED = False
    DEBUG = False # And should be False
