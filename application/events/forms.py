from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, RadioField, SubmitField, BooleanField, \
                    TextAreaField, TimeField, DateField, SelectField, FieldList, FormField, \
                    IntegerField, FloatField
from wtforms.widgets import TextArea
from wtforms.validators import InputRequired, Email, EqualTo, ValidationError, Optional
from flask import current_app
from application.events.models import difficulties, Ticket, CustomField, event_types
from application import app
from application.models.config import Config




class EventSearchForm(FlaskForm):
    """
    Formular to filter on event list page
    """
    filter_name = StringField("Name", validators=[])
    filter_category = SelectField("Kategorie")
    filter_date = DateField("Datum", validators=[Optional()])
    filter_own = BooleanField("Ich bin angemeldet")
    filter_future = BooleanField("Zukünftige", default="y")
    export = BooleanField("Export", default=False)
    filter_send = SubmitField("Filter")


class EventCustomField(FlaskForm):
    """
    Simple extra question field
    """
    class Meta:
        csrf = False

    field_name = StringField("Extra Frage", validators=[Optional()])

class TicketForm(FlaskForm):
    """
    Ticket Formular Field
    """
    class Meta:
        csrf = False

    name = StringField("Name", default="Teilnahme", validators=[Optional()])
    description = StringField("Beschreibung", validators=[Optional()])
    price = FloatField("Preis", validators=[Optional()])
    maximum_tickets = IntegerField("Anzahl", validators=[Optional()])


class EventForm(FlaskForm):
    """
    Event Formular
    """
    event_name = StringField("Touren Name", validators=[InputRequired()])
    event_teaser = StringField("Event Teaser", validators=[InputRequired()])
    event_category = SelectField("Kategorie", validators=[InputRequired()])
    event_type = SelectField("Touren Typ", choices=event_types, validators=[InputRequired()])
    event_description = TextAreaField("Beschreibung")
    places = IntegerField("Plätze auf Tour", validators=[InputRequired()])
    min_places = IntegerField("Mindestteilnehmerzahl", validators=[])
    booking_from = DateField("Buchbar ab", validators=[Optional()])
    booking_from_time = TimeField("Zeit", validators=[Optional()])
    booking_until = DateField("Buchbar bis", validators=[Optional()])
    booking_until_time = TimeField("Zeit", validators=[Optional()])
    start_date = DateField("Datum")
    start_time = TimeField("Zeit am Treffpunkt", validators=[Optional()])
    end_date = DateField("Ende Datum")
    end_time = TimeField("Endezeit der Tour", validators=[Optional()])
    difficulty = RadioField("Schwierigkeit", choices=difficulties, validators=[InputRequired()])
    shape = RadioField("Kondition", choices=difficulties, validators=[InputRequired()])
    length_h = IntegerField("Länge in Stunden")
    length_km= IntegerField("Strecke in km")
    altitude_difference= IntegerField("Höhenmeter")
    custom_fields = FieldList(FormField(EventCustomField), min_entries=1)
    tickets = FieldList(FormField(TicketForm), min_entries=2)

    submit = SubmitField("speichern")


class EventRegisterForm(FlaskForm):
    """
    Event Register Formular
    """
    class Meta:
        csrf = False
    comment = TextAreaField("Kommentar", validators=[Optional()])
    #custom_fields = FieldList(FormField(EventCustomField), min_entries=1)
    #tickets = FieldList(FormField(TicketForm), min_entries=1)
    submit = SubmitField("Anmelden")
