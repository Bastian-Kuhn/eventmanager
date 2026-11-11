"""
Models for flask_admin
"""
#pylint: disable=no-member
#pylint: disable=missing-function-docstring
#pylint: disable=no-self-use
from flask_admin.contrib.mongoengine.form import get_form
from flask_admin.model.form import create_editable_list_form
from wtforms import BooleanField, SelectField, StringField
from flask_login import current_user
from application.views.default import CustomModelView
from wtforms.validators import ValidationError
from application.models.section import ref_id, section_or_none
from application.models.user import roles, remove_user_references, TFA_FIELDS, \
    SECTION_FIELDS, section_members
from markupsafe import Markup



roles_dict = dict(roles)

# Gehoeren dem Konto, nicht der Sektion: bei Konten, die auch in anderen
# Sektionen sind, darf sie nur ein globaler Admin aendern. Sonst koennte ein
# Vereinsadmin z.B. die Mailadresse tauschen und per Passwort-Reset das Konto
# eines Admins einer anderen Sektion uebernehmen.
ACCOUNT_FIELDS = ('email', 'first_name', 'last_name', 'birthdate', 'phone',
                  'disabled', 'force_password_change', 'email_unconfirmed', 'profile_img')


def section_fields():
    """Formularfelder fuer die Mitgliedschaft in der Sektion des Requests"""
    return {
        'club_id': StringField("Mitgliedernummer"),
        'role': SelectField("Rolle im Verein", choices=[('', 'Keine')] + roles,
                            coerce=lambda value: value or None),
        'admin': BooleanField("Vereinsadmin"),
    }

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

    column_sortable_list = ("email", "first_name", "last_name")
    column_list = ('email', 'first_name', 'last_name', 'guest_from', 'club_id', 'role', 'admin',
                   'disabled', 'media_optin', 'data_optin', 'email_unconfirmed')

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
        'memberships',
        'legacy_role', 'legacy_admin', 'legacy_club_id',
        *TFA_FIELDS,
    ]

    column_formatters = {
       'role': _render_role,
       'admin': lambda _v, _c, model, _n: "Ja" if model.admin else "Nein",
       'guest_from': lambda _v, _c, model, _n: ', '.join(model.guest_from()),
    }


    column_labels = {
        "first_name": "Vorname",
        "last_name": "Nachname",
        "club_id": "Mitgliedernummer",
        "guest_from": "Gast aus",
        "role": "Rolle im Verein",
        "admin": "Vereinsadmin",
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

    def get_query(self):
        return section_members()

    def scaffold_form(self):
        form_class = super().scaffold_form()
        for name, field in section_fields().items():
            setattr(form_class, name, field)
        return form_class

    def scaffold_list_form(self, widget=None, validators=None):
        """Wie Flask-Admin, plus Rolle und Vereinsadmin der Sektion"""
        form_class = get_form(
            self.model,
            self.model_form_converter(self),
            base_class=self.form_base_class,
            only=[name for name in self.column_editable_list if name not in SECTION_FIELDS],
            field_args=validators,
        )
        extra = section_fields()
        for name in self.column_editable_list:
            if name in extra:
                setattr(form_class, name, extra[name])
        return create_editable_list_form(self.form_base_class, form_class, widget)

    @staticmethod
    def _in_other_sections(model):
        own = ref_id(section_or_none() and section_or_none().pk)
        return any(ref_id(entry._data.get('section')) != own  # pylint: disable=protected-access
                   for entry in model.memberships)

    def _protect_account(self, form, model):
        if current_user.is_global_admin() or not self._in_other_sections(model):
            return
        for name in ACCOUNT_FIELDS:
            if name in form and form[name].data != form[name].object_data:
                raise ValidationError("Das Konto gehört auch zu einer anderen Sektion. "
                                      "Hier lassen sich nur Rolle, Vereinsadmin und "
                                      "Mitgliedernummer ändern.")

    def _protect_global_admin(self, model):
        """
        Nur Global Admins aendern Global Admins. Sonst liesse sich die Mailadresse
        tauschen und das Konto per Passwort-Reset uebernehmen.
        """
        if model.global_admin and not current_user.is_global_admin():
            raise ValidationError("Global Admins können nur von Global Admins geändert werden")

    def on_model_change(self, form, model, is_created):
        self._protect_global_admin(model)
        self._protect_account(form, model)
        return super().on_model_change(form, model, is_created)

    def delete_model(self, model):
        """
        Ist das Konto auch in anderen Sektionen, verlaesst es nur diese
        Sektion. Sonst wird es wie bisher geloescht.
        """
        if not self._in_other_sections(model):
            return super().delete_model(model)
        self._protect_global_admin(model)
        model.leave_section()
        return True

    def on_model_delete(self, model):
        self._protect_global_admin(model)
        remove_user_references(model)
