"""
Models for flask_admin
"""
#pylint: disable=no-member
#pylint: disable=missing-function-docstring
#pylint: disable=no-self-use
from datetime import datetime
from flask import flash
from flask_admin.actions import action
from wtforms import PasswordField
from flask_login import current_user
from application.models.user import User, remove_user_references, TFA_FIELDS
from application.views.default import CustomModelView


class UserView(CustomModelView):
    """
    Extended Admin View for Users
    """
    column_sortable_list = ("email", "global_admin")
    # tfa_secret bleibt in der Liste, der Formatter zeigt nur Ja/Nein
    column_exclude_list = ("pwdhash", 'force_password_change', 'date_changed', 'date_password',
                           'tfa_pending_secret', 'tfa_recovery_codes', 'tfa_last_step')
    form_excluded_columns = ("pwdhash", "legacy_role", "legacy_admin", "legacy_club_id", *TFA_FIELDS)
    column_labels = {'tfa_secret': '2FA'}
    column_formatters = {
        'tfa_secret': lambda _v, _c, model, _n: "Ja" if model.tfa_enabled else "Nein",
    }
    page_size = 100
    can_set_page_size = True
    column_filters = (
        'email',
        'first_name',
        'last_name',
        'global_admin',
    )

    column_editable_list = (
        'disabled',
    )

    form_widget_args = {
        'date_added': {'disabled': True},
        'date_changed': {'disabled': True},
        'date_password': {'disabled': True},
        'last_login': {'disabled': True},
    }

    def scaffold_form(self):
        form_class = super().scaffold_form()
        form_class.password = PasswordField("Password")
        return form_class

    def on_model_change(self, form, model, is_created):
        # Das Inline-Bearbeiten in der Liste schickt nur das geaenderte Feld
        if 'email' in form and form.email.data:
            model.email = form.email.data.lower()
        if 'password' in form and form.password.data:
            # Time of Password Change will stored by set_password
            model.set_password(form.password.data)
        if is_created:
            model.date_added = datetime.now()
        else:
            model.date_changed = datetime.now()
        return super().on_model_change(form, model, is_created)

    def on_model_delete(self, model):
        remove_user_references(model)

    @action('reset_tfa', '2FA zurücksetzen',
            'Zwei-Faktor-Anmeldung für die gewählten Konten ausschalten? '
            'Die Personen melden sich danach nur mit dem Passwort an.')
    def action_reset_tfa(self, ids):
        """Fuer Konten, deren Handy und Wiederherstellungscodes weg sind"""
        count = 0
        for user in User.objects(id__in=ids):
            if user.tfa_enabled or user.tfa_pending_secret:
                user.disable_tfa()
                count += 1
        flash(f"Zwei-Faktor-Anmeldung bei {count} Konten zurückgesetzt.", 'success')

    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_global_admin()
