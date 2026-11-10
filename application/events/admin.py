"""
Models for flask_admin
"""
#pylint: disable=no-member
#pylint: disable=missing-function-docstring
#pylint: disable=too-few-public-methods
from flask_login import current_user
from application.views.default import CustomModelView
from application.huts.models import remove_event_booking
from application.models.user import remove_event_references

class EventView(CustomModelView):
    """
    Extended Admin View for Users
    """
    column_sortable_list = ("start_date", "event_name")
    column_exclude_list = ('event_description', 'difficulty', 'length_h', 'length_km', 'tour_link', 'altitude_difference')
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
        Trainer und Tourenbegleiter sehen und bearbeiten hier nur ihre eigenen
        Touren. Liste, Bearbeiten, Loeschen und Export laufen alle hierueber.
        """
        query = super().get_query()
        if current_user.is_global_admin():
            return query
        return query.filter(event_owners=current_user.id)

    def on_model_change(self, form, model, is_created):
        """Wer hier eine Tour anlegt, wird ihr Verantwortlicher"""
        if is_created and not current_user.is_global_admin() \
                and current_user.id not in [x.id for x in model.event_owners]:
            model.event_owners.append(current_user._get_current_object())  # pylint: disable=protected-access

    def on_model_delete(self, model):
        """Verknüpfte Hüttenbuchung und Rückreferenzen mit dem Event mitentfernen."""
        remove_event_references(model)
        remove_event_booking(model)
