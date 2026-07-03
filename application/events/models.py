"""
Events
"""
#pylint: disable=too-few-public-methods, no-member
from random import choices
from wtforms import StringField
from application import db, app

difficulties = [
    ('keine', "Keine"),
    ("sehr leicht", "Sehr Leicht"),
    ("leicht", "Leicht"),
    ("mittel", "Mittel"),
    ("schwer", "Schwer"),
    ("sehr schwer", "sehr Schwer"),
]


class Ticket(db.EmbeddedDocument):
    """
    Tickets to buy with event
    """
    #id = db.StringField()
    name = db.StringField()
    description = db.StringField()
    price = db.FloatField()
    is_extra_ticket = db.BooleanField(default=False)
    maximum_tickets = db.IntField(default=False)

class OwnedTicket(db.EmbeddedDocument):
    """
    Field stored in participation
    """
    ticket_id = db.StringField()
    name_on_ticket = db.StringField()
    email_on_ticket = db.StringField()
    phone_on_ticket = db.StringField()
    comment_on_ticket = db.StringField()
    birthdate_on_ticket = db.DateField()

    ticket_name = db.StringField()
    ticket_comment = db.StringField()
    guide_comment = db.StringField()  # Kommentar nur für Guides sichtbar
    guide = db.StringField()  # Guide Zuordnung

    confirmed = db.BooleanField()
    waitinglist = db.BooleanField()

    is_extra_ticket = db.BooleanField(default=False)
    is_paid = db.BooleanField(default=False)
    is_free = db.BooleanField(default=False)  # Teilnehmer zahlt nichts, gilt als erledigt
    is_transfer = db.BooleanField(default=False)  # Ueberweisung vorgemerkt, noch nicht eingegangen
    custom_price = db.FloatField()  # Individual price override for guides

class EventCost(db.EmbeddedDocument):
    """
    Ausgabe/Kosten eines Events
    """
    name = db.StringField()
    person = db.StringField()  # Empfaenger der Ausgabe
    price = db.FloatField()
    date = db.DateField()
    is_paid = db.BooleanField(default=False)  # Wurde an die Person ausgezahlt

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

    user = db.ReferenceField(document_type='User')
    booking_date = db.DateTimeField()

    custom_fields = db.ListField(field=db.EmbeddedDocumentField(document_type='CustomField'))
    tickets = db.ListField(field=db.EmbeddedDocumentField(document_type='OwnedTicket'))

    comment = db.StringField()

    # Ueberschreibt das globale User.media_optin fuer genau dieses Event.
    # None = globalen Wert aus dem Profil verwenden, True/False = bewusst gesetzt.
    media_optin = db.BooleanField()

    def get_field(self, fieldname):
        """
        Return given field from participation
        """
        return {x.name: x.value for x in self.custom_fields}.get(fieldname, 'ubk')

    meta = {
        'strict': False,
    }

event_types = [
  ('guided', "Von Guide geführt"),
  ('youthguide', "Jugendleiter geführt"),
  ('unguided', "Gemeinschafts Tour"),
  ('notour', "Event/ Termin"),
]

class Event(db.Document):
    """
    Event Entry
    """
    event_name = db.StringField()
    event_teaser = db.StringField()
    event_type = db.StringField(choices=event_types)
    event_description = db.StringField()
    event_category = db.StringField()
    event_owners = db.ListField(field=db.ReferenceField(document_type='User'))
    places = db.IntField()
    min_places = db.IntField()

    booking_from = db.DateTimeField()
    booking_until = db.DateTimeField()
    start_date = db.DateTimeField()
    end_date = db.DateTimeField()
    tour_link = db.StringField()
    difficulty = db.StringField(choices=difficulties)
    shape = db.StringField(choices=difficulties)
    length_h = db.StringField()
    length_km = db.StringField()
    altitude_difference = db.StringField()

    custom_fields = db.ListField(field=db.EmbeddedDocumentField(document_type=CustomFieldDefintion))

    participations = db.ListField(field=db.EmbeddedDocumentField(document_type=EventParticipation))
    tickets = db.ListField(field=db.EmbeddedDocumentField(document_type=Ticket))
    costs = db.ListField(field=db.EmbeddedDocumentField(document_type=EventCost))
    
    # Global hidden categories for all users
    hidden_categories = db.ListField(field=db.StringField())



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
                if ticket.is_extra_ticket:
                    continue
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

    def get_booked_ticket(self, ticket_id, user=False):
        """
        Get Event Particiation by ID
        """
        for parti in self.participations:
            if user:
                if parti.user == user:
                    for ticket in parti.tickets:
                        if ticket.ticket_id == ticket_id:
                            return ticket
            else:
                for ticket in parti.tickets:
                    if ticket.ticket_id == ticket_id:
                        return ticket

    def get_tickets_of_user(self, user):
        for parti in self.participations:
            if parti.user == user:
                for ticket in parti.tickets:
                    yield ticket

    def get_participation(self, user):
        """
        Return the EventParticipation of the given user, or None.
        """
        for parti in self.participations:
            if parti.user == user:
                return parti
        return None

    def is_over(self, now):
        """
        True wenn das Event beendet ist. end_date hat Vorrang, sonst start_date.
        """
        end = self.end_date or self.start_date
        return bool(end and end < now)

    def is_started(self, now):
        """
        True wenn die Tour begonnen hat (start_date erreicht).
        """
        return bool(self.start_date and self.start_date <= now)

    def delete_ticket(self, ticket_id):
        """
        Delete given Ticket id
        """
        found = False
        last = False
        for parti in self.participations:
            for ticket in parti.tickets:
                if ticket.ticket_id == ticket_id:
                    parti.tickets.remove(ticket)
                    found = True
                    break
        if found:
            if not parti.tickets:
                last = True
                user = parti.user
                user.event_registrations.remove(self)
                user.save()
                self.participations.remove(parti)
        return {
            'found': found,
            'last': last,
            'ticket_id': ticket_id,
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
