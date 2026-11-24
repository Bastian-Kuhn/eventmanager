"""
Websites einer Sektion, die Touren ueber das Tourentool-Plugin fuer WordPress
anbieten. Die Website meldet sich mit ihrem Schluessel (nur der SHA-256 liegt
hier) und darf Touren lesen und fuer angemeldete Bucher buchen. Bucher melden
sich per Ruecksprung an (Code gegen Schluessel tauschen) oder, wenn die Sektion
es erlaubt, ueber das WordPress-Konto (SSO).
"""
# pylint: disable=no-member, too-few-public-methods
import secrets
from datetime import datetime, timedelta

from application import db
from application.models.api_token import token_hash

CODE_MINUTES = 5

# SSO-Modus: wie die Sektion WordPress-Konten den Konten hier zuordnet
sso_modes = [
    ('off', "Aus: Bucher melden sich mit ihrem Konto hier an"),
    ('auto', "Automatisch: gleiche E-Mail verknüpft, neue E-Mail legt ein Konto an"),
    ('existing', "Nur bestehende Konten: gleiche E-Mail verknüpft, sonst nichts"),
    ('approval', "Nach Freigabe: jede neue Verknüpfung gibt ein Admin frei"),
]
SSO_MODE_KEYS = [x[0] for x in sso_modes]


class Website(db.Document):
    """Eine Website mit dem Plugin"""
    section = db.ReferenceField(document_type='Section', required=True)
    name = db.StringField(required=True, max_length=100)
    # Ruecksprung nach der Anmeldung, exakt so, wie das Plugin es anzeigt
    return_url = db.StringField(required=True, max_length=500)
    key_hash = db.StringField(required=True, unique=True)
    sso_mode = db.StringField(choices=SSO_MODE_KEYS, default='off')
    created = db.DateTimeField(default=datetime.now)
    last_used = db.DateTimeField()

    meta = {
        'strict': False,
        'indexes': ['section'],
    }

    def new_key(self):
        """Neuen Schluessel setzen (nicht speichern), gibt den Klartext zurueck"""
        raw = 'tts_' + secrets.token_urlsafe(32)
        self.key_hash = token_hash(raw)
        return raw


class WebsiteLink(db.Document):
    """
    WordPress-Konto einer Website -> Konto hier. Offen (active=False) wartet
    es auf die Freigabe eines Admins, dann kann user noch fehlen.
    """
    website = db.ReferenceField(document_type='Website', required=True)
    wp_user = db.StringField(required=True, max_length=64)
    user = db.ReferenceField(document_type='User')
    email = db.StringField(max_length=254)
    first_name = db.StringField(max_length=100)
    last_name = db.StringField(max_length=100)
    active = db.BooleanField(default=False)
    created = db.DateTimeField(default=datetime.now)

    meta = {
        'strict': False,
        'indexes': [{'fields': ['website', 'wp_user'], 'unique': True}, 'user'],
    }


class WebsiteCode(db.Document):
    """Einmal-Code nach der Anmeldung, die Website tauscht ihn gegen einen Schluessel"""
    code_hash = db.StringField(required=True, unique=True)
    website = db.ReferenceField(document_type='Website', required=True)
    user = db.ReferenceField(document_type='User', required=True)
    expires = db.DateTimeField(required=True)

    meta = {'strict': False}

    @classmethod
    def issue(cls, website, user):
        """Code anlegen, gibt den Klartext zurueck"""
        raw = secrets.token_urlsafe(32)
        cls(code_hash=token_hash(raw), website=website, user=user,
            expires=datetime.now() + timedelta(minutes=CODE_MINUTES)).save()
        return raw
