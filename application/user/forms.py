from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, DateField
from wtforms.validators import InputRequired, Email, EqualTo

from application.auth.forms import validate_password


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


class NewUserForm(FlaskForm):
    """
    Signup Form for User
    """
    first_name = StringField("Vorname", validators=[InputRequired()])
    last_name = StringField("Nachname", validators=[InputRequired()])
    email = StringField("E-Mail", validators=[Email(), InputRequired()])
    phone = StringField("Handynummer", validators=[InputRequired()])
    birthdate = DateField("Geburstdatum", validators=[InputRequired()])
    club_id = StringField("Mitgliedsnummer")
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
