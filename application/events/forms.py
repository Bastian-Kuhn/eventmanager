from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, RadioField, SubmitField, BooleanField, \
                    TextAreaField, DateField, DateTimeLocalField, SelectField, FieldList, FormField, \
                    IntegerField, FloatField
from wtforms.widgets import TextArea
from wtforms.validators import InputRequired, Email, EqualTo, ValidationError, Optional, Length
from flask import current_app
from application.events.models import difficulties, Ticket, CustomField, event_types
from application import app
from application.models.config import Config




# Format von <input type="datetime-local">
DATETIME_LOCAL = '%Y-%m-%dT%H:%M'


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
    maximum_tickets = IntegerField("Max. pro Konto", validators=[Optional()])
    is_extra_ticket = BooleanField("Zusatzticket (belegt keinen Platz)", validators=[Optional()])


class EventForm(FlaskForm):
    """
    Event Formular
    """
    event_name = StringField("Touren Name", validators=[InputRequired()])
    event_teaser = StringField("Event Teaser", validators=[InputRequired()])
    event_category = SelectField("Kategorie", validators=[InputRequired()])
    hut = SelectField("Hütte (Übernachtung)", validators=[Optional()])
    event_type = SelectField("Touren Typ", choices=event_types, validators=[InputRequired()])
    event_description = TextAreaField("Beschreibung")
    places = IntegerField("Plätze auf Tour", default=7, validators=[Optional()])
    min_places = IntegerField("Mindestteilnehmerzahl", default=0, validators=[])
    start_date = DateTimeLocalField("Beginn", format=DATETIME_LOCAL, validators=[InputRequired()])
    end_date = DateTimeLocalField("Ende (leer = wie Beginn)", format=DATETIME_LOCAL, validators=[Optional()])
    booking_from = DateTimeLocalField("Buchbar ab (leer = sofort)", format=DATETIME_LOCAL, validators=[Optional()])
    booking_until = DateTimeLocalField("Buchbar bis (leer = bis Beginn)", format=DATETIME_LOCAL,
                                       validators=[Optional()])
    difficulty = RadioField("Schwierigkeit", choices=difficulties, default='keine',
                            validators=[InputRequired()])
    shape = RadioField("Kondition", choices=difficulties, default='keine', validators=[InputRequired()])
    length_h = IntegerField("Länge in Stunden", default=0)
    length_km= IntegerField("Strecke in km", default=0)
    altitude_difference= IntegerField("Höhenmeter", default=0)
    custom_fields = FieldList(FormField(EventCustomField), min_entries=1)
    tickets = FieldList(FormField(TicketForm), min_entries=2)
    add_guide = BooleanField("Mich beim speichern als verantwortlichen eintragen")

    submit = SubmitField("speichern")

    def validate_tickets(self, field):
        """
        Nur optionale Tickets gehen nicht: sie belegen keine Plaetze, die
        Anmeldungen fehlen dann in allen Zahlen der Tour.
        """
        # tickets-0 ist die versteckte Vorlage
        tickets = [x.form for x in field.entries if x.name != 'tickets-0' and x.form.name.data]
        if tickets and all(x.is_extra_ticket.data for x in tickets):
            raise ValidationError("Mindestens ein Ticket darf kein Zusatzticket sein.")


# Kommentare liegen in jedem gebuchten Ticket im Event-Dokument, das
# MongoDB auf 16 MB begrenzt
COMMENT_MAX = 1000


class EventRegisterForm(FlaskForm):
    """
    Event Register Formular
    """
    class Meta:
        csrf = False
    comment = TextAreaField("Kommentar", validators=[Optional(), Length(max=COMMENT_MAX)],
                            render_kw={'maxlength': COMMENT_MAX,
                                       'aria-describedby': 'comment_hint'})
    #custom_fields = FieldList(FormField(EventCustomField), min_entries=1)
    #tickets = FieldList(FormField(TicketForm), min_entries=1)
    submit = SubmitField("Anmelden")
