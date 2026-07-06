"""
Flask-Admin View für die Hütten-Verwaltung.
"""
#pylint: disable=too-few-public-methods, no-member
from flask_login import current_user
from application.views.default import CustomModelView


class HutView(CustomModelView):
    """
    Verwaltung der Hütten inkl. Zimmer und Belegungen (inline).
    """
    can_create = True
    can_edit = True
    can_delete = True
    page_size = 100
    can_set_page_size = True

    column_list = ('name', 'region', 'managed', 'total_places', 'requires_approval',
                   'contact', 'phone')
    column_sortable_list = ('name', 'region')
    column_filters = ('name', 'region', 'managed', 'requires_approval')
    column_labels = {
        'name': 'Name',
        'region': 'Region/Ort',
        'managed': 'Bewirtschaftet',
        'total_places': 'Schlafplätze',
        'requires_approval': 'Freigabe nötig',
        'admins': 'Hütten-Admins',
        'contact': 'Kontakt',
        'phone': 'Telefon',
        'link': 'Link',
        'note': 'Notiz',
        'rooms': 'Zimmer',
        'bookings': 'Belegungen',
    }
    # total_places ist eine Methode, kein Feld -> als berechnete Spalte anzeigen
    column_formatters = {
        'total_places': lambda view, context, model, name: model.total_places(),
    }
    form_columns = ('name', 'region', 'managed', 'requires_approval', 'admins',
                    'contact', 'phone', 'link', 'note', 'rooms', 'bookings')

    def is_accessible(self):
        return current_user.is_authenticated and current_user.has_right('guide')
