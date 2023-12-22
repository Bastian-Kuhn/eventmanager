"""
Frontend User
"""
# pylint: disable=no-member
from application import limiter
from application.models.user import User

from flask import request, render_template, current_app, \
     flash, redirect, session, Blueprint, url_for
from flask_login import current_user, login_required
from .forms import NewUserForm


USER = Blueprint('USER', __name__)


@USER.route('/user/profile')
def page_user_profil():
    """
    User profile
    """

    context = {}

    return render_template('events_list.html', **context)

@USER.route('/user/create', methods=['POST', 'GET'])
@limiter.limit("4/min 1/sec")
def page_user_create():
    """
    Create New User
    """

    form = NewUserForm(request.form)
    if form.validate_on_submit():
        new_user = User()
        for field, value in dict(request.form).items():
            if field in ['password', 'password_repeat']:
                continue
            if field in ['media_optin', 'data_optin']:
                value = bool(value)
            if field == 'email':
                value = value.lower()
            setattr(new_user, field, value)
        new_user.set_password(request.form['password'])
        new_user.save()
        flash("Anmeldung erfolgreich", 'info')
        return redirect(url_for('auth.login'))

    if form.errors:
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

    context = {
        'form': form
    }

    return render_template('formular.html', **context)
