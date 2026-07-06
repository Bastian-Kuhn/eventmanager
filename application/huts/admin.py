"""
Flask-Admin Views für die Hütten-Verwaltung.
"""
#pylint: disable=too-few-public-methods, no-member
from flask_login import current_user
from application.views.default import CustomModelView
from application.huts.models import HutBooking


class HutView(CustomModelView):
    """
    Verwaltung der Hütten inkl. Zimmer (inline). Buchungen liegen in einer eigenen
    Collection und werden in der HutBookingView verwaltet.
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
    }
    # total_places ist eine Methode, kein Feld -> als berechnete Spalte anzeigen
    column_formatters = {
        'total_places': lambda view, context, model, name: model.total_places(),
    }
    form_columns = ('name', 'region', 'managed', 'requires_approval', 'admins',
                    'contact', 'phone', 'link', 'note', 'rooms')

    def is_accessible(self):
        return current_user.is_authenticated and current_user.has_right('guide')

    def on_model_delete(self, model):
        """Buchungen der Hütte mitentfernen (eigene Collection, kein Cascade auf DB-Ebene)."""
        HutBooking.objects(hut=model).delete()


class HutBookingView(CustomModelView):
    """
    Verwaltung der Hüttenbuchungen (eigene Collection).
    """
    can_create = True
    can_edit = True
    can_delete = True
    page_size = 100
    can_set_page_size = True

    column_list = ('hut', 'from_date', 'to_date', 'places', 'room', 'name',
                   'confirmed', 'event')
    column_sortable_list = ('from_date', 'to_date', 'confirmed')
    column_filters = ('confirmed',)
    column_labels = {
        'hut': 'Hütte',
        'from_date': 'Von',
        'to_date': 'Bis',
        'places': 'Plätze',
        'room': 'Zimmer',
        'name': 'Bucher',
        'confirmed': 'Bestätigt',
        'event': 'Tour',
        'user': 'Mitglied',
        'comment': 'Kommentar',
    }
    form_columns = ('hut', 'from_date', 'to_date', 'places', 'room', 'name',
                    'user', 'event', 'comment', 'confirmed')

    def is_accessible(self):
        return current_user.is_authenticated and current_user.has_right('guide')
