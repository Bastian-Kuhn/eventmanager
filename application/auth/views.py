"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member
import hmac
from datetime import datetime
from datetime import timedelta
from flask import request, render_template, current_app, \
     flash, redirect, session, Blueprint, url_for
from flask_login import current_user, login_user, logout_user, login_required
from authlib.jose import jwt, JoseError
from limits import parse
from werkzeug.security import generate_password_hash, check_password_hash

from application import limiter, login_manager
from application.models.user import User
from application.auth.forms import LoginForm, RequestPasswordForm, ResetPasswordForm
from application.user.forms import ChangePasswordForm
from application.modules.email import send_email

def _(text):
    """
    Have to replaced by message translator
    """
    return text


AUTH = Blueprint('auth', __name__)

# Pro IP. Gilt auch fuer das Login-Formular auf der Eventseite.
LOGIN_LIMIT = "10/minute;60/hour"
PASSWORD_LIMIT = "10/minute"
# Pro E-Mail-Adresse, egal von welcher IP: danach ist das Konto voruebergehend
# gesperrt. Zeitlich begrenzt, damit niemand ein fremdes Konto dauerhaft sperrt.
ACCOUNT_LOCK = parse("10 per 15 minutes")
ACCOUNT_LOCK_KEY = "login-fail"

# Unbekannte E-Mail kostet so viel Zeit wie ein falsches Passwort
_DUMMY_HASH = generate_password_hash("kein-echtes-passwort")


@AUTH.before_app_request
def enforce_password_change():
    """
    Wer sein Passwort aendern muss, kommt erst danach an den Rest der Seite
    """
    if request.endpoint in ('static', 'auth.change_password', 'auth.logout'):
        return None
    if current_user.is_authenticated and current_user.force_password_change:
        return redirect(url_for('auth.change_password'))
    return None


def refresh_login(user):
    """
    Nach einem Passwortwechsel neu einloggen: die bisherige Session gilt nicht mehr
    """
    login_user(user, remember='remember_token' in request.cookies)


def _account_lock(action, email):
    """
    Fehlversuche pro E-Mail-Adresse im Speicher des Limiters. Ohne Limiter oder
    Redis gibt es keine Sperre, der Login selbst soll daran nicht scheitern.
    """
    if not limiter.enabled:
        return True
    try:
        return getattr(limiter.limiter, action)(ACCOUNT_LOCK, ACCOUNT_LOCK_KEY, email)
    except Exception:  # pylint: disable=broad-except
        return True


def do_login(login_form, context):
    email = login_form.login_email.data.lower()
    password = login_form.password.data
    # Gilt auch fuer unbekannte Adressen, sonst verraet die Sperre, wer ein Konto hat
    if not _account_lock('test', email):
        flash("Zu viele Fehlversuche. Bitte versuche es in 15 Minuten noch einmal "
              "oder setze dein Passwort zurück.", 'danger')
        return False
    user_result = User.objects(email=email, disabled__ne=True)
    remember_login = login_form.remember_login.data
    if user_result:
        existing_user = user_result[0]
    else:
        check_password_hash(_DUMMY_HASH, password)
        existing_user = False

    if not (existing_user and existing_user.check_password(password)):
        _account_lock('hit', email)
        flash("E-Mail oder Passwort ist falsch", 'danger')
        return False

    if existing_user.email_unconfirmed:
        flash("Bitte bestätige zuerst deine E-Mail-Adresse über den Link, "
              "den wir dir nach der Registrierung geschickt haben.", 'danger')
        return False

    login_user(
        existing_user,
        remember=remember_login,
        duration=timedelta(
            days=(current_app.config.get('REMEMBER_COOKIE_DAYS') or 30)
        )
    )
    existing_user.last_login = datetime.now()
    existing_user.save()
    return True

@login_manager.user_loader
def load_user(session_id):
    """
    Flask Login: Load User from Database. Gesperrte oder geloeschte User und
    Sessions von vor dem letzten Passwortwechsel gelten als nicht eingeloggt.
    """
    user_id, _, fingerprint = session_id.partition('|')
    user = User.objects(id=user_id, disabled__ne=True).first()
    if not user or not hmac.compare_digest(user.password_fingerprint(), fingerprint):
        return None
    return user

