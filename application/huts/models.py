"""
Hütten-Verwaltung: Hütte, Zimmer und Belegung/Buchung.

Gesamtkapazität einer Hütte ergibt sich aus der Summe der Zimmerplätze
(total_places), nicht aus einem eigenen Feld.
"""
#pylint: disable=too-few-public-methods, no-member
from datetime import datetime
from application import db


class HutRoom(db.EmbeddedDocument):
    """
    Ein Zimmer/Lager einer Hütte. Die Plätze aller Zimmer ergeben die
    Gesamtkapazität der Hütte.
    """
    name = db.StringField()
    places = db.IntField(default=0)

    meta = {'strict': False}


class HutBooking(db.EmbeddedDocument):
    """
    Belegung/Reservierung einer Hütte für einen Zeitraum. `to_date` ist der
    Abreisetag (nicht mehr belegt), Zeiträume gelten also halb-offen.
    """
    from_date = db.DateField()
    to_date = db.DateField()
    places = db.IntField(default=0)
    room = db.StringField()               # optional: bestimmtes Zimmer
    name = db.StringField()               # wer bucht (Freitext, z.B. Gruppe)
    user = db.ReferenceField(document_type='User')      # optional
    event = db.ReferenceField(document_type='Event')    # optional: verknüpfte Tour
    comment = db.StringField()
    created = db.DateTimeField(default=datetime.now)

    meta = {'strict': False}


class Hut(db.Document):
    """
    Eine Hütte der Sektion.
    """
    name = db.StringField(required=True)
    region = db.StringField()                 # Ort/Region
    managed = db.BooleanField(default=False)  # bewirtschaftet
    contact = db.StringField()
    phone = db.StringField()
    link = db.StringField()
    note = db.StringField()

    rooms = db.ListField(field=db.EmbeddedDocumentField(document_type=HutRoom))
    bookings = db.ListField(field=db.EmbeddedDocumentField(document_type=HutBooking))

    meta = {'strict': False}

    def total_places(self):
        """Gesamtkapazität = Summe der Zimmerplätze."""
        return sum((room.places or 0) for room in self.rooms)

    @staticmethod
    def _overlaps(a_start, a_end, b_start, b_end):
        """True wenn sich zwei halb-offene Zeiträume überschneiden."""
        if not (a_start and a_end and b_start and b_end):
            return False
        return a_start < b_end and b_start < a_end

    def booked_places(self, from_date, to_date):
        """Summe der belegten Plätze, die den Zeitraum überschneiden."""
        return sum(
            (booking.places or 0)
            for booking in self.bookings
            if self._overlaps(booking.from_date, booking.to_date, from_date, to_date)
        )

    def free_places(self, from_date, to_date):
        """Freie Plätze im Zeitraum (kann bei Überbuchung negativ sein)."""
        return self.total_places() - self.booked_places(from_date, to_date)

    def __str__(self):
        return self.name
