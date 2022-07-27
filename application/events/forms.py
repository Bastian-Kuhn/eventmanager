from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, RadioField, SubmitField, BooleanField, \
                    TextAreaField, TimeField, DateField, SelectField
from wtforms.widgets import TextArea
from wtforms.validators import InputRequired, Email, EqualTo, ValidationError
from flask import current_app
from application.models.event import difficulties, categories


class EventForm(FlaskForm):
    """
    Event Formular
    """
    event_name = StringField("Touren Name", validators=[InputRequired()])
    event_category = SelectField("Kategorie", choices=categories)
    event_description = TextAreaField("Beschreibung")
    places = StringField("Plätze")
    waitlist = BooleanField("Mit Warteliste")
    start_date = DateField("Datum")
    start_time = TimeField("Zeit am Treffpunkt")
    end_date = DateField("Ende Datum")
    end_time = TimeField("Endezeit der Tour")
    tour_link = StringField("Outdooractive Link")
    difficulty = RadioField("Schwierigkeit", choices=difficulties)
    length_h = StringField("Länge in Stunden")
    length_km= StringField("Strecke in km")
    altitude_difference= StringField("Höhenmeter")

    submit = SubmitField("speichern")

class EventRegisterForm(FlaskForm):
    """
    Event Register Formular
    """
    comment = TextAreaField("Kommentar", validators=[InputRequired()])
    submit = SubmitField("Anmelden")
