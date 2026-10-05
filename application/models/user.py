"""
User Accounts
"""
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

    date_added = db.DateTimeField()
    date_changed = db.DateTimeField(default=datetime.now())
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

    def is_favorite(self, event_id):
        """
        Check if event is favored by user
        """
        return str(event_id) in self._reference_ids('favorites')

    def toggle_favorite(self, event):
        """
        Add/remove event from favorites, return new state
        """
        for fav in self.favorites:
            if str(fav.id) == str(event.id):
                self.favorites.remove(fav)
                self.save()
                return False
        self.favorites.append(event)
        self.save()
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
        return check_password_hash(self.pwdhash, password)

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
        """ User Mixin overwrite """
        return str(self.id)

    def __str__(self):
        """
        Model representation
        """
        return f"{self.first_name} {self.last_name}"
