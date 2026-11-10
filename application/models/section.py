"""
Sektionen: eine Installation bedient mehrere Vereine. Jede Sektion hat eigene
Adresse, Design und Mailversand. Welche Sektion gilt, steht in g.section
(siehe current_section).
"""
from flask import g, has_request_context, request
from mongoengine.errors import DoesNotExist
from application import db
from application.models.config import Config

SLUG_PATTERN = r'[a-z0-9][a-z0-9-]{0,39}'


class Section(db.Document):
    """Ein Verein, z.B. eine DAV-Sektion"""
    slug = db.StringField(required=True, unique=True, regex=f'^{SLUG_PATTERN}$')
    name = db.StringField(required=True)
    # Hostnamen ohne Port, klein geschrieben
    hostnames = db.ListField(field=db.StringField())
    # Gilt fuer Hosts, die keiner Sektion gehoeren (genau eine sollte das haben)
    is_default = db.BooleanField(default=False)

    nav_background_color = db.StringField()
    homepage_link = db.StringField()
    logo_image = db.ImageField(field="logo_image", collection='logos')
    event_categories_full = db.ListField(field=db.EmbeddedDocumentField(document_type="Category"))

    mail_sender = db.StringField()
    mail_server = db.StringField()
    mail_use_tls = db.BooleanField()
    mail_username = db.StringField()
    mail_subject_prefix = db.StringField()
    mail_password = db.StringField()

    # HTML der Foto-/Video-Einwilligung, leer = Standardtext (DAV Kampenwand)
    media_consent_text = db.StringField()

    meta = {
        'strict': False,
        'indexes': ['hostnames'],
    }

    def clean(self):
        self.hostnames = sorted({h.strip().lower() for h in self.hostnames or [] if h.strip()})

    def __str__(self):
        return self.name or self.slug


def section_from_legacy_config():
    """
    Vor der Migration gibt es keine Section, nur das alte Config-Doc. Daraus
    eine ungespeicherte Section bauen, damit die App unveraendert laeuft.
    """
    section = Section(slug='default', name='', is_default=True)
    try:
        old = Config.objects(enabled=True)[0]
    except IndexError:
        return section
    for field in ('nav_background_color', 'homepage_link', 'event_categories_full',
                  'mail_sender', 'mail_server', 'mail_use_tls', 'mail_username',
                  'mail_subject_prefix', 'mail_password', 'media_consent_text'):
        setattr(section, field, getattr(old, field))
    section.logo_image = old.logo_image
    return section


def resolve_section(host):
    """
    Sektion zu einem Hostnamen, sonst die Default-Sektion (Catchall
    *.tourenanmeldung.de zeigt auf dav). None, wenn es Sektionen gibt, aber
    keine passt.
    """
    host = (host or '').split(':')[0].lower()
    section = Section.objects(hostnames=host).first()
    if section:
        return section
    section = Section.objects(is_default=True).first()
    if section:
        return section
    if Section.objects.count():
        return None
    return section_from_legacy_config()


def current_section():
    """Sektion des laufenden Requests, ausserhalb eines Requests None"""
    if not has_request_context():
        return None
    if 'section' not in g:
        g.section = resolve_section(request.host)
    if g.section is None:
        raise DoesNotExist('Keine Sektion fuer diese Adresse')
    return g.section

