"""
Hütten-Verwaltung: Hütte, Zimmer (eingebettet) und Belegung/Buchung.

Buchungen liegen in einer **eigenen Collection** (HutBooking als Document), nicht
eingebettet in der Hütte – sie wachsen unbegrenzt und werden nebenläufig geschrieben;
eingebettet würden sie das Hütten-Dokument aufblähen (16-MB-Limit) und bei
gleichzeitigem Speichern Buchungen überschreiben.

Gesamtkapazität einer Hütte ergibt sich aus der Summe der Zimmerplätze (total_places).
"""
#pylint: disable=too-few-public-methods, no-member
from datetime import datetime
from application import db


class HutRoom(db.EmbeddedDocument):
    """
    Ein Zimmer/Lager einer Hütte. Die Plätze aller Zimmer ergeben die
    Gesamtkapazität der Hütte. (Bounded -> eingebettet ist ok.)
    """
    name = db.StringField()
    places = db.IntField(default=0)

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

    # Selbstbuchung durch Mitglieder: bei True muss ein Hütten-Admin/Guide freigeben,
    # sonst ist die Buchung sofort bestätigt.
    requires_approval = db.BooleanField(default=False)
    admins = db.ListField(field=db.ReferenceField(document_type='User'))

    rooms = db.ListField(field=db.EmbeddedDocumentField(document_type=HutRoom))

    meta = {'strict': False}

    def total_places(self):
        """Gesamtkapazität = Summe der Zimmerplätze."""
        return sum((room.places or 0) for room in self.rooms)

    def booked_places(self, from_date, to_date):
        """Belegte Plätze im (halb-offenen) Zeitraum; hält auch offene Anfragen."""
        if not (from_date and to_date):
            return 0
        overlapping = HutBooking.objects(
            hut=self, from_date__lt=to_date, to_date__gt=from_date)
        return sum((booking.places or 0) for booking in overlapping)

    def free_places(self, from_date, to_date):
        """Freie Plätze im Zeitraum (kann bei Überbuchung negativ sein)."""
        return self.total_places() - self.booked_places(from_date, to_date)

    def get_booking(self, booking_id):
        """Buchung dieser Hütte per id (oder None)."""
        return HutBooking.objects(id=booking_id, hut=self).first()

    def can_manage(self, user):
        """Darf der User Buchungen dieser Hütte freigeben/verwalten?"""
        if not (user and user.is_authenticated):
            return False
        if user.has_right('guide'):
            return True
        return any(str(getattr(admin, 'id', None)) == str(user.id) for admin in self.admins)

    def __str__(self):
        return self.name


class HutBooking(db.Document):
    """
    Belegung/Reservierung einer Hütte für einen Zeitraum. `to_date` ist der
    Abreisetag (nicht mehr belegt), Zeiträume gelten also halb-offen.

    Eigene Collection (siehe Moduldoc). `confirmed=False` = wartet auf Freigabe.
    """
    hut = db.ReferenceField(document_type='Hut', required=True)
    from_date = db.DateField()
    to_date = db.DateField()
    places = db.IntField(default=0)
    rooms = db.ListField(field=db.StringField())  # optional: gewählte Zimmer (mehrere)
    name = db.StringField()               # wer bucht (Freitext, z.B. Gruppe)
    user = db.ReferenceField(document_type='User')      # optional
    event = db.ReferenceField(document_type='Event')    # optional: verknüpfte Tour
    comment = db.StringField()
    confirmed = db.BooleanField(default=False)
    blocked = db.BooleanField(default=False)  # Admin-Sperre (Hütte im Zeitraum nicht buchbar)
    created = db.DateTimeField(default=datetime.now)

    meta = {
        'strict': False,
        'indexes': ['hut', 'event', ('hut', 'from_date')],
    }

    def __str__(self):
        return f"{self.hut.name if self.hut else '?'} {self.from_date}–{self.to_date}"


def _as_date(value):
    """DateTime/Date -> date (oder None)."""
    if value is None:
        return None
    return value.date() if hasattr(value, 'date') else value


def sync_event_booking(event):
    """
    Hält die mit einer Tour verknüpfte Hüttenbuchung konsistent: höchstens **eine**
    Buchung pro Event. Ist der Tour eine Hütte + Start/Ende zugeordnet, wird eine
    (bestätigte) Buchung über den Tour-Zeitraum mit `event.places` Plätzen angelegt;
    sonst wird eine evtl. vorhandene entfernt. Idempotent.
    """
    HutBooking.objects(event=event).delete()

    from_date = _as_date(event.start_date)
    to_date = _as_date(event.end_date)
    if event.hut and from_date and to_date:
        HutBooking(
            hut=event.hut,
            from_date=from_date,
            to_date=to_date,
            places=event.places or 0,
            name=event.event_name,
            event=event,
            comment="Automatisch aus Tour verknüpft",
            confirmed=True,  # vom Guide angelegt -> direkt bestätigt
        ).save()


def remove_event_booking(event):
    """Entfernt alle mit dem Event verknüpften Hüttenbuchungen (z.B. beim Löschen)."""
    HutBooking.objects(event=event).delete()
