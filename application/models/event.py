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
    event_owners = db.ListField(db.ReferenceField('User'))

    places = db.IntField()
    waitlist = db.BooleanField()
    start_date = db.DateTimeField()
    #start_time = db.StringField() #Helper to populate form
    end_date = db.DateTimeField()
    #end_time = db.StringField() #Helper to populate form
    tour_link = db.StringField()
    difficulty = db.StringField(choices=difficulties)
    length_h = db.StringField()
    length_km= db.StringField()
    altitude_difference= db.StringField()

    participations = db.ListField(db.EmbeddedDocumentField(EventParticipation))


    meta = {
        'strict': False,
    }


    def get_numbers(self):
        confirmed = 0
        wait_for_confirm = 0
        waitlist = 0
        for participation in self.participations:
            if participation.confirmed:
                confirmed += 1
            elif not participation.waitinglist:
                wait_for_confirm += 1
            if participation.waitinglist:
                waitinglist += 1
        return {
            'total_places' : self.places,
            'confirmed': confirmed,
            'wait_for_confirm': wait_for_confirm,
            'waitlist': waitlist,
        }


    def __str__(self):
        return self.event_name
