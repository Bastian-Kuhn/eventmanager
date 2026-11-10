"""
Models for flask_admin
"""
#pylint: disable=no-member
from flask_admin import AdminIndexView
from flask_admin.contrib.mongoengine import ModelView
from flask_admin.contrib.mongoengine.form import CustomModelConverter, ModelSelectField, \
    ModelSelectMultipleField
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

def _is_user(document_type):
    return getattr(document_type, '__name__', document_type) == 'User'


def _section_members():
    from application.models.user import section_members  # pylint: disable=import-outside-toplevel
    return section_members()


class MemberSelectField(ModelSelectField):
    """
    Auswahl eines Kontos nur aus den Mitgliedern der Sektion. Das Feld
    entsteht pro Formular, also im Request, auch in eingebetteten Formularen.
    """

    def __init__(self, label="", validators=None, model=None, **kwargs):
        kwargs['queryset'] = _section_members()
        super().__init__(label, validators, model=model, **kwargs)


class MemberSelectMultipleField(ModelSelectMultipleField):
    """Mehrfachauswahl wie MemberSelectField"""

    def __init__(self, label="", validators=None, model=None, **kwargs):
        kwargs['queryset'] = _section_members()
        super().__init__(label, validators, model=model, **kwargs)


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
        if isinstance(field.field, ReferenceField) and _is_user(field.field.document_type) \
                and field.name not in getattr(self.view, '_form_ajax_refs', {}):
            return MemberSelectMultipleField(model=field.field.document_type, **kwargs)
        return super().conv_List(model, field, kwargs)

    def conv_Reference(self, model, field, kwargs):
        if _is_user(field.document_type) and field.name not in getattr(self.view, '_form_ajax_refs', {}):
            kwargs["allow_blank"] = not field.required
            return MemberSelectField(model=field.document_type, **kwargs)
        return super().conv_Reference(model, field, kwargs)


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
