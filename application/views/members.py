"""
Models for flask_admin
"""
#pylint: disable=no-member
#pylint: disable=missing-function-docstring
#pylint: disable=no-self-use
from datetime import datetime
from wtforms import PasswordField
from flask_login import current_user
from application.views.default import CustomModelView
from wtforms.validators import ValidationError
from application.models.user import roles, remove_user_references
from markupsafe import Markup



roles_dict = dict(roles)

def _render_role(_view, _context, model, _name):
    """
    Render Rolle
    """
    if model.role:
        return Markup(roles_dict[model.role])


class MemberView(CustomModelView):
    """
    View for Member Management
    """
    can_create = False

    column_sortable_list = ("email", "global_admin")
    column_exclude_list =["pwdhash", 'force_password_change',
                          'date_changed', 'date_password',
                          'birthdate', 'phone', 'date_added',
                          'last_login', 'global_admin',
                          'event_registrations', 'profile_img',
                         ]

    page_size = 100
    can_set_page_size = True
    column_filters = (
        'email',
        'first_name',
        'last_name',
        'global_admin',
    )

    form_excluded_columns = [
        'pwdhash',
        'global_admin',
    ]

    column_formatters = {
       'role': _render_role,
    }


    column_labels = {
        "first_name": "Vorname",
        "last_name": "Nachname",
        "club_id": "Mitgliedernummer",
        "role": "Rolle im Verein",
        "media_optin": "Bildfreigabe",
        "data_optin": "Datenfreigabe",
    }

    column_editable_list = (
        'disabled',
        'media_optin', 'data_optin',
        'role', 'admin'
    )

    form_widget_args = {
        'date_added': {'disabled': True},
        'date_changed': {'disabled': True},
        'date_password': {'disabled': True},
        'last_login': {'disabled': True},
    }

    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_admin()

    def _protect_global_admin(self, model):
        """
        Nur Global Admins aendern Global Admins. Sonst liesse sich die Mailadresse
        tauschen und das Konto per Passwort-Reset uebernehmen.
        """
        if model.global_admin and not current_user.is_global_admin():
            raise ValidationError("Global Admins können nur von Global Admins geändert werden")

    def on_model_change(self, form, model, is_created):
        self._protect_global_admin(model)
        return super().on_model_change(form, model, is_created)

    def on_model_delete(self, model):
        self._protect_global_admin(model)
        remove_user_references(model)
