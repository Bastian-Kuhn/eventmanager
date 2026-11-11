""" Main entry """
# pylint: disable=invalid-name
# pylint: disable=wrong-import-position
import os
import time
import locale
locale.setlocale(locale.LC_TIME, 'de_DE.UTF-8')

from datetime import datetime
from dateutil import relativedelta
import base64
import ipaddress

from pprint import pformat
from redis import Redis
from flask import Flask, session, request, send_from_directory, render_template, redirect, url_for, abort
from flask_login import LoginManager, current_user
from flask_request_id_header.middleware import RequestID
from flask_admin import Admin
from flask_admin.menu import MenuLink
from flask_bootstrap import Bootstrap
from flask_mail import Mail
from flask_mongoengine import MongoEngine
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from mongoengine.errors import DoesNotExist, ValidationError
from werkzeug.middleware.proxy_fix import ProxyFix
import application.modules.log as logging
from application.modules.sanitize import sanitize_html, safe_url


app = Flask(__name__)
if os.environ.get('config') == "prod":
    print(" * Loading PROD Config")
    app.config.from_object('application.config.ProductionConfig')
elif os.environ.get('config') in ["prod_compose", "compose"]:
    print(" * Loading PROD Compose Config")
    app.config.from_object('application.config.ComposeProdConfig')
else:
    print(" * Loading Fallback Baseconfig")
    app.config.from_object('application.config.BaseConfig')
    app.jinja_env.auto_reload = True

if os.environ.get('config') == "prod_compose":
    # Hinter nginx mit TLS: nginx setzt X-Forwarded-For/-Proto selbst. Ohne das
    # teilen sich alle Besucher ein Rate-Limit, naemlich das des Docker-Gateways.
    # "compose" bleibt aussen vor, das laeuft lokal ueber HTTP.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.config.update(SESSION_COOKIE_SECURE=True, REMEMBER_COOKIE_SECURE=True)

# Der Fallback aus BaseConfig steht im oeffentlichen Repo
if os.environ.get('config') in ["prod", "prod_compose"] and not os.environ.get('SECRET_KEY'):
    raise RuntimeError("SECRET_KEY fehlt: ohne ihn liessen sich Sessions faelschen")

RequestID(app)
csrf = CSRFProtect(app)

if app.config.get('ENABLE_SENTRY'):
    print(" * ENABLED SENTRY")
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    from sentry_sdk.integrations.redis import RedisIntegration


    def filter_events(event, _hint):
        """
        Filter a list of Exception from sending to sentry
        """
        excp = event.get('exception', {})
        values = excp.get('values', [])
        if values:
            excp_type = values[0]['type']
            if excp_type in [
                    'SignatureExpired',
                    'ConnectionResetError',
                    'RateLimitExceeded',
                    'Unauthorized',
                    'MethodNotAllowed',
                    'timeout',
                ]:
                return None
        return event

    sentry_sdk.init(
        dsn=app.config['SENTRY_DSN'],
        before_send=filter_events,
        integrations=[FlaskIntegration(),
                      RedisIntegration(),
                     ],
        release=app.config['RELEASE'],
        environment=app.config['ENVIRONMENT'],
    )


db = MongoEngine(app)





from application.models.section import Section, current_section, is_portal_request
from application.views.section import SectionView

# Logo (GridFS) nicht bei jedem Request neu laden und kodieren, am
# Buchungsstichtag laufen hunderte Requests gleichzeitig. Ein neues Logo
# greift damit nach spaetestens LOGO_CACHE_SECONDS.
LOGO_CACHE_SECONDS = 60
_logo_cache = {}

def section_logo(section):
    """Logo der Sektion als data-URL, leer ohne Logo"""
    key = str(section.id or section.slug)
    now = time.monotonic()
    cached = _logo_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    try:
        logo = "data:image/png;base64," + base64.b64encode(section.logo_image.read()).decode('utf-8')
    except Exception:  # pylint: disable=broad-except
        logo = ''  # kein Logo hinterlegt
    _logo_cache[key] = (now + LOGO_CACHE_SECONDS, logo)
    return logo

def dark_background(color):
    """True, wenn auf der Farbe weisse Schrift besser lesbar ist als schwarze"""
    value = (color or '').lstrip('#')
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    try:
        channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return False
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    # Kontrast zu Weiss gegen Kontrast zu Schwarz
    return 1.05 / (luminance + 0.05) > (luminance + 0.05) / 0.05

