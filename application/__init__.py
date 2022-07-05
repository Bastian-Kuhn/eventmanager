""" Main entry """
# pylint: disable=invalid-name
# pylint: disable=wrong-import-position
import os
from datetime import datetime
from pprint import pformat
from redis import Redis
from flask import Flask, session, request, send_from_directory
from flask_login import LoginManager
from flask_request_id_header.middleware import RequestID
from flask_admin import Admin
from flask_admin.menu import MenuLink
from flask_mail import Mail
from flask_bootstrap import Bootstrap
from flask_mongoengine import MongoEngine
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import application.modules.log as logging


app = Flask(__name__)
if os.environ.get('config') == "prod":
    print(" * Loading PROD Config")
    app.config.from_object('application.config.ProductionConfig')
elif os.environ.get('config') == "prod_compose":
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
        db.init_app(app, app.config)
except ImportError:
    print("   \033[91mWARNING: STANDALONE MODE - NOT FOR PROD\033[0m")
    print(" * HINT: uwsgi modul not loaded")
    db = MongoEngine(app)



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

limiter = Limiter(app, key_func=get_remote_address, storage_uri=app.config['REDIS_URL'])
limiter.request_filter(lambda: request.method.upper() == 'OPTIONS')

bootstrap = Bootstrap(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = False


app.redis = Redis.from_url(app.config['REDIS_URL'])


mail = Mail(app)

from application.auth.views import AUTH
from application.events.views import EVENTS
from application.user.views import USER

from application.views.default import IndexView
from application.views.default import CustomModelView


from application.models.user import User
from application.views.user import UserView

from application.models.event import Event
from application.views.event import EventView


admin = Admin(app, name="Admin", template_mode='bootstrap4',
              index_view=IndexView())

#System
admin.add_view(EventView(Event))
admin.add_view(UserView(User, category='System'))
admin.add_view(LogView(LogEntry, name="Log", category="System"))

admin.add_link(MenuLink(name='Logout', category='', url="/logout"))

app.register_blueprint(AUTH)
app.register_blueprint(USER)
app.register_blueprint(EVENTS)
from application.jobs import *
