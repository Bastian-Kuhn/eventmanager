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
from application.events.models import Event


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


if __name__ == '__main__':
    unittest.main()
