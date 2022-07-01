from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, RadioField, SubmitField, BooleanField, \
                    TextAreaField, TimeField, DateField
from wtforms.validators import InputRequired, Email, EqualTo, ValidationError
from wtforms.widgets import TextArea
from flask import current_app


difficulties = [
  ("sehr leicht", "Sehr Leicht"),
  ("leicht", "Leicht"),
  ("mittel", "Mittel"),
  ("schwer", "Schwer"),
  ("sehr schwer", "sehr Schwer"),
]


class EventForm(FlaskForm):
    event_name = StringField("Touren Name", validators=[InputRequired()])
    event_description = TextAreaField("Beschreibung")
    places = StringField("Plätze")
    waitlist = BooleanField("Mit Warteliste")
    start_date = DateField("Datum")
    start_time = TimeField("Zeit am Treffpunkt")
    end_date = DateField("Ende Datum")
    tour_link = StringField("Outdooractive Link")
    difficulty = RadioField("Schwierigkeit", choices=difficulties)
    length_h = StringField("Länge in Stunden")

    submit = SubmitField("Anlegen")
