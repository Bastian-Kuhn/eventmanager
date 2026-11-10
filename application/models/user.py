"""
User Accounts
"""
import hashlib
import hmac
from datetime import datetime
from flask import current_app
from datetime import timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.jose import jwt, JoseError
from application import db
from application.events.models import Event

roles = [
  ('no_member', "Kein Vereinsmitglied"),
  ('member', "Vereinsmitglied"),
  ('guide', "Trainer/ Übungsleiter"),
  ('youthguide', "Jugendleiter"),
  ('attendant', "Tourenbegleiter"),
]

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
        if self.has_right('guide'):
            return True
        if not self.participate_event(event.id):
            return False
        if self.role:
            return True
        return any(ticket.confirmed for ticket in event.get_tickets_of_user(self))

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
        key = current_app.config['SECRET_KEY']
        data = {
            'userid': str(self.id),
            'pwd': self.password_fingerprint(),
            'exp' : dt.timestamp(),
            'iat': datetime.utcnow(),
        }
        return jwt.encode(header=header, payload=data, key=key).decode('utf-8')


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
