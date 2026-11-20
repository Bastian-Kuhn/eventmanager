"""
Zugangsschluessel der Tourentool-App. Gespeichert wird nur der SHA-256 des
Schluessels; er gilt fuer ein Konto in einer Sektion und endet mit einem
Passwortwechsel, dem Ablauf oder dem Widerruf im Profil.
"""
# pylint: disable=no-member, too-few-public-methods
import hashlib
import secrets
from datetime import datetime, timedelta

from application import db

TOKEN_DAYS = 365


def token_hash(raw):
    """Hash, unter dem ein Schluessel gespeichert und gesucht wird"""
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiToken(db.Document):
    """Ein Schluessel der App"""
    user = db.ReferenceField(document_type='User', required=True)
    section = db.ReferenceField(document_type='Section', required=True)
    name = db.StringField(max_length=100)
    token_hash = db.StringField(required=True, unique=True)
    # password_fingerprint() beim Anlegen: ein neues Passwort meldet die App ab
    password_fingerprint = db.StringField()
    created = db.DateTimeField(default=datetime.now)
    last_used = db.DateTimeField()
    expires = db.DateTimeField()

    meta = {
        'strict': False,
        'indexes': ['user'],
    }

    @classmethod
    def issue(cls, user, section, name):
        """Neuen Schluessel anlegen, gibt (Token, Klartext) zurueck"""
        raw = secrets.token_urlsafe(32)
        token = cls(user=user, section=section, name=(name or 'App')[:100],
                    token_hash=token_hash(raw), password_fingerprint=user.password_fingerprint(),
                    expires=datetime.now() + timedelta(days=TOKEN_DAYS)).save()
        return token, raw
