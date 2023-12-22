""" Main entry """
# pylint: disable=invalid-name
# pylint: disable=wrong-import-position
import os
from datetime import datetime
import base64
from pprint import pformat
from redis import Redis
from flask import Flask, session, request, send_from_directory
from flask_login import LoginManager
from flask_request_id_header.middleware import RequestID
from flask_admin import Admin
from flask_admin.menu import MenuLink
from flask_bootstrap import Bootstrap
from flask_mongoengine import MongoEngine
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import application.modules.log as logging


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

RequestID(app)

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


try:
    db = MongoEngine()
    from uwsgidecorators import postfork

    @postfork
    def setup_db():
        """db init in uwsgi"""
        db.init_app(app)
except ImportError:
    print("   \033[91mWARNING: STANDALONE MODE - NOT FOR PROD\033[0m")
    print(" * HINT: uwsgi modul not loaded")
    db = MongoEngine(app)





from application.models.config import Config
from application.views.config import ConfigModelView

@app.before_request
def prepare_config():
    try:
        user_config = Config.objects(enabled=True)[0]
        app.config['style_nav_background_color'] = user_config.nav_background_color
        app.config['style_brand_logo'] = "data:image/png;base64,"+base64.b64encode(user_config.logo_image.read()).decode('utf-8')
        app.config['MAIL_SENDER'] = user_config.mail_sender
        app.config['MAIL_SERVER'] = user_config.mail_server
        app.config['MAIL_USE_TLS'] = False
        app.config['MAIL_USERNAME'] = user_config.mail_username
        app.config['MAIL_PORT'] = 465
        app.config['MAIL_USE_SSL'] = True
        app.config['MAIL_SUBJECT_PREFIX'] = user_config.mail_subject_prefix
        app.config['MAIL_PASSWORD'] = user_config.mail_password
    except:
        app.config['style_nav_background_color'] = 'black'

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

limiter = Limiter(app, storage_uri=app.config['REDIS_URL'])
limiter.request_filter(lambda: request.method.upper() == 'OPTIONS')

bootstrap = Bootstrap(app)

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


admin = Admin(app, name="Admin", template_mode='bootstrap4',
              index_view=IndexView())


def translate_bool(what):
    if what:
        return "Ja"
    return "Nein"

app.jinja_env.globals.update(translate_bool=translate_bool)

@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}

#System
admin.add_view(EventView(Event))
admin.add_view(MemberView(User, name="Mitglieder", endpoint="Members"))
admin.add_view(UserView(User, category='System'))
admin.add_view(ConfigModelView(Config, category='System'))
admin.add_view(LogView(LogEntry, name="Log", category="System"))

admin.add_link(MenuLink(name='Zum Frontend', category='', url="/"))
admin.add_link(MenuLink(name='Logout', category='', url="/logout"))

app.register_blueprint(AUTH)
app.register_blueprint(USER)
app.register_blueprint(EVENTS)
from application.jobs import *
