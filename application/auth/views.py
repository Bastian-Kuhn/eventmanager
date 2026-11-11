"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member
import hmac
import time
from datetime import datetime
from datetime import timedelta
from flask import request, render_template, current_app, \
     flash, redirect, session, Blueprint, url_for, make_response
from flask_login import current_user, login_user, logout_user, login_required
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey
from limits import parse
from werkzeug.security import generate_password_hash, check_password_hash

from application import limiter, login_manager
from application.models.user import User
from application.auth.forms import LoginForm, RequestPasswordForm, ResetPasswordForm, \
    TwoFactorForm, TwoFactorSetupForm, TwoFactorDisableForm
from application.user.forms import ChangePasswordForm
from application.modules import tfa
from application.modules.email import send_email
from application.modules.sanitize import is_local_path

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
# Pro Empfaengeradresse: Bestaetigungs- und Reset-Mails zusammen
MAIL_LIMIT = parse("3 per hour")
MAIL_LIMIT_KEY = "mail-to"

LOCK_MESSAGE = ("Zu viele Fehlversuche. Bitte versuche es in 15 Minuten noch einmal "
                "oder setze dein Passwort zurück.")

# Nach dem Passwort bleibt so lange Zeit fuer den Code aus der App
TFA_LOGIN_SECONDS = 300
TFA_SESSION_KEY = 'tfa_login'
# Rueckgabe von do_login: Passwort stimmt, es fehlt noch der zweite Faktor
TFA_PENDING = 'tfa'
# Ohne eingerichtete 2FA erreicht ein Admin bei TFA_REQUIRED_FOR_ADMINS nur diese Seiten
TFA_SETUP_ENDPOINTS = ('static', 'auth.logout', 'auth.change_password', 'auth.tfa_settings',
                       'auth.tfa_setup', 'auth.tfa_confirm')

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


@AUTH.before_app_request
def enforce_admin_tfa():
    """
    Mit TFA_REQUIRED_FOR_ADMINS muessen Admins erst die 2FA einrichten
    """
    if not current_app.config.get('TFA_REQUIRED_FOR_ADMINS'):
        return None
    if request.endpoint in TFA_SETUP_ENDPOINTS:
        return None
    if current_user.is_authenticated and current_user.is_admin() \
            and not current_user.tfa_enabled:
        flash("Für Admin-Konten ist die Zwei-Faktor-Anmeldung Pflicht. "
              "Bitte richte sie jetzt ein.", 'warning')
        return redirect(url_for('auth.tfa_settings'))
    return None


def refresh_login(user):
    """
    Nach einem Passwortwechsel neu einloggen: die bisherige Session gilt nicht mehr
    """
    login_user(user, remember='remember_token' in request.cookies)


def _limit_per_email(action, limit, key, email):
    """
    Zaehler pro E-Mail-Adresse im Speicher des Limiters. Ohne Limiter oder
    Redis gibt es keine Grenze, Login und Mails sollen daran nicht scheitern.
    """
    if not limiter.enabled:
        return True
    try:
        return getattr(limiter.limiter, action)(limit, key, email)
    except Exception:  # pylint: disable=broad-except
        return True


def _account_lock(action, email):
    """
    Fehlversuche pro E-Mail-Adresse
    """
    return _limit_per_email(action, ACCOUNT_LOCK, ACCOUNT_LOCK_KEY, email)


def mail_allowed(email):
    """
    Zaehlt eine Mail an diese Adresse. False, wenn die Adresse in letzter
    Zeit schon genug Mails bekommen hat, egal wer sie angefordert hat.
    """
    return _limit_per_email('hit', MAIL_LIMIT, MAIL_LIMIT_KEY, email)


def _finish_login(user, remember):
    """Einloggen, nachdem alle Faktoren geprueft sind"""
    login_user(
        user,
        remember=remember,
        duration=timedelta(
            days=(current_app.config.get('REMEMBER_COOKIE_DAYS') or 30)
        )
    )
    user.last_login = datetime.now()
    user.save()
    # Ein Konto gilt ueberall: wer sich bei einer Sektion anmeldet, steht
    # danach in deren Mitgliederliste (ohne Rolle)
    user.join_section()