@AUTH.route('/login', methods=['GET', 'POST'])
@limiter.limit(LOGIN_LIMIT, methods=['POST'])
def login():
    """
    Login Route and Handling
    """
    if current_user.is_authenticated:
        flash('Already Logged in')
        return redirect("/")

    login_form = LoginForm(request.form)

    context = {
        'LoginForm' : login_form,
    }
    if login_form.login_submit.data and login_form.validate_on_submit():
        login = do_login(login_form, context)
        if login:
            return redirect(url_for('EVENTS.page_list'))

    return render_template('login.html', **context)

@AUTH.route('/logout')
@login_required
def logout():
    """
    Session cleanup and logout
    """
    session.clear()
    logout_user()
    return redirect("/")


@AUTH.route('/change-password', methods=['GET', 'POST'])
@login_required
@limiter.limit(PASSWORD_LIMIT, methods=['POST'])
def change_password():
    """
    Change Password Route
    """
    form = ChangePasswordForm(request.form)
    if current_user.force_password_change and request.method == 'GET':
        flash("Bitte ändere dein Passwort", 'danger')
    if form.validate_on_submit():
        if not current_user.check_password(form.old_password.data):
            flash("Aktuelles Passwort ist falsch", 'danger')
            return render_template('formular.html', form=form)
        current_user.set_password(form.password.data)
        current_user.force_password_change = False
        current_user.save()
        refresh_login(current_user._get_current_object())  # pylint: disable=protected-access
        return redirect('/')
    if form.errors:
        for _, message in form.errors.items():
            flash(message[0], 'danger')

    return render_template('formular.html', form=form)

@AUTH.route('/request-password', methods=['GET', 'POST'])
@limiter.limit("5/minute;20/hour", methods=['POST'])
def request_password():
    """
    Password Request Page
    """
    form = RequestPasswordForm(request.form)
    if form.validate_on_submit():
        email = form.request_email.data.lower()
        user_result = User.objects(email=email, disabled__ne=True)
        if user_result:
            existing_user = user_result[0]
            token = existing_user.generate_token()
            send_email(existing_user.email, "Neues Passwort", 'email/resetpassword',
                       user=existing_user, token=token)
        flash("Ein Link zum zurückseten deines Passworts wurde an dich gesendet.", 'info')
        return redirect("/")

    return render_template('formular.html', form=form)


def read_reset_token(token):
    """
    Helper to read the claims from a reset token
    """
    key = current_app.config['SECRET_KEY']
    try:
        data = jwt.decode(token, key)
        if 'exp' in data:
            now = datetime.utcnow().timestamp()
            if now >  data['exp']:
                raise ValueError("Token Expired")

    except JoseError as error:
        raise ValueError("Der Link ist ungültig oder abgelaufen") from error
    return data

@AUTH.route('/reset-password/<token>', methods=['GET', 'POST'])
@limiter.limit(PASSWORD_LIMIT, methods=['POST'])
def reset_password(token):
    """ Reset Password Route"""
    form = ResetPasswordForm(request.form)
    invalid_link = "Der Link ist ungültig oder abgelaufen"
    try:
        claims = read_reset_token(token)
    except ValueError:
        flash(invalid_link, "danger")
        return redirect("/")

    # Der Fingerprint macht den Link nach dem ersten Passwortwechsel ungueltig
    existing_user = User.objects(id=claims.get('userid'), disabled__ne=True).first()
    if not existing_user or claims.get('pwd') != existing_user.password_fingerprint():
        flash(invalid_link, "danger")
        return redirect("/")

    if form.validate_on_submit():
        password = request.form.get('password')
        existing_user.set_password(password)
        existing_user.last_login = datetime.now()
        existing_user.force_password_change = False
        # Der Link kam per Mail, das Postfach ist damit bestaetigt
        existing_user.email_unconfirmed = False
        existing_user.save()
        _account_lock('clear', existing_user.email)
        login_user(
            existing_user,
            duration=timedelta(
                hours=(current_app.config['ADMIN_SESSION_HOURS'] or 8)
            )
        )
        flash("Password Changed", 'success')
        return redirect("/")
    if form.errors:
        for _, message in form.errors.items():
            flash(message[0], 'danger')

    return render_template('formular.html', form=form)
