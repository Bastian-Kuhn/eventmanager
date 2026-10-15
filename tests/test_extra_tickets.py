"""
Tests fuer Zusatztickets, die direkt einer Person (Haupt-Ticket) zugeordnet
werden und deren Personendaten uebernehmen.
Ausfuehren: .venv/bin/python -m unittest discover tests
Kein Mongo/Redis noetig, DB-Zugriffe sind gemockt.
"""
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from unittest import mock

from application import app
from application.events import views
from application.events.models import Event, EventParticipation, OwnedTicket, Ticket


def owned(ticket_id, name, main=None):
    """Gebuchtes Ticket mit Personendaten"""
    return OwnedTicket(ticket_id=ticket_id, name_on_ticket=name, email_on_ticket=f"{ticket_id}@example.org",
                       phone_on_ticket="0123", main_ticket_id=main)


class SyncLinkedTicketsTest(unittest.TestCase):
    """Personendaten des Haupt-Tickets wandern auf seine Zusatztickets"""

    def test_take_person_from(self):
        main = owned('m1', 'Erika Muster')
        extra = OwnedTicket(ticket_id='e1')
        extra.take_person_from(main)
        self.assertEqual(extra.main_ticket_id, 'm1')
        self.assertEqual(extra.name_on_ticket, 'Erika Muster')
        self.assertEqual(extra.email_on_ticket, 'm1@example.org')

    def test_only_linked_tickets_change(self):
        main = owned('m1', 'Neuer Name')
        linked = owned('e1', 'Alter Name', main='m1')
        other = owned('e2', 'Andere Person', main='m2')
        unlinked = owned('e3', 'Ohne Bezug')
        event = Event(participations=[EventParticipation(tickets=[main, linked, other, unlinked])])
        result = event.sync_linked_tickets(main)
        self.assertEqual(result, [linked])
        self.assertEqual(linked.name_on_ticket, 'Neuer Name')
        self.assertEqual(other.name_on_ticket, 'Andere Person')
        self.assertEqual(unlinked.name_on_ticket, 'Ohne Bezug')


