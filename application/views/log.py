"""
Log Model View
"""
from html import escape
from markupsafe import Markup
from application.views.default import CustomModelView
from flask_login import current_user

def _field_escape(_view, _context, model, name):
    """
    Show debug stuff
    """
    return Markup(escape(model[name]))

def _render_request_link(_view, _context, model, name):
    return Markup("<a href='/admin/logentry/?flt1_35={req}'>{req}</a>".format(req=model.request_id))

class LogView(CustomModelView): #pylint: disable=too-few-public-methods
    """
    Log Model
    """

    can_edit = True
    can_delete = False
    can_create = False
    can_export = True

    export_types = ['xlsx']

    column_filters = (
        'user_id', 'type', 'message', 'raw', 'url',
        'request_id',
    )
    column_formatters = {
        'raw': _field_escape,
        'request_id': _render_request_link,
    }
    column_default_sort = ('id', True)
    page_size = 100

    def is_accessible(self): #pylint: disable=no-self-use
        """ Overwrite """
        return current_user.is_authenticated and current_user.has_right('log')