@app.context_processor
def inject_section():
    """
    Design der Sektion fuer die Templates. Bewusst nicht in app.config: das
    teilen sich alle Threads, parallele Requests verschiedener Sektionen
    wuerden sich gegenseitig ueberschreiben.
    """
    from application.portal.terms import terms  # pylint: disable=import-outside-toplevel
    if is_portal_request():
        return {'site': {'name': 'Tourenanmeldung', 'nav_color': '#2f6b3a'}, 'portal': True, 't': terms()}
    try:
        section = current_section()
    except DoesNotExist:
        return {'site': {}, 't': terms()}
    return {'t': terms(), 'site': {
        'name': section.name,
        'nav_color': section.nav_background_color or 'grey',
        'nav_dark': dark_background(section.nav_background_color),
        'homepage_link': section.homepage_link,
        'logo': section_logo(section),
        'media_consent_text': section.media_consent_text,
    }}

# Was es auf dem Portal-Host gibt: das Portal selbst, Login und Konto
PORTAL_BLUEPRINTS = ('PORTAL', 'auth', 'USER', 'LEGAL', 'bootstrap')

@app.before_request
def require_section():
    """
    Unbekannte Adresse ohne Default-Sektion: 404 statt einer fremden Sektion.
    Auf dem Portal nur dessen Seiten. Eine Sektion ohne Testphase und ohne
    bezahltes Abo zeigt nur einen Hinweis, ihre Daten bleiben.
    """
    if request.endpoint == 'static':
        return None
    if is_portal_request():
        if request.path == '/':
            return redirect(url_for('PORTAL.index'))
        if request.blueprint not in PORTAL_BLUEPRINTS:
            abort(404)
        return None
    section = current_section()
    # Impressum und Datenschutz bleiben auch ohne Freischaltung erreichbar
    if not section.is_active() and request.blueprint != 'LEGAL':
        portal = app.config.get('PORTAL_HOSTNAME')
        return error_page(503, "Sektion nicht freigeschaltet",
                          "Die Testphase dieser Sektion ist abgelaufen. Admins der Sektion "
                          f"schließen das Abo unter https://{portal}/ ab." if portal else
                          "Diese Sektion ist gerade nicht freigeschaltet.")
    return None

# Nur Regeln, die nichts brechen: Inline-Skripte und das Einbetten in fremde
# Seiten bleiben erlaubt, bis geklaert ist, was davon gebraucht wird.
SECURITY_HEADERS = {
    'Content-Security-Policy': "object-src 'none'; base-uri 'self'",
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
}

@app.after_request
def set_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response

from application.models.log import LogEntry
from application.views.log import LogView


def LogFunction(message): #pylint: disable=invalid-name
    """
    Write entries do db
    """
    log_entry = LogEntry()
    log_entry.datetime = datetime.now()
    log_entry.user_id = str(message['user_id'])
    log_entry.message = message['message']
    log_entry.raw = ""
    if message['raw']:
        for raw_entry in message['raw']:
            log_entry.raw += raw_entry[0]+"\n"
            log_entry.raw += pformat(raw_entry[1])+"\n\n"
    log_entry.type = message['type']
    log_entry.url = message['url']
    log_entry.source = app.config['SITENAME']
    log_entry.traceback = message['traceback']
    log_entry.request_id = message.get('request_id')
    log_entry.save()

log = logging.Logging(log_func=LogFunction)

def client_key():
    """
    Schluessel fuer die IP-Limits. IPv6 zaehlt pro /64, weil ein Anschluss
    meist ein ganzes /64 bekommt und sonst jede Adresse ein eigenes Limit haette.
    """
    address = get_remote_address()
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return address

# swallow_errors: faellt Redis aus, soll das nicht auch noch den Login mitreissen
limiter = Limiter(client_key, app=app, storage_uri=app.config['REDIS_URL'],
                  swallow_errors=True)
limiter.request_filter(lambda: request.method.upper() == 'OPTIONS')

bootstrap = Bootstrap(app)