class RegisterExtrasPerPersonTest(unittest.TestCase):
    """Anmeldung: Zusatztickets werden pro Person gewaehlt und verknuepft"""

    def setUp(self):
        now = datetime.now()
        self.event = Event(
            id='0123456789abcdef01234567', event_name='Testevent', event_type='notour',
            start_date=now + timedelta(days=10), end_date=now + timedelta(days=11),
            tickets=[Ticket(name='Teilnahme', price=10.0, maximum_tickets=3),
                     Ticket(name='Leihausruestung', price=5.0, maximum_tickets=2, is_extra_ticket=True),
                     Ticket(name='Busfahrt', price=8.0, maximum_tickets=3, is_extra_ticket=True)],
            participations=[])
        self.user = mock.MagicMock(is_authenticated=True, first_name='Max', last_name='Muster',
                                   email='max@example.org', phone='0123', birthdate=None)

    def _post(self, form):
        objects = mock.MagicMock()
        objects.get.return_value = self.event
        with mock.patch.object(views.Event, 'objects', objects), \
             mock.patch.object(Event, 'reload'), \
             mock.patch.object(views, 'event_lock', return_value=nullcontext()), \
             mock.patch.object(views, 'new_booking_ref', return_value='ABC234'), \
             mock.patch.object(views, 'send_email'), \
             mock.patch.object(views, 'current_user', self.user), \
             mock.patch('flask_login.utils._get_user', return_value=self.user), \
             mock.patch.object(views, 'render_template', return_value='form'), \
             mock.patch.object(views, 'flash') as flash:
            app.config['WTF_CSRF_ENABLED'] = False
            with app.test_client() as client:
                response = client.post(f'/event/details?event_id={self.event.id}', data=form)
        update = objects.return_value.update_one
        participation = update.call_args.kwargs['push__participations'] if update.called else None
        return response, participation, flash

    def test_extras_are_linked_to_their_person(self):
        response, parti, _ = self._post({
            'ticket_Teilnahme': '2', 'comment': '',
            'extra_0_0': ['1', '2'], 'extra_0_1': '2',
        })
        self.assertEqual(response.status_code, 302)
        mains = [x for x in parti.tickets if not x.is_extra_ticket]
        extras = [x for x in parti.tickets if x.is_extra_ticket]
        self.assertEqual(len(mains), 2)
        self.assertEqual(sorted(x.ticket_name for x in extras), ['Busfahrt', 'Busfahrt', 'Leihausruestung'])
        leih = next(x for x in extras if x.ticket_name == 'Leihausruestung')
        self.assertEqual(leih.main_ticket_id, mains[0].ticket_id)
        bus = sorted(x.main_ticket_id for x in extras if x.ticket_name == 'Busfahrt')
        self.assertEqual(bus, sorted(x.ticket_id for x in mains))
        self.assertEqual(leih.name_on_ticket, 'Max Muster')

    def test_extra_for_unbooked_person_is_rejected(self):
        _, parti, flash = self._post({'ticket_Teilnahme': '1', 'comment': '', 'extra_0_2': '1'})
        self.assertIsNone(parti)
        self.assertTrue(flash.called)

    def test_extra_limit_per_booking(self):
        _, parti, _ = self._post({'ticket_Teilnahme': '3', 'comment': '',
                                  'extra_0_0': '1', 'extra_0_1': '1', 'extra_0_2': '1'})
        self.assertIsNone(parti)

    def test_several_main_ticket_types(self):
        # Jede gebuchte Person jedes Haupt-Tickets bekommt ihre eigenen Zusatztickets
        self.event.tickets.insert(1, Ticket(name='Jugend', price=5.0, maximum_tickets=2))
        response, parti, _ = self._post({
            'ticket_Teilnahme': '1', 'ticket_Jugend': '2', 'comment': '',
            'extra_0_0': '2', 'extra_1_1': ['2', '3'],
        })
        self.assertEqual(response.status_code, 302)
        by_name = {}
        for ticket in parti.tickets:
            by_name.setdefault(ticket.ticket_name, []).append(ticket)
        self.assertEqual(len(by_name['Jugend']), 2)
        leih = sorted(x.main_ticket_id for x in by_name['Leihausruestung'])
        self.assertEqual(leih, sorted([by_name['Teilnahme'][0].ticket_id, by_name['Jugend'][1].ticket_id]))
        self.assertEqual(by_name['Busfahrt'][0].main_ticket_id, by_name['Jugend'][1].ticket_id)

    def test_extras_without_limit(self):
        # Opt. Tickets mit Anzahl 0 (ohne Limit) und zwei Haupt-Tickets, eins davon gratis
        self.event.tickets = [Ticket(name='Erwachsene', price=50.0, maximum_tickets=4),
                              Ticket(name='Kind', price=0.0, maximum_tickets=4),
                              Ticket(name='Kurs', price=100.0, maximum_tickets=0, is_extra_ticket=True),
                              Ticket(name='Liftkarte', price=0.0, maximum_tickets=0, is_extra_ticket=True)]
        response, parti, _ = self._post({
            'ticket_Erwachsene': '1', 'ticket_Kind': '2', 'comment': '',
            'extra_0_0': '3', 'extra_1_0': ['2', '3'], 'extra_1_1': ['2', '3'],
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(parti.waitinglist)
        extras = [x for x in parti.tickets if x.is_extra_ticket]
        self.assertEqual(sorted(x.ticket_name for x in extras), ['Kurs', 'Kurs', 'Liftkarte', 'Liftkarte', 'Liftkarte'])
        self.assertFalse(any(x.waitinglist for x in extras))
        mains = {x.ticket_id for x in parti.tickets if not x.is_extra_ticket}
        self.assertTrue(all(x.main_ticket_id in mains for x in extras))


if __name__ == '__main__':
    unittest.main()
