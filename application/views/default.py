"""
Models for flask_admin
"""
#pylint: disable=no-member
from flask_admin import AdminIndexView
from flask_admin.contrib.mongoengine import ModelView
from flask_admin.contrib.mongoengine.form import CustomModelConverter
from mongoengine import ReferenceField

from flask_login import current_user
from flask import redirect, url_for, flash


class IndexView(AdminIndexView):
    """
    Index View Overwrite for auth
    """
    def is_visible(self):
        return False

    def is_accessible(self):
        return current_user.is_authenticated \
                and not current_user.force_password_change

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for('auth.login', next='/admin'))

class OptionalListConverter(CustomModelConverter):
    """
    Flask-Admin baut fuer ListField(ReferenceField) ein Multi-Select ohne
    `allow_blank`. Dessen `pre_validate` lehnt eine leere Auswahl dann mit
    "Not a valid choice" ab -- ein optionales Feld verhaelt sich wie ein
    Pflichtfeld. Hier durchreichen, was das Model tatsaechlich verlangt.
    """

    def conv_List(self, model, field, kwargs):
        if isinstance(field.field, ReferenceField) and not field.required:
            kwargs.setdefault('allow_blank', True)
        return super().conv_List(model, field, kwargs)


class CustomModelView(ModelView):
    """ Custom Model View """

    model_form_converter = OptionalListConverter

    def scaffold_filters(self, name):
        """
        Flask-Admin gibt dem Filter das Feld-*Objekt* statt des Feldnamens mit,
        die MongoEngine-Filter bauen daraus per f-String die Query -> jeder
        angewendete Filter endet in einer InvalidQueryError. Namen nachreichen.
        """
        filters = super().scaffold_filters(name)
        for flt in filters or []:
            if not isinstance(flt.column, str):
                flt.column = flt.column.name
        return filters

    def is_accessible(self):
        return current_user.is_authenticated

    def inaccessible_callback(self, name, **kwargs):
        flash("You don't have the rights for the Module")
        return redirect('/admin')
