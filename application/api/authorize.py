"""
Anmeldung der App: sie oeffnet /app/authorize?state=... im Systembrowser
(ASWebAuthenticationSession). Der Nutzer meldet sich wie gewohnt an, auch mit
Zwei-Faktor, bestaetigt mit dem Passwort und die Seite leitet mit dem
Schluessel nach tourentool://auth zurueck. Der Schluessel erscheint nie auf
einer Seite.
"""
# pylint: disable=no-member
from urllib.parse import urlencode

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from application import limiter
from application.auth.views import LOGIN_LIMIT
from application.models.api_token import ApiToken
from application.models.section import current_section

APP_LOGIN = Blueprint('APP_LOGIN', __name__)

# Fest, damit niemand den Schluessel an ein fremdes Schema umleiten kann
APP_SCHEME = 'tourentool'


@APP_LOGIN.route('/app/authorize', methods=['GET', 'POST'])
@login_required
@limiter.limit(LOGIN_LIMIT, methods=['POST'])
def authorize():
    """App verbinden: Passwort bestaetigen, dann mit Schluessel zur App"""
    state = (request.values.get('state') or '').strip()[:128]
    name = (request.values.get('name') or 'Tourentool-App').strip()[:100] or 'Tourentool-App'
    if not state:
        abort(400)
    if request.method == 'POST':
        if current_user.check_password(request.form.get('password') or ''):
            _token, raw = ApiToken.issue(current_user._get_current_object(),  # pylint: disable=protected-access
                                         current_section(), name)
            return redirect(f"{APP_SCHEME}://auth?{urlencode({'token': raw, 'state': state})}")
        flash("Das Passwort stimmt nicht.", 'danger')
    return render_template('app_authorize.html', state=state, name=name)


@APP_LOGIN.route('/user/apps/<token_id>/revoke', methods=['POST'])
@login_required
def revoke(token_id):
    """Zugang einer App im Profil beenden. Der Schluessel wird ungueltig, der Eintrag bleibt nicht."""
    ApiToken.objects(id=token_id, user=current_user.id).delete()
    flash("Die App ist abgemeldet.", 'info')
    return redirect(url_for('USER.page_user_profil') + '#apps')
