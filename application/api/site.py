"""
/api/site/v1: Zugang des WordPress-Plugins mit dem Schluessel der Website
(Authorization: Bearer tts_...). Touren ohne Konto lesen, einen Anmelde-Code
gegen einen Bucher-Schluessel tauschen und, wenn die Sektion es erlaubt,
WordPress-Konten anmelden (SSO). Gebucht wird danach ueber /api/v1 mit dem
Bucher-Schluessel.
"""
# pylint: disable=no-member, protected-access
import re
from datetime import datetime

from flask import Blueprint, g, jsonify, request
from mongoengine.errors import NotUniqueError
from mongoengine.queryset.visitor import Q

from application import csrf, limiter, login_manager
from application.api import v1
from application.api.v1 import fail
from application.auth.views import LOGIN_LIMIT
from application.events.models import Event
from application.models.api_token import ApiToken, token_hash
from application.models.section import current_section, ref_id
from application.models.user import Membership, User
from application.models.website import Website, WebsiteCode, WebsiteLink

SITE_API = Blueprint('SITE_API', __name__, url_prefix='/api/site/v1')
csrf.exempt(SITE_API)

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def site_limit_key():
    """Rate-Limits pro Website-Schluessel"""
    raw = v1.bearer()
    return f"site:{token_hash(raw)}" if raw else f"ip:{request.remote_addr}"


@SITE_API.before_request
def site_login():
    """Website aus dem Schluessel, nie ein Konto aus der Session"""
    g._login_user = login_manager.anonymous_user()
    raw = v1.bearer()
    website = Website.objects(key_hash=token_hash(raw)).first() if raw.startswith('tts_') else None
    if not website or ref_id(website._data.get('section')) != str(current_section().pk):
        return fail(401, "Der Schlüssel der Website stimmt nicht.")
    g.website = website
    now = datetime.now()
    if not website.last_used or (now - website.last_used).total_seconds() > 3600:
        website.update(set__last_used=now)
    return None


@SITE_API.route('/site')
def site():
    """Die Website und ihre Sektion: id fuer den Login-Link, ob SSO an ist"""
    section = current_section()
    return jsonify({'id': str(g.website.pk), 'name': g.website.name, 'sso': g.website.sso_mode != 'off',
                    'return_url': g.website.return_url,
                    'section': {'name': section.name, 'slug': section.slug,
                                'color': section.nav_background_color or None},
                    'categories': v1.categories()})


@SITE_API.route('/events')
def events():
    """Kommende Touren der Sektion samt geteilter, ohne Konto"""
    now = datetime.now()
    found = Event.visible(Q(end_date__gte=now) | (Q(end_date=None) & Q(start_date__gte=now))) \
        .order_by('start_date').exclude('costs')
    return jsonify({'events': [v1.event_summary(x, now) for x in found]})


@SITE_API.route('/events/<event_id>')
def event(event_id):
    """Eine Tour, ohne Konto"""
    found = v1.visible_event(event_id)
    if not found:
        return fail(404, "Diese Tour gibt es nicht (mehr).")
    return jsonify(v1.event_detail(found, datetime.now()))


def booker_token(user, website):
    """Bucher-Schluessel; ein frueherer derselben Website fuer das Konto endet damit"""
    ApiToken.objects(user=user.id, website=website.pk).delete()
    _token, raw = ApiToken.issue(user, current_section(), website.name, website=website)
    return raw


@SITE_API.route('/token', methods=['POST'])
@limiter.limit(LOGIN_LIMIT, key_func=site_limit_key)
def token():
    """Code aus dem Ruecksprung gegen einen Bucher-Schluessel tauschen (einmal)"""
    raw = str((request.get_json(silent=True) or {}).get('code') or '')
    code = WebsiteCode.objects(code_hash=token_hash(raw), website=g.website.pk).first() if raw else None
    # Loeschen vor dem Pruefen: ein Code gilt genau einmal
    if not code or not WebsiteCode.objects(id=code.id).delete():
        return fail(400, "Die Anmeldung ist abgelaufen, bitte noch einmal.")
    user = User.objects(id=ref_id(code._data.get('user')), disabled__ne=True).first()
    if code.expires < datetime.now() or not user:
        return fail(400, "Die Anmeldung ist abgelaufen, bitte noch einmal.")
    return jsonify({'token': booker_token(user, g.website)})


def sso_payload():
    """WordPress-Konto aus der Anfrage: (wp_user, email, Vorname, Nachname) oder None"""
    payload = request.get_json(silent=True) or {}
    wp_user = str(payload.get('wp_user') or '').strip()[:64]
    email = str(payload.get('email') or '').strip().lower()
    if not wp_user or len(email) > 254 or not EMAIL_RE.match(email):
        return None
    return (wp_user, email, str(payload.get('first_name') or '').strip()[:100],
            str(payload.get('last_name') or '').strip()[:100])


def may_link(user):
    """Globale Admins und gesperrte Konten verknuepft keine Website"""
    return user is not None and not user.global_admin and not user.disabled


def create_booker(email, first_name, last_name):
    """Neues Konto fuer ein WordPress-Konto, Mitglied der Sektion, ohne Passwort"""
    user = User(email=email, first_name=first_name, last_name=last_name, date_added=datetime.now())
    user.memberships = [Membership(section=current_section())]
    try:
        return user.save()
    except NotUniqueError:
        return User.objects(email=email).first()


@SITE_API.route('/sso', methods=['POST'])
@limiter.limit("60/minute", key_func=site_limit_key)
def sso():
    """
    WordPress-Konto anmelden. Antwort {'token'} oder 202 {'pending': true},
    wenn ein Admin die Verknuepfung erst freigeben muss. Was neu verknuepft
    wird, entscheidet sso_mode der Website.
    """
    website = g.website
    if website.sso_mode == 'off':
        return fail(403, "Die Anmeldung mit dem WordPress-Konto ist für diese Website aus.")
    data = sso_payload()
    if not data:
        return fail(400, "WordPress-Konto ohne gültige E-Mail-Adresse.")
    wp_user, email, first_name, last_name = data
    link = WebsiteLink.objects(website=website.pk, wp_user=wp_user).first()
    if link is None:
        existing = User.objects(email=email).first()
        if website.sso_mode == 'existing' and not existing:
            return fail(403, "Zu dieser E-Mail-Adresse gibt es bei der Sektion kein Konto.")
        if existing is not None and not may_link(existing):
            return fail(403, "Dieses Konto lässt sich nicht mit einer Website verbinden.")
        if website.sso_mode == 'auto' and not existing:
            existing = create_booker(email, first_name, last_name)
        link = WebsiteLink(website=website, wp_user=wp_user, user=existing, email=email,
                           first_name=first_name, last_name=last_name,
                           active=website.sso_mode != 'approval')
        try:
            link.save()
        except NotUniqueError:
            link = WebsiteLink.objects(website=website.pk, wp_user=wp_user).first()
    if not link.active:
        return jsonify({'pending': True}), 202
    user = User.objects(id=ref_id(link._data.get('user')), disabled__ne=True).first()
    if not may_link(user):
        return fail(403, "Dieses Konto lässt sich nicht mit einer Website verbinden.")
    if not user.may_use_section(current_section()):
        user.request_join(current_section())
        return jsonify({'pending': True}), 202
    return jsonify({'token': booker_token(user, website)})
