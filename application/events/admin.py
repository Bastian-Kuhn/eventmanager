"""
Models for flask_admin
"""
#pylint: disable=no-member
#pylint: disable=missing-function-docstring
#pylint: disable=too-few-public-methods
from flask import url_for
from flask_login import current_user
from markupsafe import Markup
from application.views.default import CustomModelView

def _event_links(_view, _context, model, _name):
    """Bearbeiten, Teilnehmer und Abrechnung laufen im Frontend, das die Daten konsistent haelt"""
    links = [('Ansehen', url_for('EVENTS.page_details', event_id=model.id))]
    if current_user.may_manage_event(model):
        links += [('Bearbeiten', url_for('EVENTS.page_admin', event_id=model.id)),
                  ('Teilnehmer', url_for('EVENTS.page_participants', event_id=model.id)),
                  ('Abrechnung', url_for('EVENTS.page_billing', event_id=model.id))]
    return Markup(' · ').join(
        Markup('<a href="{}" aria-label="{} {}">{}</a>').format(url, text, model.event_name, text)
        for text, url in links)


class EventView(CustomModelView):
    """
    Touren im Admin-Panel nur als Uebersicht. Anlegen, Bearbeiten und Loeschen
    gehen ueber das Frontend: das Formular hier zeigte Teilnahmen, Tickets und
    Kosten roh und liess die Rueckverweise bei Konten und Huetten kaputt machen.
    """
    can_create = False
    can_edit = False
    can_delete = False
    can_view_details = False
    column_list = ('event_name', 'start_date', 'end_date', 'event_category', 'places',
                   'event_owners', 'members_only', 'links')
    column_sortable_list = ("start_date", "event_name")
    column_default_sort = ('start_date', True)
    column_labels = {
        'event_name': 'Tour', 'start_date': 'Beginn', 'end_date': 'Ende',
        'event_category': 'Kategorie', 'places': 'Plätze', 'event_owners': 'Verantwortlich',
        'members_only': 'Nur Mitglieder', 'links': '',
    }
    column_formatters = {'links': _event_links}
    page_size = 100
    can_set_page_size = True
    column_filters = (
        'event_name',
        'event_description',
        'start_date',
    )

    def is_accessible(self):
        return current_user.is_authenticated and current_user.has_right('guide')

    def get_query(self):
        """
        Tourenbegleiter sehen hier nur ihre eigenen Touren (Liste und Export)
        """
        query = super().get_query()
        if current_user.manages_all_events():
            return query
        return query.filter(event_owners=current_user.id)
