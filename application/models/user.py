"""
User Accounts
"""
import hashlib
import hmac
import re
from datetime import datetime
from flask import current_app, has_request_context
from datetime import timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from joserfc import jwt
from joserfc.jwk import OctKey
from mongoengine.queryset.visitor import Q
from application import db
from application.modules import tfa
from application.events.models import Event
from application.models.section import current_section, in_current_section, ref_id, section_or_none

roles = [
  ('no_member', "Kein Vereinsmitglied"),
  ('member', "Vereinsmitglied"),
  ('guide', "Trainer/ Übungsleiter"),
  ('youthguide', "Jugendleiter"),
  ('attendant', "Tourenbegleiter"),
]

# Nie im Admin-Formular: wer sie dort aendern koennte, schaltet fremde 2FA ab
TFA_FIELDS = ('tfa_secret', 'tfa_pending_secret', 'tfa_recovery_codes', 'tfa_last_step')


# Gilt pro Sektion, siehe Membership
SECTION_FIELDS = ('role', 'admin', 'club_id')


class Membership(db.EmbeddedDocument):
    """Rolle, Vereinsadmin und Mitgliedsnummer eines Kontos in einer Sektion"""
    section = db.ReferenceField(document_type='Section', required=True)
    role = db.StringField(choices=roles)
    admin = db.BooleanField(default=False)
    club_id = db.StringField()


def _saved(section):
    """Gespeicherte Section, sonst None (vor der Migration gibt es nur Altfelder)"""
    return section if section is not None and getattr(section, 'pk', None) is not None else None


