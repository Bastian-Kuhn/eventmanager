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
    is_extra_ticket = BooleanField("Opt. Ticket", validators=[Optional()])


class EventForm(FlaskForm):
    """
    Event Formular
    """
    event_name = StringField("Touren Name", validators=[InputRequired()])
    event_teaser = StringField("Event Teaser", validators=[InputRequired()])
    event_category = SelectField("Kategorie", validators=[InputRequired()])
    event_type = SelectField("Touren Typ", choices=event_types, validators=[InputRequired()])
    event_description = TextAreaField("Beschreibung")
    places = IntegerField("Plätze auf Tour", default=7, validators=[Optional()])
    min_places = IntegerField("Mindestteilnehmerzahl", default=0, validators=[])
    booking_from = DateField("Buchbar ab", validators=[InputRequired()])
    booking_from_time = TimeField("Zeit", validators=[InputRequired()])
    booking_until = DateField("Buchbar bis", validators=[InputRequired()])
    booking_until_time = TimeField("Zeit", validators=[InputRequired()])
    start_date = DateField("Datum")
    start_time = TimeField("Beginn des Events", validators=[InputRequired()])
    end_date = DateField("Ende Datum", validators=[InputRequired()])
    end_time = TimeField("Endezeit des Events", validators=[InputRequired()])
    difficulty = RadioField("Schwierigkeit", choices=difficulties, validators=[InputRequired()])
    shape = RadioField("Kondition", choices=difficulties, validators=[InputRequired()])
    length_h = IntegerField("Länge in Stunden", default=0)
    length_km= IntegerField("Strecke in km", default=0)
    altitude_difference= IntegerField("Höhenmeter", default=0)
    custom_fields = FieldList(FormField(EventCustomField), min_entries=1)
    tickets = FieldList(FormField(TicketForm), min_entries=2)
    add_guide = BooleanField("Mich beim speichern als verantwortlichen eintragen")

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
