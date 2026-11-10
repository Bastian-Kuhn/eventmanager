import re

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, DateField
from wtforms.validators import InputRequired, Email, EqualTo, ValidationError

from application.auth.forms import validate_password
from application.events.forms import max_length, PHONE_MAX, EMAIL_MAX, CLUB_ID_MAX


class ConsentForm(FlaskForm):
    """
    Globale Einwilligungen im Profil bearbeiten
    """
    media_optin = BooleanField("Ich willige ein, dass von mir Fotos und Videos angefertigt und "
                               "– wie in der folgenden Erklärung beschrieben – veröffentlicht werden dürfen")
    data_optin = BooleanField("Ich bin einverstanden, dass meine Kontaktdaten mit anderen Mitgliedern geteilt werden")
    submit_consent = SubmitField("Einwilligungen speichern")


class ChangePasswordForm(FlaskForm):
    """
    Passwort im Profil aendern (aktuelles Passwort erforderlich)
    """
    old_password = PasswordField("Aktuelles Passwort", validators=[InputRequired()])
    password = PasswordField("Neues Passwort",
                             validators=[InputRequired(), validate_password, EqualTo('password_repeat')])
    password_repeat = PasswordField("Neues Passwort wiederholen", validators=[InputRequired()])
    submit_password = SubmitField("Passwort ändern")


# Buchstaben, getrennt durch Leerzeichen, Bindestrich, Apostroph oder "Punkt
# Leerzeichen" (Abkuerzung). Keine Ziffern, Links oder Satzzeichen: der Name
# steht in Mails des Vereins an die angegebene Adresse.
NAME_PATTERN = re.compile(r"[^\W\d_]+(?:(?:[ '’-]|\. )[^\W\d_]+)*\.?")
NAME_MAX = 50


def validate_name(_form, field):
    """
    Namen auf Buchstaben und uebliche Trennzeichen begrenzen
    """
    field.data = " ".join((field.data or "").split())
    if not NAME_PATTERN.fullmatch(field.data):
        raise ValidationError("Bitte nur Buchstaben, Leerzeichen, Bindestrich oder Apostroph verwenden")


NAME_VALIDATORS = [InputRequired(), max_length(NAME_MAX), validate_name]


class NewUserForm(FlaskForm):
    """
    Signup Form for User
    """
    first_name = StringField("Vorname", validators=NAME_VALIDATORS, render_kw={'maxlength': NAME_MAX})
    last_name = StringField("Nachname", validators=NAME_VALIDATORS, render_kw={'maxlength': NAME_MAX})
    email = StringField("E-Mail", validators=[Email(), InputRequired(), max_length(EMAIL_MAX)],
                        render_kw={'maxlength': EMAIL_MAX})
    phone = StringField("Handynummer", validators=[InputRequired(), max_length(PHONE_MAX)],
                        render_kw={'maxlength': PHONE_MAX})
    birthdate = DateField("Geburstdatum", validators=[InputRequired()])
    club_id = StringField("Mitgliedsnummer", validators=[max_length(CLUB_ID_MAX)],
                          render_kw={'maxlength': CLUB_ID_MAX})
    media_optin = BooleanField("Ich willige ein, dass von mir Fotos und Videos angefertigt und "
                               "– wie in der folgenden Erklärung beschrieben – veröffentlicht werden dürfen")
    data_optin = BooleanField("Ich bin einverstanden das meine Kontaktdaten mit anderen Mitgliedern geteilt werden")
    password  = PasswordField("Neues Passwort", validators=[InputRequired(),validate_password, EqualTo('password_repeat')])
    password_repeat = PasswordField("Password widerholen", validators=[InputRequired()])
    submit = SubmitField("Registrieren")


class ConfirmEmailForm(FlaskForm):
    """
    E-Mail-Adresse nach der Registrierung bestaetigen
    """
    password = PasswordField("Passwort aus der Registrierung", validators=[InputRequired()])
    submit = SubmitField("E-Mail-Adresse bestätigen")
