"""
User Accounts
"""
from datetime import datetime
from flask import current_app
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.jose import jwt, JoseError
from application import db
from application.events.models import Event

roles = [
  ('no_member', "Kein Vereinsmitglied"),
  ('member', "Vereinsmitglied"),
  ('guide', "Trainer/ Übungsleiter"),
]

class User(db.Document, UserMixin):
    """
    User for login
    """

    email = db.EmailField(unique=True, required=True)
    first_name = db.StringField()
    last_name = db.StringField()

    birthdate = db.DateTimeField()
    phone = db.StringField()

    club_member = db.BooleanField(default=False)
    club_member_confirmed = db.BooleanField(default=False)

    media_optin = db.BooleanField()
    data_optin = db.BooleanField()


    event_registrations = db.ListField(db.ReferenceField(Event))

    pwdhash = db.StringField()

    global_admin = db.BooleanField(default=False)
    role = db.StringField(choices=roles)

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
        Add Event to User
        """
        if event not in self.event_registrations:
            self.event_registrations.append(event)
            self.save()

    def participate_event(self, event_id):
        """
        Check if user is part of event
        """
        if str(event_id) in [ str(x.id) for x in self.event_registrations]:
            return True
        return False


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
        # @TODO Expire Token
        header = {
              'alg': 'HS256'
        }
        key = current_app.config['SECRET_KEY']
        data = {
            'userid': str(self.id)
        }
        data.update(**kwargs)

        return jwt.encode(header=header, payload=data, key=key)



    def is_admin(self):
        """
        Check Admin Status
        """
        return self.global_admin

    def has_right(self, role):
        """
        Grand User the right if he has the role
        or is global admin
        """
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
