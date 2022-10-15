"""
Events
"""
#pylint: disable=too-few-public-methods
from application import db

difficulties = [
  ("sehr leicht", "Sehr Leicht"),
  ("leicht", "Leicht"),
  ("mittel", "Mittel"),
  ("schwer", "Schwer"),
  ("sehr schwer", "sehr Schwer"),
]


categories = [
 (None, "Kategorie"),
 ('mtb', "Mountain Bike"),
 ('skitour', "Skitour"),
 ('hike', "Wandern"),
 ('alpine_tour', "Hochtour"),
 ('ski_alpine_tour', "Ski Hochtour"),
]

class Ticket(db.EmbeddedDocument):
    """
    Tickets to buy with event
    """
    id = db.StringField()
    name = db.StringField()
    description = db.StringField()
    price = db.FloatField()
    maximum_tickets = db.IntField()

class CustomFieldDefintion(db.EmbeddedDocument):
    """ Extra Questions for Events """
    field_name = db.StringField()

class CustomField(db.EmbeddedDocument):
    """ Extra Questions for Participants"""
    name = db.StringField()
    value = db.StringField()


class EventParticipation(db.EmbeddedDocument):
    """
    Event Participation Entry
    """

    user = db.ReferenceField('User')

    custom_fields = db.ListField(db.EmbeddedDocumentField(CustomField))

    comment = db.StringField()
    confirmed = db.BooleanField(default=False)
    waitinglist = db.BooleanField(default=False)

    def get_field(self, fieldname):
        """
        Return given field from participation
        """
        return {x.name: x.value for x in self.custom_fields }.get(fieldname, 'ubk')


class Event(db.Document):
    """
    Event Entry
    """
    event_name = db.StringField()
    event_description = db.StringField()
    event_category = db.StringField(choices=categories)
    event_owners = db.ListField(db.ReferenceField('User'))

    places = db.IntField()
    waitlist = db.BooleanField()
    booking_from = db.DateTimeField()
    booking_until = db.DateTimeField()
    start_date = db.DateTimeField()
    end_date = db.DateTimeField()
    tour_link = db.StringField()
    difficulty = db.StringField(choices=difficulties)
    length_h = db.StringField()
    length_km= db.StringField()
    altitude_difference= db.StringField()

    custom_fields = db.ListField(db.EmbeddedDocumentField(CustomFieldDefintion))

    participations = db.ListField(db.EmbeddedDocumentField(EventParticipation))
    tickets = db.ListField(db.EmbeddedDocumentField(Ticket))



    meta = {
        'strict': False,
    }


    def change_user_status(self, userid, status):
        """
        Change the given user to giben status
        """
        if status not in ['confirmed', 'unconfirmed',
                          'waitinglist_on', 'waitinglist_off']:
            raise Exception(f"Unkonwn Status: {status}")

        waitinglist, confirmed = False, False
        for participation in self.participations:
            if str(participation.user.id) == userid:
                if status == "confirmed":
                    waitinglist = False
                    confirmed = True
                elif status == "unconfirmed":
                    waitinglist = False
                    confirmed = False
                elif status == "waitinglist_on":
                    waitinglist = True
                    confirmed = False
                elif status == "waitinglist_off":
                    waitinglist = False
                    confirmed = True
                participation.confirmed = confirmed
                participation.waitinglist = waitinglist
                self.save()
                return {
                    'confirmed': confirmed,
                    'waitinglist': waitinglist,
                }
        return {
            'error': True
        }


    def get_numbers(self):
        """
        Return Stats about event booking
        """
        confirmed = 0
        wait_for_confirm = 0
        waitinglist = 0
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
            'waitlist': waitinglist,
        }


    def __str__(self):
        return self.event_name
