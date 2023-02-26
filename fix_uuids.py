#!/usr/bin/env python3
from application.events.models import Event
import uuid


for event in Event.objects():
    for pari in event.participations:
        for ticket in pari.tickets:
            if not ticket.ticket_id:
                ticket.ticket_id = uuid.uuid4().hex
    event.save()