class User(db.Document, UserMixin):
    """
    User for login
    """

    email = db.EmailField(unique=True, required=True)
    first_name = db.StringField()
    last_name = db.StringField()

    # Ein Konto gilt in allen Sektionen, Rolle und Vereinsadmin je Sektion
    memberships = db.ListField(field=db.EmbeddedDocumentField(document_type=Membership))
    # Stand vor den Sektionen, nur noch gelesen solange es keine Section gibt.
    # Die Migration uebernimmt sie in memberships und laesst sie stehen.
    legacy_role = db.StringField(choices=roles, db_field='role')

    profile_img = db.ImageField(field="profile_img", collection='logos')

    birthdate = db.DateField()
    phone = db.StringField()

    legacy_club_id = db.StringField(db_field='club_id')

    media_optin = db.BooleanField()
    media_optin_date = db.DateTimeField()  # Zeitpunkt der Foto/Video-Einwilligung (Nachweis)
    data_optin = db.BooleanField()

    event_registrations = db.ListField(field=db.ReferenceField(document_type=Event))
    favorites = db.ListField(field=db.ReferenceField(document_type=Event))

    pwdhash = db.StringField()

    global_admin = db.BooleanField(default=False)
    legacy_admin = db.BooleanField(default=False, db_field='admin')

    disabled = db.BooleanField(default=False)
    # Nur bei Selbstregistrierung gesetzt, bis der Link aus der Mail genutzt
    # wurde. Fehlt bei Altkonten und bei Konten aus dem Admin: gilt als bestaetigt.
    email_unconfirmed = db.BooleanField(default=False)

    date_added = db.DateTimeField()
    date_changed = db.DateTimeField(default=datetime.now)
    date_password = db.DateTimeField()
    last_login = db.DateTimeField()
    force_password_change = db.BooleanField(default=False)

    # Zwei-Faktor-Anmeldung (freiwillig). Geheimnisse verschluesselt mit dem
    # SECRET_KEY, Wiederherstellungscodes nur als Hash, siehe modules/tfa.py
    tfa_secret = db.StringField()
    # Waehrend der Einrichtung, bis der erste Code bestaetigt ist
    tfa_pending_secret = db.StringField()
    tfa_recovery_codes = db.ListField(field=db.StringField())
    # Zuletzt genutztes Zeitfenster: ein Code gilt nur einmal
    tfa_last_step = db.IntField()

    meta = {'indexes': [
        'email',
        'memberships.section',
        'event_registrations',
        ],
    'strict': False,
    }

    def __init__(self, *args, **kwargs):
        # role, admin und club_id sind keine Felder mehr, sondern gelten fuer
        # die Sektion des Requests (siehe membership)
        section_values = {key: kwargs.pop(key) for key in SECTION_FIELDS if key in kwargs}
        super().__init__(*args, **kwargs)
        for key, value in section_values.items():
            setattr(self, key, value)

    def membership(self, section=None, create=False):
        """
        Mitgliedschaft in section (Default: Sektion des Requests). None ohne
        Mitgliedschaft und vor der Migration (dann gelten die Altfelder).
        """
        section = _saved(section if section is not None else section_or_none())
        if section is None:
            return None
        wanted = ref_id(section)
        for entry in self.memberships:
            if ref_id(entry._data.get('section')) == wanted:  # pylint: disable=protected-access
                return entry
        if not create:
            return None
        entry = Membership(section=section)
        self.memberships.append(entry)
        return entry

    def _section_value(self, key, section=None):
        if _saved(section if section is not None else section_or_none()) is None:
            return getattr(self, 'legacy_' + key)
        entry = self.membership(section)
        return getattr(entry, key) if entry else Membership._fields[key].default  # pylint: disable=protected-access

    def _set_section_value(self, key, value):
        entry = self.membership(create=True)
        if entry is None:
            setattr(self, 'legacy_' + key, value)
        else:
            setattr(entry, key, value)

    role = property(lambda self: self._section_value('role'),
                    lambda self, value: self._set_section_value('role', value))
    admin = property(lambda self: self._section_value('admin'),
                     lambda self, value: self._set_section_value('admin', value))
    club_id = property(lambda self: self._section_value('club_id'),
                       lambda self, value: self._set_section_value('club_id', value))

    def role_in(self, section):
        """Rolle in einer bestimmten Sektion, z.B. der eines Events"""
        return self._section_value('role', section)

    def join_section(self, section=None):
        """
        Mitgliedschaft (ohne Rolle) in der Sektion anlegen, falls es keine gibt.
        Atomar, damit parallele Logins keine doppelten Eintraege erzeugen.
        """
        section = _saved(section if section is not None else section_or_none())
        if section is None or self.membership(section):
            return
        User.objects(id=self.id, memberships__section__ne=section.pk) \
            .update_one(push__memberships=Membership(section=section))
        self.reload('memberships')


    def add_event(self, event):
        """
        Add Event to User. Atomar per $addToSet, damit parallele Buchungen
        sich nicht gegenseitig die Rueckreferenz ueberschreiben.
        """
        User.objects(id=self.id).update_one(add_to_set__event_registrations=event)

    def _reference_ids(self, field_name):
        """
        IDs einer ListField(ReferenceField) als Strings, ohne die referenzierten
        Documents zu laden (Zugriff ueber das Attribut dereferenziert alle).
        """
        return {str(getattr(ref, 'id', ref)) for ref in self._data.get(field_name) or []}

    def participate_event(self, event_id):
        """
        Check if user is part of event
        """
        return str(event_id) in self._reference_ids('event_registrations')

    def may_see_participants(self, event):
        """
        Teilnehmerliste: Guides, sonst nur Teilnehmer mit geprueftem Konto
        (Rolle gesetzt) oder mit bestaetigtem Ticket. Ein frisch
        registriertes Konto mit offener Buchung sieht sie nicht.
        """
        if self.may_manage_event(event):
            return True
        # Geteilte Touren: die Liste gibt es nur in der Sektion der Tour
        if not self.in_event_section(event) or not self.participate_event(event.id):
            return False
        if self.role:
            return True
        return any(ticket.confirmed for ticket in event.get_tickets_of_user(self))

    @staticmethod
    def in_event_section(event):
        """Laeuft der Request in der Sektion der Tour? Ausserhalb eines Requests immer"""
        return not has_request_context() or in_current_section(event)

    def may_manage_event(self, event):
        """
        Tour bearbeiten und verwalten (Teilnehmer, Preise, Abrechnung, Export):
        Admins und Trainer alle Touren, Tourenbegleiter nur die, bei denen sie
        als Verantwortliche eingetragen sind. Nur in der Sektion der Tour,
        nie bei einer geteilten Tour einer Partnersektion.
        """
        if not self.in_event_section(event):
            return False
        if self.manages_all_events():
            return True
        if not self.has_right('guide'):
            return False
        # Ohne Dereferenzieren: die Liste kann DBRefs, ObjectIds oder Users enthalten
        owners = event._data.get('event_owners') or []  # pylint: disable=protected-access
        return str(self.id) in {str(getattr(ref, 'id', ref)) for ref in owners}

    def leave_section(self, section=None):
        """Mitgliedschaft in der Sektion entfernen, das Konto bleibt"""
        section = _saved(section if section is not None else section_or_none())
        if section is not None:
            User.objects(id=self.id).update_one(pull__memberships__section=section.pk)
            self.reload('memberships')

    def is_favorite(self, event_id):
        """
        Check if event is favored by user
        """
        return str(event_id) in self._reference_ids('favorites')

    def toggle_favorite(self, event):
        """
        Add/remove event from favorites, return new state
        """
        if self.is_favorite(event.id):
            User.objects(id=self.id).update_one(pull__favorites=event)
            return False
        User.objects(id=self.id).update_one(add_to_set__favorites=event)
        return True


    def set_password(self, password):
        """
        Password seter
        """
        self.date_password = datetime.now()
        self.pwdhash = generate_password_hash(password)

    def check_password(self, password):
        """
        Password checker
        """
        return bool(self.pwdhash) and check_password_hash(self.pwdhash, password)

    @property
    def tfa_enabled(self):
        """Zwei-Faktor-Anmeldung ist eingeschaltet"""
        return bool(self.tfa_secret)

    def check_tfa_code(self, code):
        """
        Code aus der Authenticator-App pruefen. Jeder Code gilt nur einmal:
        das Zeitfenster wird atomar hochgezaehlt, ein zweiter Versuch mit
        demselben Code scheitert.
        """
        step = tfa.matching_step(tfa.decrypt_secret(self.tfa_secret), code)
        if step is None:
            return False
        used = User.objects(id=self.id, tfa_secret=self.tfa_secret) \
            .filter(Q(tfa_last_step__lt=step) | Q(tfa_last_step=None)) \
            .update_one(set__tfa_last_step=step)
        if used:
            self.tfa_last_step = step
        return bool(used)

    def use_recovery_code(self, code):
        """Wiederherstellungscode einloesen, jeder gilt nur einmal"""
        hashed = tfa.hash_recovery_code(code)
        if not tfa.normalize_recovery_code(code):
            return False
        used = User.objects(id=self.id, tfa_recovery_codes=hashed) \
            .update_one(pull__tfa_recovery_codes=hashed)
        if used:
            self.reload('tfa_recovery_codes')
        return bool(used)

    def check_tfa(self, code):
        """Code aus der App oder ein Wiederherstellungscode"""
        if not self.tfa_enabled:
            return False
        if re.fullmatch(r"\s*\d{3}\s?\d{3}\s*", code or ""):
            return self.check_tfa_code(code)
        return self.use_recovery_code(code)

    def enable_tfa(self, code):
        """
        Einrichtung abschliessen, wenn der Code zum vorlaeufigen Geheimnis passt.
        Gibt die Wiederherstellungscodes im Klartext zurueck (nur dieses eine
        Mal sichtbar), sonst None.
        """
        step = tfa.matching_step(tfa.decrypt_secret(self.tfa_pending_secret), code)
        if step is None:
            return None
        codes = tfa.new_recovery_codes()
        self.tfa_secret = self.tfa_pending_secret
        self.tfa_pending_secret = None
        self.tfa_recovery_codes = [tfa.hash_recovery_code(c) for c in codes]
        self.tfa_last_step = step
        self.save()
        return codes

    def disable_tfa(self):
        """2FA abschalten, auch fuer das Zuruecksetzen durch einen Admin"""
        User.objects(id=self.id).update_one(
            unset__tfa_secret=True, unset__tfa_pending_secret=True,
            unset__tfa_recovery_codes=True, unset__tfa_last_step=True)
        self.reload()

    def password_fingerprint(self):
        """
        Aendert sich mit dem Passwort. Sessions und Reset-Links tragen ihn mit und
        werden dadurch beim Passwortwechsel ungueltig.
        """
        key = current_app.config['SECRET_KEY'].encode()
        return hmac.new(key, (self.pwdhash or '').encode(), hashlib.sha256).hexdigest()[:32]

    def generate_token(self, expiration=3600, custom_values=False):
        """
        Token generator
        """
        dt = datetime.utcnow()+timedelta(seconds=expiration)
        header = {
              'alg': 'HS256'
        }
        key = OctKey.import_key(current_app.config['SECRET_KEY'])
        data = {
            'userid': str(self.id),
            'pwd': self.password_fingerprint(),
            'exp' : dt.timestamp(),
            'iat': datetime.utcnow(),
        }
        return jwt.encode(header, data, key, algorithms=['HS256'])


    def is_admin(self):
        """
        Check Admin Status
        """
        return self.global_admin or self.admin

    def is_global_admin(self):
        """
        Check Admin Status
        """
        return self.global_admin

    def manages_all_events(self):
        """
        Admins (global oder Verein) und Trainer verwalten alle Touren,
        Tourenbegleiter nur ihre eigenen (Bastian, 2026-11-10)
        """
        return self.is_admin() or self.role == 'guide'

    def has_right(self, role):
        """
        Grand User the right if he has the role
        or is global admin
        """
        if role == "guide":
            if self.role == 'attendant':
                return True
        if role == self.role:
            return True
        if self.global_admin:
            return True
        return False

    def get_id(self):
        """ User Mixin overwrite, siehe password_fingerprint """
        return f"{self.id}|{self.password_fingerprint()}"

    def __str__(self):
        """
        Model representation
        """
        return f"{self.first_name} {self.last_name}"


def section_members():
    """Konten mit Mitgliedschaft in der Sektion des Requests (vor der Migration alle)"""
    section = _saved(section_or_none())
    if section is None:
        return User.objects
    return User.objects(memberships__section=section.pk)


def remove_event_references(event):
    """Rueckreferenzen auf ein Event entfernen, das gleich geloescht wird."""
    User.objects(event_registrations=event).update(pull__event_registrations=event)
    User.objects(favorites=event).update(pull__favorites=event)


def remove_user_references(user):
    """
    Verweise auf einen User entfernen, der gleich geloescht wird. Seine gebuchten
    Tickets bleiben fuer die Abrechnung erhalten, die Teilnehmerliste zeigt sie
    als "Geloeschter Account".
    """
    from application.huts.models import Hut, HutBooking  # pylint: disable=import-outside-toplevel
    # all_objects: das Konto kann in mehreren Sektionen stecken
    Event.all_objects(event_owners=user).update(pull__event_owners=user)
    Hut.all_objects(admins=user).update(pull__admins=user)
    HutBooking.all_objects(user=user).update(unset__user=True)
