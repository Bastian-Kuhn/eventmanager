"""
Models for flask_admin
"""
#pylint: disable=no-member
#pylint: disable=missing-function-docstring
#pylint: disable=too-few-public-methods
from flask_login import current_user
from application.views.default import CustomModelView

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
