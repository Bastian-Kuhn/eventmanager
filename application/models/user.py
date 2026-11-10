"""
User Accounts
"""
import hashlib
import hmac
import re
from datetime import datetime
from flask import current_app
from datetime import timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from joserfc import jwt
from joserfc.jwk import OctKey
from mongoengine.queryset.visitor import Q
from application import db
from application.modules import tfa
from application.events.models import Event

roles = [
  ('no_member', "Kein Vereinsmitglied"),
  ('member', "Vereinsmitglied"),
  ('guide', "Trainer/ Übungsleiter"),
  ('youthguide', "Jugendleiter"),
  ('attendant', "Tourenbegleiter"),
]

# Nie im Admin-Formular: wer sie dort aendern koennte, schaltet fremde 2FA ab
TFA_FIELDS = ('tfa_secret', 'tfa_pending_secret', 'tfa_recovery_codes', 'tfa_last_step')


class User(db.Document, UserMixin):
    """
    User for login
    """

    email = db.EmailField(unique=True, required=True)
    first_name = db.StringField()
    last_name = db.StringField()

    role = db.StringField(choices=roles)

    profile_img = db.ImageField(field="profile_img", collection='logos')

    birthdate = db.DateField()
    phone = db.StringField()

    club_id = db.StringField()

    media_optin = db.BooleanField()
    media_optin_date = db.DateTimeField()  # Zeitpunkt der Foto/Video-Einwilligung (Nachweis)
    data_optin = db.BooleanField()

    event_registrations = db.ListField(field=db.ReferenceField(document_type=Event))
    favorites = db.ListField(field=db.ReferenceField(document_type=Event))

    pwdhash = db.StringField()

    global_admin = db.BooleanField(default=False)
    admin = db.BooleanField(default=False)

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
        'email'
        ],
    'strict': False,
    }


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
        if not self.participate_event(event.id):
            return False
        if self.role:
            return True
        return any(ticket.confirmed for ticket in event.get_tickets_of_user(self))

    def may_manage_event(self, event):
        """
        Tour bearbeiten und verwalten (Teilnehmer, Preise, Abrechnung, Export):
        globale Admins, sonst nur Trainer und Tourenbegleiter, die als
        Verantwortliche der Tour eingetragen sind. Touren ohne Verantwortliche
        verwalten nur globale Admins.
        """
        if self.global_admin:
            return True
        if not self.has_right('guide'):
            return False
        # Ohne Dereferenzieren: die Liste kann DBRefs, ObjectIds oder Users enthalten
        owners = event._data.get('event_owners') or []  # pylint: disable=protected-access
        return str(self.id) in {str(getattr(ref, 'id', ref)) for ref in owners}

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
    Event.objects(event_owners=user).update(pull__event_owners=user)
    Hut.objects(admins=user).update(pull__admins=user)
    HutBooking.objects(user=user).update(unset__user=True)
