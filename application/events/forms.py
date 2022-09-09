from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, RadioField, SubmitField, BooleanField, \
                    TextAreaField, TimeField, DateField, SelectField, FieldList, FormField, \
                    IntegerField, FloatField
from wtforms.widgets import TextArea
from wtforms.validators import InputRequired, Email, EqualTo, ValidationError, Optional
from flask import current_app
from application.events.models import difficulties, categories, Ticket, CustomField


class EventSearchForm(FlaskForm):
    """
    Formular to filter on event list page
    """
    filter_name = StringField("Name", validators=[])
    filter_category = SelectField("Kategorie", choices=categories)
    filter_date = DateField("Datum", validators=[Optional()])
    filter_own = BooleanField("Meine")
    filter_future = BooleanField("Zukünftige", default="y")
    filter_send = SubmitField("Filter")


class EventCustomField(FlaskForm):
    """
    Custom Field
    """
    def __init__(self, csrf_enabled=False, *args, **kwargs):
        """
        Disable CSRF
        """
        super().__init__(csrf_enabled=False, *args, **kwargs)

    field_name = StringField("Extra Frage")

class TicketForm(FlaskForm):
    """
    Custom Field
    """
    def __init__(self, csrf_enabled=False, *args, **kwargs):
        """
        Disable CSRF
        """
        super().__init__(csrf_enabled=False, *args, **kwargs)

    name = StringField("Name", validators=[Optional()])
    description = StringField("Beschreibung", validators=[Optional()])
    price = FloatField("Preis", validators=[Optional()])
    maximum_tickets = IntegerField("Anzahl", validators=[Optional()])

class EventForm(FlaskForm):
    """
    Event Formular
    """
    event_name = StringField("Touren Name", validators=[InputRequired()])
    event_category = SelectField("Kategorie", choices=categories)
    event_description = TextAreaField("Beschreibung")
    places = StringField("Plätze")
    waitlist = BooleanField("Mit Warteliste")
    booking_from = DateField("Buchbar ab")
    booking_from_time = TimeField("Zeit")
    booking_until = DateField("Buchbar bis")
    booking_until_time = TimeField("Zeit")
    start_date = DateField("Datum")
    start_time = TimeField("Zeit am Treffpunkt")
    end_date = DateField("Ende Datum")
    end_time = TimeField("Endezeit der Tour")
    tour_link = StringField("Outdooractive Link")
    difficulty = RadioField("Schwierigkeit", choices=difficulties)
    length_h = StringField("Länge in Stunden")
    length_km= StringField("Strecke in km")
    altitude_difference= StringField("Höhenmeter")
    custom_fields = FieldList(FormField(EventCustomField), min_entries=1)
    tickets = FieldList(FormField(TicketForm), min_entries=1)

    submit = SubmitField("speichern")


class EventRegisterForm(FlaskForm):
    """
    Event Register Formular
    """
    comment = TextAreaField("Kommentar", validators=[InputRequired()])