def do_login(login_form, context, next_url=None):  # pylint: disable=unused-argument
    """
    Passwort pruefen und einloggen. Mit 2FA gibt es TFA_PENDING zurueck: der
    Aufrufer leitet dann zur Code-Eingabe (auth.login_2fa) weiter, die danach
    auf next_url fuehrt.
    """
    email = login_form.login_email.data.lower()
    password = login_form.password.data
    # Gilt auch fuer unbekannte Adressen, sonst verraet die Sperre, wer ein Konto hat
    if not _account_lock('test', email):
        flash(LOCK_MESSAGE, 'danger')
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
              "den wir dir nach der Registrierung geschickt haben. Keine Mail bekommen? "
              "Unter „Bestätigungsmail erneut senden“ forderst du sie neu an.", 'danger')
        return False

    if existing_user.tfa_enabled:
        # Der Fingerprint macht den Zwischenschritt mit einem Passwortwechsel ungueltig
        session[TFA_SESSION_KEY] = {
            'user': str(existing_user.id),
            'pwd': existing_user.password_fingerprint(),
            'remember': bool(remember_login),
            'next': next_url if is_local_path(next_url) else None,
            'until': time.time() + TFA_LOGIN_SECONDS,
        }
        return TFA_PENDING

    _finish_login(existing_user, remember_login)
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
        if login == TFA_PENDING:
            return redirect(url_for('auth.login_2fa'))
        if login:
            return redirect(url_for('EVENTS.page_list'))

    return render_template('login.html', **context)


def _pending_tfa_login():
    """
    Konto und Daten aus dem ersten Login-Schritt, solange er gilt
    """
    pending = session.get(TFA_SESSION_KEY)
    if not isinstance(pending, dict) or pending.get('until', 0) < time.time():
        session.pop(TFA_SESSION_KEY, None)
        return None, None
    user = User.objects(id=pending.get('user'), disabled__ne=True).first()
    if not user or not user.tfa_enabled or \
            not hmac.compare_digest(user.password_fingerprint(), str(pending.get('pwd'))):
        session.pop(TFA_SESSION_KEY, None)
        return None, None
    return user, pending


@AUTH.route('/login/2fa', methods=['GET', 'POST'])
@limiter.limit(LOGIN_LIMIT, methods=['POST'])
def login_2fa():
    """
    Zweiter Login-Schritt: Code aus der App oder Wiederherstellungscode.
    Falsche Codes zaehlen in dieselbe Kontosperre wie falsche Passwoerter.
    """
    if current_user.is_authenticated:
        return redirect('/')
    user, pending = _pending_tfa_login()
    if not user:
        flash("Bitte melde dich erneut an.", 'info')
        return redirect(url_for('auth.login'))

    form = TwoFactorForm(request.form)
    error = None
    use_recovery = bool(form.recovery_code.data)
    if form.validate_on_submit():
        entered = form.recovery_code.data if use_recovery else form.code.data
        if not _account_lock('test', user.email):
            error = LOCK_MESSAGE
        elif not (entered or '').strip():
            error = "Bitte gib einen Code ein."
        elif user.check_tfa(entered):
            session.pop(TFA_SESSION_KEY, None)
            _finish_login(user, pending.get('remember'))
            if use_recovery:
                flash(f"Wiederherstellungscode verwendet. Du hast noch "
                      f"{len(user.tfa_recovery_codes)} Codes übrig.", 'warning')
            return redirect(pending.get('next') or url_for('EVENTS.page_list'))
        else:
            _account_lock('hit', user.email)
            error = "Der Code ist falsch oder abgelaufen."
    return render_template('tfa_login.html', form=form, error=error, use_recovery=use_recovery)

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
        # Gleiche Antwort auch bei erreichtem Limit, sonst verraet es das Konto
        if user_result and mail_allowed(email):
            existing_user = user_result[0]
            token = existing_user.generate_token()
            send_email(existing_user.email, "Neues Passwort", 'email/resetpassword',
                       user=existing_user, token=token)
        flash("Ein Link zum zurückseten deines Passworts wurde an dich gesendet.", 'info')
        return redirect("/")

    return render_template('password_request.html', form=form)


