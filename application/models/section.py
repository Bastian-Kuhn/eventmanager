"""
Sektionen: eine Installation bedient mehrere Vereine. Jede Sektion hat eigene
Adresse, Design und Mailversand. Welche Sektion gilt, steht in g.section
(siehe current_section).
"""
from datetime import datetime
from flask import current_app, g, has_request_context, request
from mongoengine.errors import DoesNotExist
from mongoengine.queryset import QuerySetManager
from application import db
from application.models.config import Config

SLUG_PATTERN = r'[a-z0-9][a-z0-9-]{0,39}'


def normalize_hostname(value):
    """
    Nur der Hostname, wie ihn der Request liefert: aus
    'https://Touren.Example.org:443/login' wird 'touren.example.org'
    """
    host = (value or '').strip().lower()
    host = host.split('://', 1)[-1]
    host = host.split('/', 1)[0].split('?', 1)[0]
    return host.split(':', 1)[0].strip('.')


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

    # Abrechnung (nur Sektionen aus dem Portal). Ohne trial_until laeuft die
    # Sektion ohne Abrechnung, so wie alle, die ein globaler Admin anlegt.
    member_count = db.IntField(min_value=1)
    trial_until = db.DateTimeField()
    stripe_customer_id = db.StringField()
    subscription_id = db.StringField()
    subscription_status = db.StringField()  # Status laut Stripe
    current_period_end = db.DateTimeField()
    cancel_at_period_end = db.BooleanField(default=False)
    billing_interval = db.StringField()  # 'month' oder 'year' laut Stripe

    # Sektionen, mit denen diese ihre Touren teilt. Wirksam nur, wenn die
    # andere Seite diese Sektion ebenfalls eingetragen hat (siehe partners)
    partner_sections = db.ListField(field=db.ReferenceField(document_type='Section'))

    meta = {
        'strict': False,
        'indexes': ['hostnames'],
    }

    def clean(self):
        self.hostnames = sorted({host for host in map(normalize_hostname, self.hostnames or []) if host})

    def __str__(self):
        return self.name or self.slug

    @property
    def is_paid(self):
        """Abo laeuft laut Stripe (past_due: Stripe versucht es noch)"""
        return self.subscription_status in ('active', 'trialing', 'past_due')

    def partners(self):
        """Freigeschaltete Sektionen, die beidseitig als Partner eingetragen sind"""
        ids = [ref_id(ref) for ref in self._data.get('partner_sections') or []]  # pylint: disable=protected-access
        if self.pk is None or not ids:
            return []
        return [section for section in Section.objects(pk__in=ids, partner_sections=self.pk)
                if section.is_active()]

    def in_trial(self, now=None):
        return bool(self.trial_until) and (now or datetime.now()) < self.trial_until

    def is_active(self, now=None):
        """Darf die Sektion genutzt werden?"""
        if not current_app.config.get('SUBSCRIPTIONS_ENABLED') or self.trial_until is None:
            return True
        return self.is_paid or self.in_trial(now)


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


def portal_hosts():
    """Hostnamen des Portals: {host: 'sektion' oder 'verein'}"""
    portal = current_app.config.get('PORTAL_HOSTNAME')
    if not portal:
        return {}
    club = current_app.config.get('PORTAL_CLUB_HOSTNAME')
    if not club and portal.startswith('sektion.'):
        club = 'verein.' + portal[len('sektion.'):]
    hosts = {portal: 'sektion'}
    if club:
        hosts[club] = 'verein'
    return hosts


def portal_kind():
    """'sektion' oder 'verein' auf dem Portal, sonst None"""
    return portal_hosts().get(normalize_hostname(request.host))


def is_portal_request():
    """Request an das Portal (PORTAL_HOSTNAME), dort gibt es keine Sektion"""
    return portal_kind() is not None


def current_section():
    """Sektion des laufenden Requests, ausserhalb eines Requests None"""
    if not has_request_context():
        return None
    if is_portal_request():
        raise DoesNotExist('Das Portal gehoert zu keiner Sektion')
    if 'section' not in g:
        g.section = resolve_section(request.host)
    if g.section is None:
        raise DoesNotExist('Keine Sektion fuer diese Adresse')
    return g.section



def section_or_none():
    """Wie current_section, aber None statt 404 (Fehlerseiten, Context-Processors)"""
    try:
        return current_section()
    except DoesNotExist:
        return None


def ref_id(value):
    """id eines Verweises als String, egal ob Document, DBRef, ObjectId oder None"""
    if value is None:
        return None
    return str(getattr(value, 'id', value))


def in_current_section(document):
    """Gehoert der Datensatz zur Sektion des Requests? Fuer direkt geladene Verweise."""
    section = section_or_none()
    return ref_id(document._data.get('section')) == ref_id(section.pk if section else None)  # pylint: disable=protected-access


class SectionQuerySetManager(QuerySetManager):  # pylint: disable=too-few-public-methods
    """
    `objects` fuer Daten einer Sektion: im Request nur die der aktuellen
    Sektion. Ohne gespeicherte Section (vor der Migration) filtert er auf
    section=None, das trifft alle Altdaten. Ausserhalb eines Requests (Jobs,
    Tests) ungefiltert. Fuer bewusst sektionsuebergreifende Abfragen gibt es
    `all_objects`.
    """

    def __get__(self, instance, owner):
        queryset = super().__get__(instance, owner)
        if not has_request_context():
            return queryset
        section = section_or_none()
        return queryset.filter(section=section.pk if section is not None else None)


class SectionScoped:  # pylint: disable=too-few-public-methods
    """
    Mixin fuer Documents mit Feld `section`: neue Datensaetze bekommen beim
    Speichern die Sektion des Requests.
    """

    def clean(self):
        if self.section is None and has_request_context():
            section = section_or_none()
            if section is not None and section.pk is not None:
                self.section = section
        super().clean()
