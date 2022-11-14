from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, DateField
from wtforms.validators import InputRequired, Email, EqualTo, ValidationError
from mongoengine.errors import DoesNotExist

from application.models.user import User
from application.auth.forms import validate_password


def validate_user(form, field):
    """
    Check if the user can be registered
    """
    email = field.data
    try:
        User.objects.get(email=email)
    except DoesNotExist:
        return

    raise ValidationError("Eine User mit dieser E-Mail Adresse exsitiert bereits")


class NewUserForm(FlaskForm):
    """
    Signup Form for User
    """
    first_name = StringField("Vorname", validators=[InputRequired()])
    last_name = StringField("Nachname", validators=[InputRequired()])
    email = StringField("E-Mail", validators=[Email(), InputRequired(), validate_user])
    phone = StringField("Handynummer", validators=[InputRequired()])
    birthdate = DateField("Geburstdatum", validators=[InputRequired()])
    club_member = BooleanField("Vereins Mitglied")
    media_optin = BooleanField("Von mir drürfen Fotos und Videos erstellt werden")
    data_optin = BooleanField("Ich bin einverstanden das meine Kontaktdaten mit anderen Mitgliedern geteilt werden")
    password  = PasswordField("Neues Passwort", validators=[InputRequired(),validate_password, EqualTo('password_repeat')])
    password_repeat = PasswordField("Password widerholen", validators=[InputRequired()])
    submit = SubmitField("Registrieren")
