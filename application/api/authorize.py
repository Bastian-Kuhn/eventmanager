"""
Anmeldung der App: sie oeffnet /app/authorize?state=... im Systembrowser
(ASWebAuthenticationSession). Der Nutzer meldet sich wie gewohnt an, auch mit
Zwei-Faktor, bestaetigt mit dem Passwort und die Seite leitet mit dem
Schluessel nach tourentool://auth zurueck. Der Schluessel erscheint nie auf
einer Seite.

Mit site=<id> meldet sich ein Bucher fuer eine Website mit dem
WordPress-Plugin an: ohne Passwort, nur bestaetigen, dann zurueck zur
hinterlegten Adresse der Website mit einem Einmal-Code, den die Website
selbst gegen einen Bucher-Schluessel tauscht.
"""
# pylint: disable=no-member
from urllib.parse import urlencode

from bson import ObjectId

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from application import limiter
from application.auth.views import LOGIN_LIMIT
from application.models.api_token import ApiToken
from application.models.section import current_section
from application.models.website import Website, WebsiteCode

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
    site_id = request.values.get('site') or ''
    if site_id:
        website = Website.objects(id=site_id, section=current_section().pk).first() \
            if ObjectId.is_valid(site_id) else None
        if website is None:
            abort(404)
        return authorize_website(website, state)
    if request.method == 'POST':
        if current_user.check_password(request.form.get('password') or ''):
            _token, raw = ApiToken.issue(current_user._get_current_object(),  # pylint: disable=protected-access
                                         current_section(), name)
            return redirect(f"{APP_SCHEME}://auth?{urlencode({'token': raw, 'state': state})}")
        flash("Das Passwort stimmt nicht.", 'danger')
    return render_template('app_authorize.html', state=state, name=name)


def authorize_website(website, state):
    """Bucher einer Website: bestaetigen, dann mit Code zur Website"""
    if request.method == 'POST':
        code = WebsiteCode.issue(website, current_user._get_current_object())  # pylint: disable=protected-access
        sep = '&' if '?' in website.return_url else '?'
        return redirect(f"{website.return_url}{sep}{urlencode({'code': code, 'state': state})}")
    return render_template('app_authorize_website.html', state=state, website=website)


@APP_LOGIN.route('/user/apps/<token_id>/revoke', methods=['POST'])
@login_required
def revoke(token_id):
    """Zugang einer App im Profil beenden. Der Schluessel wird ungueltig, der Eintrag bleibt nicht."""
    ApiToken.objects(id=token_id, user=current_user.id).delete()
    flash("Die App ist abgemeldet.", 'info')
    return redirect(url_for('USER.page_user_profil') + '#apps')
