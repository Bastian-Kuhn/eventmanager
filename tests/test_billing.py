"""
Tests fuer Geldformatierung und Buchungsnummern.
Ausfuehren: .venv/bin/python -m unittest discover tests
Kein Mongo/Redis noetig, DB-Zugriffe sind gemockt.
"""
import unittest
from contextlib import nullcontext
from unittest import mock

from redis.exceptions import LockError

from application import app, format_euro
from application.events import models
from application.events.models import (
    Event, EventParticipation, new_booking_ref,
    BOOKING_REF_ALPHABET, BOOKING_REF_LENGTH,
)


class FormatEuroTest(unittest.TestCase):
    """Betraege immer mit zwei Nachkommastellen, deutsches Format"""

    def test_float_rest_is_rounded(self):
        self.assertEqual(format_euro(0.1 + 0.2), "0,30 €")

    def test_thousands_and_decimals(self):
        self.assertEqual(format_euro(1234.5), "1.234,50 €")

    def test_empty_and_negative(self):
        self.assertEqual(format_euro(None), "0,00 €")
        self.assertEqual(format_euro(-5), "-5,00 €")

    def test_jinja_filter(self):
        rendered = app.jinja_env.from_string("{{ x|euro }}").render(x=33.333333)
        self.assertEqual(rendered, "33,33 €")


class BookingRefTest(unittest.TestCase):
    """Kurze, eindeutige, gut abtippbare Buchungsnummer"""

    def _objects(self, hits):
        """Event.objects(...).only(...).first() liefert nacheinander hits"""
        objects = mock.MagicMock()
        objects.return_value.only.return_value.first.side_effect = hits
        return objects

    def test_format(self):
        with mock.patch.object(Event, 'objects', self._objects([None])):
            ref = new_booking_ref()
        self.assertEqual(len(ref), BOOKING_REF_LENGTH)
        self.assertTrue(set(ref) <= set(BOOKING_REF_ALPHABET))

    def test_alphabet_has_no_ambiguous_chars(self):
        for char in "0O1IL":
            self.assertNotIn(char, BOOKING_REF_ALPHABET)
        self.assertEqual(BOOKING_REF_ALPHABET, BOOKING_REF_ALPHABET.upper())

    def test_collision_is_retried(self):
        objects = self._objects([object(), None])
        with mock.patch.object(Event, 'objects', objects):
            new_booking_ref()
        self.assertEqual(objects.call_count, 2)


class EnsureBookingRefsTest(unittest.TestCase):
    """Altbestand bekommt Nummern, bestehende bleiben"""

    def _event(self, *refs):
        event = Event(id='0123456789abcdef01234567')
        event.participations = [EventParticipation(booking_ref=ref) for ref in refs]
        return event

    def _run(self, event, modified, lock=None):
        collection = mock.MagicMock()
        collection.update_one.side_effect = [mock.Mock(modified_count=m) for m in modified]
        with mock.patch.object(Event, '_get_collection', return_value=collection), \
             mock.patch.object(models, 'event_lock', return_value=lock or nullcontext()), \
             mock.patch.object(models, 'new_booking_ref', return_value='ABC234'), \
             mock.patch.object(Event, 'reload') as reload:
            event.ensure_booking_refs()
        return collection, reload

    def test_noop_when_all_have_refs(self):
        collection, reload = self._run(self._event('AAA222', 'BBB333'), [])
        collection.update_one.assert_not_called()
        reload.assert_not_called()

    def test_only_missing_refs_are_set(self):
        collection, reload = self._run(self._event('AAA222', None, None), [1, 1, 0])
        self.assertEqual(collection.update_one.call_count, 3)
        query, update = collection.update_one.call_args.args
        self.assertEqual(query['participations'], {'$elemMatch': {'booking_ref': None}})
        self.assertEqual(update, {'$set': {'participations.$.booking_ref': 'ABC234'}})
        reload.assert_called_once_with('participations')

    def test_busy_lock_is_skipped(self):
        lock = mock.MagicMock()
        lock.__enter__.side_effect = LockError()
        collection, reload = self._run(self._event(None), [], lock=lock)
        collection.update_one.assert_not_called()
        reload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
