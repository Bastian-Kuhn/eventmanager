"""
Formulare des Portals
"""
import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import FieldList, FormField, IntegerField, PasswordField, StringField, \
    SubmitField, TextAreaField, BooleanField
from wtforms.validators import Email, EqualTo, InputRequired, NumberRange, Optional, \
    Regexp, ValidationError
from wtforms.widgets import ColorInput

from application.auth.forms import validate_password
from application.events.forms import max_length, EMAIL_MAX
from application.models.section import SLUG_PATTERN
from application.user.forms import NAME_MAX, NAME_VALIDATORS

# Subdomains, die keine Sektion bekommt
RESERVED_SLUGS = {'www', 'sektion', 'verein', 'portal', 'admin', 'api', 'app', 'mail', 'smtp',
                  'docs', 'static', 'test', 'demo', 'status'}
SECTION_NAME_MAX = 100
COLOR_PATTERN = r'^#[0-9a-fA-F]{6}$'


def validate_slug(_form, field):
    field.data = (field.data or '').strip().lower()
    if not re.fullmatch(SLUG_PATTERN, field.data) or len(field.data) < 3:
        raise ValidationError("3 bis 40 Zeichen: Kleinbuchstaben, Ziffern und Bindestrich, "
                              "am Anfang ein Buchstabe oder eine Ziffer")
    if field.data in RESERVED_SLUGS:
        raise ValidationError("Dieser Kurzname ist reserviert")


MEMBER_COUNT = [InputRequired(), NumberRange(min=1, max=1000000, message="Bitte eine Zahl ab 1")]


class RegisterForm(FlaskForm):
    """Sektion registrieren, fuer Gaeste mit neuem Admin-Konto"""
    section_name = StringField("Name der Sektion", validators=[InputRequired(), max_length(SECTION_NAME_MAX)],
                               render_kw={'maxlength': SECTION_NAME_MAX, 'autocomplete': 'organization'})
    slug = StringField("Kurzname (wird die Adresse)", validators=[InputRequired(), validate_slug],
                       render_kw={'maxlength': 40, 'autocapitalize': 'none', 'spellcheck': 'false'})
    member_count = IntegerField("Zahl der Mitglieder", validators=MEMBER_COUNT,
                                render_kw={'min': 1, 'inputmode': 'numeric'})
    first_name = StringField("Vorname", validators=NAME_VALIDATORS,
                             render_kw={'maxlength': NAME_MAX, 'autocomplete': 'given-name'})
    last_name = StringField("Nachname", validators=NAME_VALIDATORS,
                            render_kw={'maxlength': NAME_MAX, 'autocomplete': 'family-name'})
    email = StringField("E-Mail", validators=[Email(), InputRequired(), max_length(EMAIL_MAX)],
                        render_kw={'maxlength': EMAIL_MAX, 'autocomplete': 'email'})
    password = PasswordField("Passwort", validators=[InputRequired(), validate_password,
                                                     EqualTo('password_repeat')],
                             render_kw={'autocomplete': 'new-password'})
    password_repeat = PasswordField("Passwort wiederholen", validators=[InputRequired()],
                                    render_kw={'autocomplete': 'new-password'})
    submit = SubmitField("Sektion registrieren")


class RegisterLoggedInForm(FlaskForm):
    """Sektion registrieren mit dem angemeldeten Konto als Admin"""
    section_name = RegisterForm.section_name
    slug = RegisterForm.slug
    member_count = RegisterForm.member_count
    submit = SubmitField("Sektion registrieren")


class CategoryForm(FlaskForm):
    """Eine Kategorie mit Farbe, leerer Name = keine"""
    class Meta:  # pylint: disable=too-few-public-methods
        csrf = False
    name = StringField("Name", validators=[Optional(), max_length(50)], render_kw={'maxlength': 50})
    color = StringField("Farbe", widget=ColorInput(), default='#5aaa28',
                        validators=[Optional(), Regexp(COLOR_PATTERN, message="Farbe als #rrggbb")])


class SettingsForm(FlaskForm):
    """Einstellungen einer Sektion"""
    name = StringField("Name der Sektion", validators=[InputRequired(), max_length(SECTION_NAME_MAX)],
                       render_kw={'maxlength': SECTION_NAME_MAX})
    member_count = IntegerField("Zahl der Mitglieder", validators=MEMBER_COUNT,
                                render_kw={'min': 1, 'inputmode': 'numeric'})
    nav_background_color = StringField("Farbe der Navigation", widget=ColorInput(),
                                       validators=[Optional(), Regexp(COLOR_PATTERN, message="Farbe als #rrggbb")])
    logo = FileField("Neues Logo (PNG oder JPG)", validators=[FileAllowed(['png', 'jpg', 'jpeg'], "Nur PNG oder JPG")])
    homepage_link = StringField("Link hinter dem Logo (Homepage der Sektion)",
                                validators=[Optional(), max_length(300), Regexp(r'^https?://', message="Bitte mit https:// angeben")],
                                render_kw={'maxlength': 300, 'inputmode': 'url'})
    categories = FieldList(FormField(CategoryForm), label="Kategorien")
    media_consent_text = TextAreaField("Einwilligungstext Foto/Video (HTML, leer = Standardtext)",
                                       validators=[Optional(), max_length(20000)], render_kw={'rows': 8})
    require_approval = BooleanField("Neue Mitglieder erst nach Freigabe durch die Admins")
    own_mail = BooleanField("Eigenen Mailserver verwenden")
    mail_server = StringField("Mailserver (SMTP mit SSL, Port 465)",
                              validators=[Optional(), max_length(200)], render_kw={'maxlength': 200})
    mail_username = StringField("Benutzer Mailserver", validators=[Optional(), max_length(200)])
    mail_password = PasswordField("Passwort Mailserver (leer = unverändert)", validators=[Optional(), max_length(200)],
                                  render_kw={'autocomplete': 'new-password'})
    mail_use_tls = BooleanField("TLS am Mailserver aktivieren")
    mail_sender = StringField("Absender (leer = Benutzer)", validators=[Optional(), Email(), max_length(EMAIL_MAX)])
    submit = SubmitField("Speichern")
