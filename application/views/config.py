"""
Config Model View
"""
from wtforms import TextAreaField
from application.views.default import CustomModelView
from flask_login import current_user


class ConfigModelView(CustomModelView): #pylint: disable=too-few-public-methods
    """
    Style Model
    """

    can_edit = True
    can_delete = True
    can_create = True

    form_overrides = {'media_consent_text': TextAreaField}
    form_widget_args = {'media_consent_text': {'rows': 15}}
    column_labels = {'media_consent_text': 'Einwilligungstext Foto/Video (HTML)'}
    column_descriptions = {
        'media_consent_text': 'Wird bei Registrierung, Profil und Buchung angezeigt. '
                              'Leer lassen für den Standardtext (DAV Sektion Kampenwand).',
    }
    column_exclude_list = ['media_consent_text']


    def is_accessible(self): #pylint: disable=no-self-use
        """ Overwrite """
        return current_user.is_authenticated and current_user.is_global_admin()
