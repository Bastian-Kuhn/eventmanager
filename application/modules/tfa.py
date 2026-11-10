"""
Zwei-Faktor-Anmeldung per TOTP (Authenticator-App) und Wiederherstellungscodes
"""
import base64
import hashlib
import hmac
import re
import secrets
import time

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

ISSUER = "Tourenportal"
RECOVERY_CODE_COUNT = 10
# Codes aus dem vorigen und naechsten 30-Sekunden-Fenster gelten auch (Uhrabweichung)
VALID_WINDOW = 1


def _fernet():
    """
    Schluessel aus dem SECRET_KEY der App. Aendert sich der SECRET_KEY, lassen
    sich die gespeicherten Geheimnisse nicht mehr lesen: dann hilft nur das
    Zuruecksetzen der 2FA durch einen Admin.
    """
    key = hashlib.sha256(b"tfa-secret|" + current_app.config['SECRET_KEY'].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(secret):
    """TOTP-Geheimnis verschluesselt fuer die Datenbank"""
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(stored):
    """Gespeichertes Geheimnis lesen, None wenn es nicht (mehr) lesbar ist"""
    if not stored:
        return None
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        current_app.logger.warning("2FA-Geheimnis nicht lesbar, wurde der SECRET_KEY geaendert?")
        return None


def new_secret():
    """Neues Geheimnis fuer die Authenticator-App"""
    return pyotp.random_base32()


def provisioning_uri(secret, email):
    """otpauth-Link, den die Authenticator-App aus dem QR-Code liest"""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def qr_svg(uri):
    """QR-Code als SVG, auf dem Server erzeugt. Weisser Grund, auch im Dark Mode lesbar."""
    return segno.make(uri, error='m').svg_inline(scale=5, dark='#000', light='#fff',
                                                 omitsize=False)


def matching_step(secret, code):
    """
    Zeitfenster, zu dem der 6-stellige Code passt, sonst None. Das Fenster
    merkt sich der Aufrufer, damit ein Code nicht zweimal gilt.
    """
    code = re.sub(r"\s", "", code or "")
    if not secret or not re.fullmatch(r"\d{6}", code):
        return None
    totp = pyotp.TOTP(secret)
    now_step = int(time.time()) // totp.interval
    for step in range(now_step - VALID_WINDOW, now_step + VALID_WINDOW + 1):
        if hmac.compare_digest(totp.at(step * totp.interval), code):
            return step
    return None


def normalize_recovery_code(code):
    """Gross/klein, Leerzeichen und Bindestriche spielen keine Rolle"""
    return re.sub(r"[^0-9a-f]", "", (code or "").lower())


def hash_recovery_code(code):
    """
    Codes sind zufaellig mit 50 Bit, ein schneller Hash reicht. Gespeichert wird
    nur der Hash, angezeigt werden die Codes einmal bei der Einrichtung.
    """
    return hashlib.sha256(normalize_recovery_code(code).encode()).hexdigest()


def new_recovery_codes():
    """Liste der Klartext-Codes, Format xxxxx-xxxxx"""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5)
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes
