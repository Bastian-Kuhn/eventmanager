"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member
from datetime import datetime
from datetime import timedelta
from flask import request, render_template, current_app, \
     flash, redirect, session, Blueprint, url_for
from flask_login import current_user, login_user, logout_user, login_required
from authlib.jose import jwt, JoseError
from mongoengine.errors import ValidationError
from bson.errors import InvalidId

from application import login_manager, limiter
from application.models.user import User
from application.auth.forms import LoginForm, RequestPasswordForm, ResetPasswordForm
from application.modules.email import send_email

def _(text):
    """
    Have to replaced by message translator
    """
    return text


AUTH = Blueprint('auth', __name__)


def do_login(login_form, context):
    email = login_form.login_email.data.lower()
    password = login_form.password.data
    user_result = User.objects(email=email, disabled__ne=True)
    remember_login = login_form.remember_login.data
    if user_result:
        existing_user = user_result[0]
    else:
        existing_user = False

    if not (existing_user and existing_user.check_password(password)):
        flash("Wrong Password", 'danger')
        return False

    if existing_user.disabled:
        flash("User Disabled", 'danger')
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
    if existing_user.force_password_change:
        flash("Change Password", 'danger')
        return False
    return True

@login_manager.user_loader
def load_user(user_id):
    """
    Flask Login: Load User from Database

    Muss None liefern (nicht werfen), sonst wird aus einem geloeschten User
    oder einem manipulierten Cookie ein HTTP 500 statt eines sauberen Logouts.
    """
    try:
        return User.objects(id=user_id).first()
    except (ValidationError, InvalidId):
        return None

@AUTH.route('/login', methods=['GET', 'POST'])
@limiter.limit("10/minute;100/hour", methods=['POST'])
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
def change_password():
    """
    Change Password Route
    """
    form = ResetPasswordForm(request.form)
    if form.validate_on_submit():
        password = request.form.get('password')
        current_user.set_password(password)
        current_user.lastLogin = datetime.now()
        current_user.force_password_change = False
        current_user.save()
        return redirect('/')
    if form.errors:
        for _, message in form.errors.items():
            flash(message[0], 'danger')

    return render_template('formular.html', form=form)

@AUTH.route('/request-password', methods=['GET', 'POST'])
@limiter.limit("5/hour", methods=['POST'])
def request_password():
    """
    Password Request Page
    """
    form = RequestPasswordForm(request.form)
    if form.validate_on_submit():
        email = form.request_email.data.lower()
        user_result = User.objects(email=email)
        if user_result:
            existing_user = user_result[0]
            token = existing_user.generate_token()
            send_email(existing_user.email, "Neues Passwort", 'email/resetpassword',
                       user=existing_user, token=token)
        flash("Ein Link zum zurückseten deines Passworts wurde an dich gesendet.", 'info')
        return redirect("/")

    return render_template('formular.html', form=form)


def get_token_data(token):
    """
    Helper to read the verified payload from a reset token
    """
    key = current_app.config['SECRET_KEY']
    try:
        data = jwt.decode(token, key)
        if 'exp' in data:
            now = datetime.utcnow().timestamp()
            if now >  data['exp']:
                raise ValueError("Token Expired")

    except JoseError as error:
        raise ValueError(error)
    return data or {}

@AUTH.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """ Reset Password Route"""
    form = ResetPasswordForm(request.form)
    try:
        token_data = get_token_data(token)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect("/")
    user_id = token_data.get('userid')
    if not user_id:
        flash("Ungültiger Link", "danger")
        return redirect("/")

    user_result = User.objects(id=user_id)
    if user_result:
        existing_user = user_result[0]
    else:
        flash("Unbekannter Benutzer", "danger")
        return redirect("/")

    # Einmalverwendung: sobald das Passwort gesetzt wurde, passt der
    # Fingerabdruck nicht mehr und der Link ist tot.
    if token_data.get('pw') != existing_user.token_fingerprint():
        flash("Dieser Link ist nicht mehr gültig. Bitte fordere einen neuen an.", "danger")
        return redirect("/")

    if form.validate_on_submit():
        password = request.form.get('password')
        existing_user.set_password(password)
        existing_user.lastLogin = datetime.now()
        existing_user.save()
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
