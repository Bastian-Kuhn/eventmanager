"""
Hütten-Verwaltung: Hütte, Zimmer (eingebettet) und Belegung/Buchung.

Buchungen liegen in einer **eigenen Collection** (HutBooking als Document), nicht
eingebettet in der Hütte – sie wachsen unbegrenzt und werden nebenläufig geschrieben;
eingebettet würden sie das Hütten-Dokument aufblähen (16-MB-Limit) und bei
gleichzeitigem Speichern Buchungen überschreiben.

Gesamtkapazität einer Hütte ergibt sich aus der Summe der Zimmerplätze (total_places).
"""
#pylint: disable=too-few-public-methods, no-member
from datetime import datetime, timedelta
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
    # False = Mitglieder können nicht selbst buchen; nur Hütten-Admins/Guides tragen
    # Buchungen ein (Touren-Buchungen entstehen weiterhin automatisch).
    allow_self_booking = db.BooleanField(default=True)
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

    def has_capacity(self):
        """
        True, wenn Plätze hinterlegt sind. Ohne Zimmer ist die Kapazität schlicht
        unbekannt – dann darf die Hütte nicht als "ausgebucht" gelten.
        """
        return self.total_places() > 0

    def blocks(self, from_date, to_date):
        """Admin-Sperren, die den Zeitraum überschneiden."""
        if not (from_date and to_date):
            return []
        return list(HutBooking.objects(hut=self, blocked=True,
                                       from_date__lt=to_date, to_date__gt=from_date))

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
    """DateTime/Date oder 'YYYY-MM-DD…'-String -> date (oder None)."""
    if not value:
        return None
    if isinstance(value, str):
        # save_event_form setzt Datum und Zeit erst beim Speichern zusammen; im
        # Event-Objekt steht danach noch der Roh-String.
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return value.date() if hasattr(value, 'date') else value


def sync_event_booking(event):
    """
    Hält die mit einer Tour verknüpfte Hüttenbuchung konsistent: höchstens **eine**
    Buchung pro Event. Ist der Tour eine Hütte + Startdatum zugeordnet, wird eine
    Buchung über den Tour-Zeitraum mit `event.places` Plätzen angelegt bzw.
    aktualisiert; sonst wird eine evtl. vorhandene entfernt. Idempotent.

    Wie bei einer Selbstbuchung ist die Buchung nur sofort bestätigt, wenn die Hütte
    keine Freigabe verlangt. Eine bereits erteilte Freigabe bleibt erhalten, solange
    sich an Hütte, Zeitraum, Plätzen und Zimmern nichts ändert.
    """
    bookings = list(HutBooking.objects(event=event).order_by('created'))
    booking = bookings[0] if bookings else None
    for duplicate in bookings[1:]:
        duplicate.delete()

    from_date = _as_date(event.start_date)
    to_date = _as_date(event.end_date)
    if not (event.hut and from_date):
        if booking:
            booking.delete()
        return

    # Mindestens eine Nacht: bei leerem Zeitraum (Ende = Beginn) wäre die Buchung
    # sonst nirgends als Belegung sichtbar, weil Zeiträume halb-offen gelten.
    if not to_date or to_date <= from_date:
        to_date = from_date + timedelta(days=1)

    rooms = [room for room in (event.hut_rooms or []) if room]
    try:
        places = int(event.places or 0)
    except (TypeError, ValueError):
        places = 0

    if booking is None:
        booking = HutBooking(event=event)
        changed = True
    else:
        changed = (booking.hut != event.hut
                   or booking.from_date != from_date
                   or booking.to_date != to_date
                   or (booking.places or 0) != places
                   or list(booking.rooms or []) != rooms)

    booking.hut = event.hut
    booking.from_date = from_date
    booking.to_date = to_date
    booking.places = places
    booking.rooms = rooms
    booking.name = event.event_name
    booking.comment = "Automatisch aus Tour verknüpft"
    if changed:
        booking.confirmed = not event.hut.requires_approval
    booking.save()


def remove_event_booking(event):
    """Entfernt alle mit dem Event verknüpften Hüttenbuchungen (z.B. beim Löschen)."""
    HutBooking.objects(event=event).delete()
