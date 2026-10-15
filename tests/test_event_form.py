"""
Tests fuer das vereinfachte Event-Formular: Datum und Uhrzeit in einem Feld,
nur der Beginn ist Pflicht, der Rest bekommt Vorgaben.
Ausfuehren: .venv/bin/python -m unittest discover tests
Kein Mongo/Redis noetig, DB-Zugriffe sind gemockt.
"""
import unittest
from datetime import datetime
from unittest import mock

from application import app
from application.events import views
from application.events.forms import EventForm
from application.events.models import Event, EventParticipation, OwnedTicket, Ticket


class EventFormDatesTest(unittest.TestCase):

    def save(self, **dates):
        data = {'event_name': 'Testtour', 'places': '7', **dates}
        user = mock.MagicMock()
        with app.test_request_context(method='POST', data=data), \
             mock.patch.object(views, 'current_user', user), \
             mock.patch.object(views, 'sync_event_booking'), \
             mock.patch.object(Event, 'save'):
            event = Event()
            views.save_event_form(event, EventForm(meta={'csrf': False}))
        return event

    def test_only_start_given(self):
        event = self.save(start_date='2030-05-01T08:30')
        start = datetime(2030, 5, 1, 8, 30)
        self.assertEqual(event.start_date, start)
        self.assertEqual(event.end_date, start)
        self.assertEqual(event.booking_until, start)
        self.assertLessEqual(event.booking_from, datetime.now())

    def test_all_given(self):
        event = self.save(start_date='2030-05-01T08:30', end_date='2030-05-02T17:00',
                          booking_from='2030-01-01T00:00', booking_until='2030-04-30T12:00')
        self.assertEqual(event.end_date, datetime(2030, 5, 2, 17, 0))
        self.assertEqual(event.booking_from, datetime(2030, 1, 1, 0, 0))
        self.assertEqual(event.booking_until, datetime(2030, 4, 30, 12, 0))

    def test_existing_event_renders_for_input(self):
        with app.test_request_context():
            form = EventForm(obj=Event(start_date=datetime(2030, 5, 1, 8, 30)), meta={'csrf': False})
            self.assertIn('value="2030-05-01T08:30"', form.start_date())
            self.assertIn('type="datetime-local"', form.start_date())

    def test_new_event_preselects_keine(self):
        with app.test_request_context():
            form = EventForm(meta={'csrf': False})
            self.assertIn('checked id="difficulty-0"', form.difficulty())
            self.assertIn('checked id="shape-0"', form.shape())
            self.assertEqual(form.difficulty.data, 'keine')

    def test_existing_event_keeps_difficulty(self):
        with app.test_request_context():
            form = EventForm(obj=Event(difficulty='schwer', shape='mittel'), meta={'csrf': False})
            self.assertEqual(form.difficulty.data, 'schwer')
            self.assertEqual(form.shape.data, 'mittel')


class EventFormTicketsTest(unittest.TestCase):
    """Ticket-Definitionen im Formular und ihre Wirkung auf gebuchte Tickets"""

    def open_form(self, event):
        """Wie page_admin beim Oeffnen: Vorlagenzeile 0 plus die echten Tickets"""
        with app.test_request_context():
            event = views.event_populate(event)
            form = EventForm(obj=event, meta={'csrf': False})
            return views.populate_event_form(form, event)

    def test_template_row_stays_empty(self):
        """Die Vorlage fuer "Weiteres Ticket" erbt nichts vom zuvor geoeffneten Event"""
        self.open_form(Event(tickets=[Ticket(name='Leihausruestung', price=5.0,
                                             maximum_tickets=3, is_extra_ticket=True)]))
        form = self.open_form(Event(tickets=[Ticket(name='Teilnahme', price=10.0, maximum_tickets=20)]))
        template = form.tickets[0].form
        self.assertFalse(template.name.data)
        self.assertFalse(template.is_extra_ticket.data)
        self.assertEqual(template.maximum_tickets.data, 0)

    def test_save_applies_opt_flag_to_booked_tickets(self):
        """Wird der Haken "Opt. Ticket" geaendert, folgen die schon gebuchten Tickets"""
        main = OwnedTicket(ticket_id='m1', ticket_name='Teilnahme', is_extra_ticket=True)
        extra = OwnedTicket(ticket_id='e1', ticket_name='Leihausruestung', is_extra_ticket=False)
        gone = OwnedTicket(ticket_id='g1', ticket_name='Alter Name', is_extra_ticket=True)
        event = Event(participations=[EventParticipation(tickets=[main, extra, gone])])
        data = {'event_name': 'Testtour', 'places': '7', 'start_date': '2030-05-01T08:30',
                'tickets-1-name': 'Teilnahme', 'tickets-1-price': '10', 'tickets-1-maximum_tickets': '20',
                'tickets-2-name': 'Leihausruestung', 'tickets-2-price': '5',
                'tickets-2-maximum_tickets': '0', 'tickets-2-is_extra_ticket': 'y'}
        with app.test_request_context(method='POST', data=data), \
             mock.patch.object(views, 'current_user', mock.MagicMock()), \
             mock.patch.object(views, 'sync_event_booking'), \
             mock.patch.object(Event, 'save'):
            views.save_event_form(event, EventForm(meta={'csrf': False}))
        self.assertFalse(main.is_extra_ticket)
        self.assertTrue(extra.is_extra_ticket)
        self.assertTrue(gone.is_extra_ticket)


if __name__ == '__main__':
    unittest.main()
