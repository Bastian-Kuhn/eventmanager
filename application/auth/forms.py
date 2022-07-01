from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, RadioField, SubmitField, BooleanField, \
                    TextAreaField, HiddenField, DateField, SelectMultipleField
from wtforms.validators import InputRequired, Email, EqualTo, ValidationError
from wtforms.widgets import TextArea
from flask import current_app
import re


def validate_password(form, field):
    password = field.data
    if hasattr(form, 'old_password') and form.old_password.data == password:
        raise ValidationError("Bitte neues Passwort")

    passwd_length = current_app.config['PASSWD_MIN_PASSWD_LENGTH']
    if len(password) < passwd_length:
        raise ValidationError("Passwort ist zu kurz")

    not_matching = 0
    total_tests = 0
    if current_app.config['PASSWD_SPECIAL_CHARS']:
        total_tests += 1
        if not re.search(r"[ ?!#$%&'()*+,-./[\\\]^_`{|}~"+r'"]', password):
            not_matching += 1

    if current_app.config['PASSWD_SPECIAL_DIGITS']:
        total_tests += 1
        if not re.search(r"\d", password):
            not_matching += 1

    if current_app.config['PASSWD_SEPCIAL_UPPER']:
        total_tests += 1
        if not re.search(r"[A-Z]", password):
            not_matching += 1

    if current_app.config['PASSWD_SEPCIAL_LOWER']:
        total_tests += 1
        if not re.search(r"[a-z]", password):
            not_matching += 1
    matches = total_tests - not_matching

    if matches <= current_app.config['PASSWD_SPECIAL_NEEDED']:
        raise ValidationError("Unsicheres Password, zahlen und sonderzeichen erforderlich")

class LoginForm(FlaskForm):
    login_email = StringField("E-Mail", [InputRequired(), Email()])
    password = PasswordField("Passwort", [InputRequired()])
    remember_login = BooleanField("Angemeldet bleiben")
    login_submit   = SubmitField("Anmelden")

class RequestPasswordForm(FlaskForm):
    request_email = StringField("E-Mail", [InputRequired(), Email()])
    request_submit   = SubmitField("Senden")

class ResetPasswordForm(FlaskForm):
    password  = PasswordField("Neues Passwort", validators=[InputRequired(),validate_password, EqualTo('password_repeat')])
    password_repeat = PasswordField("Password widerholen", validators=[InputRequired()])
    submit = SubmitField("Senden")
