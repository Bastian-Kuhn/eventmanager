"""
Events
"""
#pylint: disable=too-few-public-methods, no-member
from application import db

difficulties = [
    ("sehr leicht", "Sehr Leicht"),
    ("leicht", "Leicht"),
    ("mittel", "Mittel"),
    ("schwer", "Schwer"),
    ("sehr schwer", "sehr Schwer"),
]


categories = [
    ('mtb', "Kategorie"),
    ('skitour', "Skitour"),
    ('mtb', "Mountain Bike"),
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

class OwnedTicket(db.EmbeddedDocument):
    """
    Field stored in participation
    """
    ticket_name = db.StringField()
    ticket_comment = db.StringField()
    confirmed = db.BooleanField()
    waitinglist = db.BooleanField()
    name_on_ticket = db.StringField()

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
    booking_date = db.DateTimeField()

    custom_fields = db.ListField(db.EmbeddedDocumentField(CustomField))
    tickets = db.ListField(db.EmbeddedDocumentField(OwnedTicket))

    comment = db.StringField()

    def get_field(self, fieldname):
        """
        Return given field from participation
        """
        return {x.name: x.value for x in self.custom_fields}.get(fieldname, 'ubk')

    meta = {
        'strict': False,
    }


class Event(db.Document):
    """
    Event Entry
    """
    event_name = db.StringField()
    event_teaser = db.StringField()
    event_description = db.StringField()
    event_category = db.StringField(choices=categories)
    event_owners = db.ListField(db.ReferenceField('User'))
    places = db.IntField()

    booking_from = db.DateTimeField()
    booking_until = db.DateTimeField()
    start_date = db.DateTimeField()
    end_date = db.DateTimeField()
    tour_link = db.StringField()
    difficulty = db.StringField(choices=difficulties)
    length_h = db.StringField()
    length_km = db.StringField()
    altitude_difference = db.StringField()

    custom_fields = db.ListField(db.EmbeddedDocumentField(CustomFieldDefintion))

    participations = db.ListField(db.EmbeddedDocumentField(EventParticipation))
    tickets = db.ListField(db.EmbeddedDocumentField(Ticket))



    meta = {
        'strict': False,
    }


    def get_numbers(self):
        """
        Return Stats about event booking
        """
        confirmed = 0
        wait_for_confirm = 0
        waitinglist = 0
        for parti in self.participations:
            for ticket in parti.tickets:
                if ticket.confirmed:
                    confirmed += 1
                else:
                    wait_for_confirm += 1
                if ticket.waitinglist:
                    waitinglist += 1
        return {
            'total_places' : self.places,
            'confirmed': confirmed,
            'wait_for_confirm': wait_for_confirm,
            'waitlist': waitinglist,
        }

    def get_ticket_stats(self):
        """
        Return Booking Stats of Tickets
        """
        counts = {}
        total = 0
        max_tickets = {x.name: x.maximum_tickets for x in self.tickets}
        for parti in self.participations:
            for ticket in parti.tickets:
                total += 1
                counts.setdefault(ticket.ticket_name, 0)
                counts[ticket.ticket_name] += 1
        has_places = {}
        for ticket_name, num in counts.items():
            has_places[ticket_name] = False
            if num < max_tickets.get(ticket_name, 0):
                has_places[ticket_name] = True

        counts['total'] = total
        counts['max'] = max_tickets
        counts['has_places'] = has_places


        return counts



    def __str__(self):
        return self.event_name