def read_reset_token(token):
    """
    Helper to read the claims from a reset token
    """
    key = OctKey.import_key(current_app.config['SECRET_KEY'])
    try:
        data = jwt.decode(token, key, algorithms=['HS256']).claims
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
        if existing_user.tfa_enabled:
            # Der Link aus der Mail ersetzt nicht den zweiten Faktor
            flash("Passwort geändert. Bitte melde dich jetzt an.", 'success')
            return redirect(url_for('auth.login'))
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


@AUTH.route('/user/2fa')
@login_required
def tfa_settings():
    """
    Zwei-Faktor-Anmeldung: Status, einrichten, abschalten
    """
    return render_template('tfa_settings.html', disable_form=TwoFactorDisableForm())


@AUTH.route('/user/2fa/setup', methods=['GET', 'POST'])
@login_required
def tfa_setup():
    """
    POST beginnt die Einrichtung mit einem neuen Geheimnis, GET zeigt QR-Code
    und Geheimnis dazu an
    """
    if current_user.tfa_enabled:
        flash("Die Zwei-Faktor-Anmeldung ist schon eingeschaltet.", 'info')
        return redirect(url_for('auth.tfa_settings'))
    if request.method == 'POST':
        current_user.tfa_pending_secret = tfa.encrypt_secret(tfa.new_secret())
        current_user.save()
        return redirect(url_for('auth.tfa_setup'))
    return _render_setup(TwoFactorSetupForm())


def _render_setup(form, error=None):
    """Einrichtungsseite zum vorlaeufigen Geheimnis"""
    secret = tfa.decrypt_secret(current_user.tfa_pending_secret)
    if not secret:
        return redirect(url_for('auth.tfa_settings'))
    uri = tfa.provisioning_uri(secret, current_user.email)
    # Geheimnis in Vierergruppen, leichter abzutippen
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    return _no_store(render_template('tfa_setup.html', form=form, error=error,
                                     qr_svg=tfa.qr_svg(uri), secret=grouped))


def _no_store(body):
    """Seiten mit Geheimnis oder Codes nicht im Browser-Cache ablegen"""
    response = make_response(body)
    response.headers['Cache-Control'] = 'no-store'
    return response


@AUTH.route('/user/2fa/confirm', methods=['POST'])
@login_required
@limiter.limit(PASSWORD_LIMIT)
def tfa_confirm():
    """
    Einrichtung mit einem Code aus der App bestaetigen, danach die
    Wiederherstellungscodes einmalig zeigen
    """
    if current_user.tfa_enabled:
        return redirect(url_for('auth.tfa_settings'))
    form = TwoFactorSetupForm()
    if form.validate_on_submit():
        codes = current_user.enable_tfa(form.code.data)
        if codes:
            return _no_store(render_template('tfa_recovery_codes.html', codes=codes))
    return _render_setup(form, "Der Code passt nicht. Prüfe die Uhrzeit auf deinem Handy "
                               "und gib den aktuell angezeigten Code ein.")


@AUTH.route('/user/2fa/disable', methods=['POST'])
@login_required
@limiter.limit(PASSWORD_LIMIT)
def tfa_disable():
    """
    Abschalten mit Passwort und Code. Falsche Eingaben zaehlen in die Kontosperre.
    """
    form = TwoFactorDisableForm()
    if not current_user.tfa_enabled:
        return redirect(url_for('auth.tfa_settings'))
    error = None
    if form.validate_on_submit():
        if not _account_lock('test', current_user.email):
            error = LOCK_MESSAGE
        # Erst das Passwort: ein falsches verbraucht keinen Wiederherstellungscode
        elif current_user.check_password(form.password.data) \
                and current_user.check_tfa(form.code.data):
            current_user.disable_tfa()
            flash("Die Zwei-Faktor-Anmeldung ist ausgeschaltet.", 'success')
            return redirect(url_for('auth.tfa_settings'))
        else:
            _account_lock('hit', current_user.email)
            error = "Passwort oder Code ist falsch."
    else:
        error = "Bitte gib Passwort und Code ein."
    return render_template('tfa_settings.html', disable_form=form, error=error)
