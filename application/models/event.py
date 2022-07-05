"""
Events
"""
from application import db

difficulties = [
  ("sehr leicht", "Sehr Leicht"),
  ("leicht", "Leicht"),
  ("mittel", "Mittel"),
  ("schwer", "Schwer"),
  ("sehr schwer", "sehr Schwer"),
]


categories = [
 ('mtb', "Mountain Bike"),
 ('skitour', "Skitour"),
 ('hike', "Wandern"),
 ('alpine_tour', "Hochtour"),
 ('ski_alpine_tour', "Ski Hochtour"),
]

class EventParticipation(db.EmbeddedDocument):
    """
    Event Entry
    """

    user = db.ReferenceField('User')

    comment = db.StringField()
    confirmed = db.BooleanField(default=False)
    waitinglist = db.BooleanField(default=False)


class Event(db.Document):
    event_name = db.StringField()
    event_description = db.StringField()
    event_category = db.StringField(choices=categories)

    places = db.StringField()
    waitlist = db.BooleanField()
    start_date = db.DateTimeField()
    end_date = db.DateTimeField()
    tour_link = db.StringField()
    difficulty = db.StringField(choices=difficulties)
    length_h = db.StringField()
    length_km= db.StringField()
    altitude_difference= db.StringField()

    participations = db.ListField(db.EmbeddedDocumentField(EventParticipation))


    def __str__(self):
        return self.event_name