# Flask-Mail liest beim Bauen jeder Nachricht app.extensions['mail']. Die
# Zugangsdaten kommen pro Sektion, siehe modules/email.py
Mail(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = False


app.redis = Redis.from_url(app.config['REDIS_URL'])



from application.auth.views import AUTH
from application.events.views import EVENTS
from application.user.views import USER

from application.views.default import IndexView
from application.views.default import CustomModelView


from application.models.user import User
from application.views.user import UserView
from application.views.members import MemberView

from application.events.models import Event
from application.events.admin import EventView

from application.huts.models import Hut, HutBooking
from application.huts.admin import HutView, HutBookingView


admin = Admin(app, name="Admin", index_view=IndexView())


def translate_bool(what):
    if what:
        return "Ja"
    return "Nein"

def age(date):
    now = datetime.now().date()
    return relativedelta.relativedelta(now, date).years

    

app.jinja_env.globals.update(translate_bool=translate_bool)
app.jinja_env.globals.update(age=age)


def format_euro(value):
    """
    Geldbetrag deutsch mit genau zwei Nachkommastellen, z.B. 1234.5 -> "1.234,50 €".
    Preise sind Floats, Summen daraus haben sonst Rundungsreste (0.1 + 0.2).
    """
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return value
    text = f"{amount:,.2f}".replace(',', ' ').replace('.', ',').replace(' ', '.')
    return f"{text}\u00a0€"

app.jinja_env.filters['euro'] = format_euro
app.jinja_env.filters['sanitize_html'] = sanitize_html
app.jinja_env.filters['safe_url'] = safe_url


def error_page(code, title, message):
    """Fehlerseite im Layout der Seite"""
    return render_template('error.html', code=code, title=title, message=message), code

@app.errorhandler(400)
def bad_request(_error):
    return error_page(400, "Ungültige Anfrage",
                      "Bitte lade die Seite neu und versuche es noch einmal.")

@app.errorhandler(403)
def forbidden(_error):
    return error_page(403, "Keine Berechtigung", "Du darfst diese Seite nicht aufrufen.")

@app.errorhandler(404)
@app.errorhandler(DoesNotExist)
def not_found(_error):
    return error_page(404, "Nicht gefunden", "Diese Seite gibt es nicht (mehr).")

@app.errorhandler(ValidationError)
def invalid_data(error):
    """Kaputte id in der URL oder ungueltige Daten beim Speichern"""
    return not_found(error) if request.method == 'GET' else bad_request(error)

@app.errorhandler(429)
def too_many_requests(_error):
    return error_page(429, "Zu viele Versuche", "Bitte warte kurz und versuche es dann erneut.")

@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}

# Laufende Build-Nummer (Commit-Anzahl), beim Deploy gesetzt; zeigt im Footer,
# welcher Stand gerade laeuft.
APP_BUILD = os.environ.get('APP_BUILD', '')

@app.context_processor
def inject_build():
    return {'app_build': APP_BUILD}

@app.context_processor
def inject_hut_management():
    """Flag fuer die Nav: darf der User Huetten-Buchungen freigeben?"""
    try:
        from application.huts.views import can_manage_huts
        return {'manages_huts': can_manage_huts(current_user)}
    except Exception:  # pylint: disable=broad-except
        pass
    return {'manages_huts': False}

@app.context_processor
def inject_hut_nav():
    """Nav-Infos: Anzahl Huetten (0/1/mehr) und id der einzigen Huette."""
    try:
        ids = [str(h.id) for h in Hut.objects.only('id').limit(2)]
        return {
            'hut_count': len(ids),
            'single_hut_id': ids[0] if len(ids) == 1 else None,
        }
    except Exception:  # pylint: disable=broad-except
        return {'hut_count': 0, 'single_hut_id': None}

#System
admin.add_view(EventView(Event))
admin.add_view(HutView(Hut, name="Hütten"))
admin.add_view(HutBookingView(HutBooking, name="Hüttenbuchungen"))
admin.add_view(MemberView(User, name="Mitglieder", endpoint="Members"))
admin.add_view(UserView(User, category='System'))
admin.add_view(SectionView(Section, name='Sektionen', category='System'))
admin.add_view(LogView(LogEntry, name="Log", category="System"))

admin.add_link(MenuLink(name='Zum Frontend', category='', url="/"))
admin.add_link(MenuLink(name='Logout', category='', url="/logout"))

from application.huts.views import HUTS
from application.portal.views import PORTAL
from application.legal import LEGAL

app.register_blueprint(AUTH)
app.register_blueprint(USER)
app.register_blueprint(EVENTS)
app.register_blueprint(HUTS)
app.register_blueprint(PORTAL)
app.register_blueprint(LEGAL)
from application.jobs import *
