"""
App-API: Anmeldung ueber /app/authorize, Schluessel pro Sektion, Touren
buchen, anlegen und bearbeiten, Teilnehmerliste und Huetten.
Ausfuehren: .venv/bin/python -m unittest tests.test_api (mongomock)
"""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

import mongoengine
import mongomock
from mongomock.gridfs import enable_gridfs_integration

from application import app, limiter
from application.events.models import Event, Ticket
from application.huts.models import Hut, HutBooking, HutRoom
from application.models.api_token import ApiToken
from application.models.config import Config
from application.models.section import Section
from application.models.user import Membership, User
from tests import test_section_isolation as isolation
from tests.test_section_isolation import A, B
from tests.test_security import NEW_PASSWORD, PASSWORD


def setUpModule():
    enable_gridfs_integration()
    mongoengine.disconnect()
    mongoengine.connect('test', mongo_client_class=mongomock.MongoClient)


def tearDownModule():
    mongoengine.disconnect()


class ApiTest(unittest.TestCase):
    """Sektion Alpha mit Mitglied, Tourenbegleiter und Trainer, Sektion Beta daneben"""

    event = staticmethod(isolation.IsolationTest.event)
    user = staticmethod(isolation.IsolationTest.user)
    login = staticmethod(isolation.IsolationTest.login)

    def setUp(self):
        for model in (User, Event, Hut, HutBooking, Section, Config, ApiToken):
            model.drop_collection()
        app.redis = mock.MagicMock()
        limiter.enabled = False
        app.config.update(WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=False)
        self.alpha = Section(slug='alpha', name='Alpha', hostnames=['alpha.example.org']).save()
        self.beta = Section(slug='beta', name='Beta', hostnames=['beta.example.org']).save()
        self.tour = self.event('Alphatour', self.alpha)
        self.member = self.user('member@example.org', (self.alpha, {'role': 'member'}))
        self.attendant = self.user('begleiter@example.org', (self.alpha, {'role': 'attendant'}))
        self.trainer = self.user('trainer@example.org', (self.alpha, {'role': 'guide'}))
        self.hut = Hut(name='Alphahuette', section=self.alpha,
                       rooms=[HutRoom(name='Lager', places=5)]).save()
        mails = mock.patch('application.events.views.send_email')
        mails.start()
        self.addCleanup(mails.stop)

    def token(self, user, host=A):
        """Schluessel ueber den echten Browser-Weg holen"""
        client = self.login(user, host)
        response = client.post('/app/authorize', base_url=host,
                               data={'state': 'abc', 'name': 'iPhone', 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)
        target = urlparse(response.headers['Location'])
        self.assertEqual((target.scheme, target.netloc), ('tourentool', 'auth'))
        query = parse_qs(target.query)
        self.assertEqual(query['state'], ['abc'])
        return query['token'][0]

    @staticmethod
    def api(method, path, token=None, host=A, client=None, **kwargs):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        response = getattr(client or app.test_client(), method)(
            f'/api/v1{path}', base_url=host, headers=headers, **kwargs)
        return response.status_code, response.get_json()

    # Anmeldung

    def test_login_returns_to_authorize_page(self):
        client = app.test_client()
        response = client.get('/app/authorize?state=xyz', base_url=A)
        self.assertEqual(response.status_code, 302)
        login_url = response.headers['Location']
        self.assertIn('/login', login_url)
        response = client.post(login_url, base_url=A, data={
            'login_email': self.member.email, 'password': PASSWORD, 'login_submit': 'Anmelden'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/app/authorize?state=xyz', response.headers['Location'])

    def test_login_ignores_foreign_next(self):
        response = app.test_client().post('/login?next=//evil.example.com/', base_url=A, data={
            'login_email': self.member.email, 'password': PASSWORD, 'login_submit': 'Anmelden'})
        self.assertNotIn('evil', response.headers['Location'])

    def test_wrong_password_gives_no_token(self):
        client = self.login(self.member, A)
        response = client.post('/app/authorize', base_url=A,
                               data={'state': 'abc', 'password': 'falsch'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ApiToken.objects.count(), 0)

    def test_only_hash_is_stored(self):
        raw = self.token(self.member)
        self.assertEqual(ApiToken.objects.count(), 1)
        self.assertNotEqual(ApiToken.objects.first().token_hash, raw)

    def test_requires_token(self):
        self.assertEqual(self.api('get', '/me')[0], 401)
        self.assertEqual(self.api('get', '/me', token='unsinn')[0], 401)

    def test_session_cookie_is_not_enough(self):
        """Ein offener Browser darf ueber die API nichts ausloesen (kein CSRF-Schutz dort)"""
        client = self.login(self.member, A)
        status, _ = self.api('post', f'/events/{self.tour.id}/bookings', client=client,
                             json={'tickets': {'Teilnahme': 1}})
        self.assertEqual(status, 401)
        self.assertEqual(Event.objects.get(id=self.tour.id).participations, [])

    def test_token_only_for_its_section(self):
        self.member.memberships.append(Membership(section=self.beta, role='member'))
        self.member.save()
        raw = self.token(self.member)
        self.assertEqual(self.api('get', '/me', raw)[0], 200)
        self.assertEqual(self.api('get', '/me', raw, host=B)[0], 401)

    def test_new_password_ends_token(self):
        raw = self.token(self.member)
        self.member.set_password(NEW_PASSWORD)
        self.member.save()
        self.assertEqual(self.api('get', '/me', raw)[0], 401)

    def test_revoke_in_profile(self):
        raw = self.token(self.member)
        client = self.login(self.member, A)
        page = client.get('/user/profile', base_url=A).get_data(as_text=True)
        self.assertIn('iPhone', page)
        client.post(f'/user/apps/{ApiToken.objects.first().id}/revoke', base_url=A)
        self.assertEqual(self.api('get', '/me', raw)[0], 401)

    def test_expired_token(self):
        raw = self.token(self.member)
        ApiToken.objects.update(set__expires=datetime.now() - timedelta(minutes=1))
        self.assertEqual(self.api('get', '/me', raw)[0], 401)

    # Touren und Buchen

    def test_me_and_rights(self):
        _, data = self.api('get', '/me', self.token(self.member))
        self.assertEqual(data['section']['name'], 'Alpha')
        self.assertFalse(data['rights']['create_events'])
        _, data = self.api('get', '/me', self.token(self.attendant))
        self.assertTrue(data['rights']['create_events'])
        self.assertFalse(data['rights']['manage_all_events'])

    def test_list_and_detail(self):
        raw = self.token(self.member)
        _, data = self.api('get', '/events', raw)
        self.assertEqual([x['name'] for x in data['events']], ['Alphatour'])
        self.assertEqual(data['events'][0]['free_places'], 5)
        status, data = self.api('get', f'/events/{self.tour.id}', raw)
        self.assertEqual(status, 200)
        self.assertEqual(data['select_tickets'], ['Teilnahme'])
        self.assertNotIn('edit', data)
        self.assertEqual(self.api('get', '/events/kaputt', raw)[0], 404)

    def test_booking(self):
        raw = self.token(self.member)
        status, data = self.api('post', f'/events/{self.tour.id}/bookings', raw, json={
            'tickets': {'Teilnahme': 2},
            'persons': [{'ticket_index': 0, 'slot': 1, 'name': 'Erika Muster'}],
            'comment': 'Mit Hund'})
        self.assertEqual(status, 201, data)
        self.assertTrue(data['booking_ref'])
        names = sorted(x['name'] for x in data['event']['my_tickets'])
        self.assertEqual(names, ['Erika Muster', 'Vor Nach'])
        self.assertTrue(all(x['state'] == 'unconfirmed' for x in data['event']['my_tickets']))
        self.assertIn(self.tour.id, [x.id for x in User.objects.get(id=self.member.id).event_registrations])
        _, data = self.api('get', '/bookings', raw)
        self.assertEqual(len(data['bookings'][0]['tickets']), 2)
        _, data = self.api('get', '/events?scope=mine', raw)
        self.assertEqual(len(data['events']), 1)

    def test_booking_rules_apply(self):
        raw = self.token(self.member)
        status, data = self.api('post', f'/events/{self.tour.id}/bookings', raw,
                                json={'tickets': {'Teilnahme': 3}})
        self.assertEqual(status, 400)
        self.assertIn('Ungültige Anzahl', data['error'])
        status, data = self.api('post', f'/events/{self.tour.id}/bookings', raw,
                                json={'tickets': {'Teilnahme': 2}})
        self.assertEqual(status, 400)
        self.assertIn('Namen', data['error'])
        Event.objects(id=self.tour.id).update(set__booking_until=datetime.now() - timedelta(hours=1))
        status, data = self.api('post', f'/events/{self.tour.id}/bookings', raw,
                                json={'tickets': {'Teilnahme': 1}})
        self.assertEqual(status, 400)
        self.assertIn('nicht freigeschaltet', data['error'])

    def test_full_tour_goes_to_waitlist(self):
        Event.objects(id=self.tour.id).update(set__places=1)
        raw = self.token(self.member)
        _, data = self.api('post', f'/events/{self.tour.id}/bookings', raw, json={
            'tickets': {'Teilnahme': 2}, 'persons': [{'ticket_index': 0, 'slot': 1, 'name': 'B'}]})
        self.assertTrue(all(x['state'] == 'waitlist' for x in data['event']['my_tickets']))

    def test_questions_required(self):
        Event.objects(id=self.tour.id).update(set__custom_fields=[{'field_name': 'Kletterniveau'}])
        raw = self.token(self.member)
        status, data = self.api('post', f'/events/{self.tour.id}/bookings', raw,
                                json={'tickets': {'Teilnahme': 1}})
        self.assertEqual(status, 400)
        self.assertIn('Kletterniveau', data['error'])
        status, _ = self.api('post', f'/events/{self.tour.id}/bookings', raw,
                             json={'tickets': {'Teilnahme': 1}, 'answers': ['5c']})
        self.assertEqual(status, 201)
        parti = Event.objects.get(id=self.tour.id).participations[0]
        self.assertEqual(parti.custom_fields[0].value, '5c')

    def test_favorite(self):
        raw = self.token(self.member)
        _, data = self.api('post', f'/events/{self.tour.id}/favorite', raw)
        self.assertTrue(data['favorite'])

    # Anlegen und Bearbeiten

    def new_tour(self, **extra):
        start = (datetime.now() + timedelta(days=10)).replace(microsecond=0)
        return {'name': 'Neue Tour', 'teaser': 'Kurz', 'type': 'guided', 'start': start.isoformat(),
                'places': 8, 'tickets': [{'name': 'Teilnahme', 'price': 12.5}], **extra}

    def test_member_cannot_create(self):
        status, _ = self.api('post', '/events', self.token(self.member), json=self.new_tour())
        self.assertEqual(status, 403)
        self.assertEqual(self.api('get', '/form', self.token(self.member))[0], 403)

    def test_attendant_creates_and_owns(self):
        raw = self.token(self.attendant)
        status, data = self.api('post', '/events', raw, json=self.new_tour(questions=['Erfahrung?']))
        self.assertEqual(status, 201, data)
        tour = Event.objects.get(id=data['id'])
        self.assertEqual(tour.section.id, self.alpha.id)
        self.assertEqual([x.id for x in tour.event_owners], [self.attendant.id])
        self.assertEqual(tour.tickets[0].maximum_tickets, 8)
        self.assertEqual(tour.tickets[0].price, 12.5)
        self.assertEqual(tour.end_date, tour.start_date)
        self.assertEqual(tour.custom_fields[0].field_name, 'Erfahrung?')
        self.assertEqual(data['edit']['name'], 'Neue Tour')

    def test_create_validation(self):
        raw = self.token(self.trainer)
        for payload, text in ((self.new_tour(name=''), 'Pflicht'),
                              (self.new_tour(type='quatsch'), 'Tourentyp'),
                              (self.new_tour(start=None), 'Beginn'),
                              (self.new_tour(category='gibtsnicht'), 'Kategorie'),
                              (self.new_tour(places='viele'), 'ganze Zahlen'),
                              (self.new_tour(tickets=[{'name': 'Bus', 'is_extra_ticket': True}]),
                               'Zusatzticket')):
            status, data = self.api('post', '/events', raw, json=payload)
            self.assertEqual(status, 400, payload)
            self.assertIn(text, data['error'])

    def test_attendant_edits_only_own(self):
        raw = self.token(self.attendant)
        status, _ = self.api('put', f'/events/{self.tour.id}', raw, json={'name': 'Geklaut'})
        self.assertEqual(status, 403)
        Event.objects(id=self.tour.id).update(push__event_owners=self.attendant)
        status, data = self.api('put', f'/events/{self.tour.id}', raw, json={'name': 'Umbenannt'})
        self.assertEqual(status, 200, data)
        tour = Event.objects.get(id=self.tour.id)
        self.assertEqual(tour.event_name, 'Umbenannt')
        # Nicht gesendete Felder bleiben
        self.assertEqual(tour.event_teaser, 't')
        self.assertEqual(tour.tickets[0].name, 'Teilnahme')

    def test_mine_means_booked_or_responsible(self):
        """Trainer verwalten alle Touren, unter "Meine" stehen trotzdem nur eigene"""
        trainer = self.token(self.trainer)
        _, data = self.api('get', '/events?scope=mine', trainer)
        self.assertEqual(data['events'], [])
        _, data = self.api('get', '/events', trainer)
        self.assertTrue(data['events'][0]['can_manage'])
        self.assertFalse(data['events'][0]['owner'])
        Event.objects(id=self.tour.id).update(push__event_owners=self.trainer)
        _, data = self.api('get', '/events?scope=mine', trainer)
        self.assertTrue(data['events'][0]['owner'])

    def test_trainer_edits_all(self):
        status, _ = self.api('put', f'/events/{self.tour.id}', self.token(self.trainer),
                             json={'places': 9})
        self.assertEqual(status, 200)
        self.assertEqual(Event.objects.get(id=self.tour.id).places, 9)

    # Teilnehmerliste

    def test_participants(self):
        member = self.token(self.member)
        self.api('post', f'/events/{self.tour.id}/bookings', member, json={'tickets': {'Teilnahme': 1}})
        # Teilnehmer mit Vereinsrolle sehen die Liste, Kontaktdaten nur mit Freigabe
        status, data = self.api('get', f'/events/{self.tour.id}/participants', member)
        self.assertEqual(status, 200)
        self.assertFalse(data['can_manage'])
        row = data['groups']['unconfirmed'][0]
        self.assertIsNone(row['phone'])
        self.assertNotIn('guide_comment', row)

        trainer = self.token(self.trainer)
        _, data = self.api('get', f'/events/{self.tour.id}/participants', trainer)
        ticket_id = data['groups']['unconfirmed'][0]['id']
        self.assertEqual(data['groups']['unconfirmed'][0]['phone'], '0170')
        with mock.patch('application.events.views.notify_confirmed') as notify:
            status, data = self.api('post', f'/events/{self.tour.id}/participants/{ticket_id}',
                                    trainer, json={'action': 'confirm'})
        self.assertEqual(status, 200)
        self.assertTrue(notify.called)
        self.assertEqual(len(data['groups']['confirmed']), 1)
        _, data = self.api('post', f'/events/{self.tour.id}/participants/{ticket_id}',
                           trainer, json={'action': 'paid'})
        self.assertTrue(data['groups']['confirmed'][0]['is_paid'])
        status, _ = self.api('post', f'/events/{self.tour.id}/participants/{ticket_id}',
                             member, json={'action': 'delete'})
        self.assertEqual(status, 403)
        _, data = self.api('post', f'/events/{self.tour.id}/participants/{ticket_id}',
                           trainer, json={'action': 'delete'})
        self.assertEqual(data['groups']['confirmed'], [])
        self.assertEqual(User.objects.get(id=self.member.id).event_registrations, [])

    def test_delete_account(self):
        member = self.token(self.member)
        self.api('post', f'/events/{self.tour.id}/bookings', member, json={'tickets': {'Teilnahme': 1}})
        status, _ = self.api('post', '/me/delete', member, json={'password': 'falsch'})
        self.assertEqual(status, 403)
        self.assertEqual(User.objects(id=self.member.id).count(), 1)
        status, data = self.api('post', '/me/delete', member, json={'password': PASSWORD})
        self.assertEqual((status, data), (200, {'deleted': True}))
        self.assertEqual(User.objects(id=self.member.id).count(), 0)
        self.assertEqual(ApiToken.objects(user=self.member.id).count(), 0)
        self.assertEqual(self.api('get', '/me', member)[0], 401)
        # Die Buchung bleibt mit Name und Kontakt in der Teilnehmerliste
        _, data = self.api('get', f'/events/{self.tour.id}/participants', self.token(self.trainer))
        row = data['groups']['unconfirmed'][0]
        self.assertEqual(row['booker'], 'Vor Nach (Konto gelöscht)')
        self.assertEqual((row['email'], row['phone']), ('member@example.org', '0170'))

    def test_participant_extras_point_to_their_person(self):
        Event.objects(id=self.tour.id).update(push__tickets=Ticket(name='Bus', price=5.0, maximum_tickets=0,
                                                                    is_extra_ticket=True))
        member = self.token(self.member)
        self.api('post', f'/events/{self.tour.id}/bookings', member,
                 json={'tickets': {'Teilnahme': 1}, 'persons': [{'ticket_index': 0, 'slot': 0, 'extras': [1]}]})
        _, data = self.api('get', f'/events/{self.tour.id}/participants', self.token(self.trainer))
        rows = data['groups']['unconfirmed']
        main = next(x for x in rows if not x['is_extra'])
        extra = next(x for x in rows if x['is_extra'])
        self.assertEqual(extra['main_ticket_id'], main['id'])

    def test_stranger_sees_no_participants(self):
        status, _ = self.api('get', f'/events/{self.tour.id}/participants', self.token(self.member))
        self.assertEqual(status, 403)

    # Huetten

    def test_hut_booking_and_cancel(self):
        raw = self.token(self.member)
        _, data = self.api('get', '/huts', raw)
        self.assertEqual([x['name'] for x in data['huts']], ['Alphahuette'])
        start = date.today() + timedelta(days=5)
        payload = {'from': start.isoformat(), 'to': (start + timedelta(days=2)).isoformat(), 'places': 2}
        status, booking = self.api('post', f'/huts/{self.hut.id}/bookings', raw, json=payload)
        self.assertEqual(status, 201, booking)
        self.assertTrue(booking['confirmed'])
        _, data = self.api('get', f"/huts/{self.hut.id}?from={payload['from']}&to={payload['to']}", raw)
        self.assertEqual(data['range']['free_places'], 3)
        self.assertNotIn('pending', data)
        status, data = self.api('post', f'/huts/{self.hut.id}/bookings', raw,
                                json={**payload, 'places': 4})
        self.assertEqual(status, 400)
        self.assertIn('Nicht genügend', data['error'])
        _, data = self.api('get', f'/huts/{self.hut.id}/calendar?year={start.year}&month={start.month}', raw)
        day = next(x for x in data['days'] if x['date'] == start.isoformat())
        self.assertEqual((day['used'], day['entries']), (2, []))
        _, data = self.api('get', '/huts/bookings', raw)
        self.assertEqual(data['bookings'][0]['hut'], 'Alphahuette')
        status, _ = self.api('post', f"/huts/{self.hut.id}/bookings/{booking['id']}/cancel", raw)
        self.assertEqual(status, 200)
        self.assertEqual(HutBooking.objects.count(), 0)

    def test_hut_approval(self):
        self.hut.update(set__requires_approval=True)
        start = date.today() + timedelta(days=5)
        _, booking = self.api('post', f'/huts/{self.hut.id}/bookings', self.token(self.member),
                              json={'from': start.isoformat(),
                                    'to': (start + timedelta(days=1)).isoformat(), 'places': 1})
        self.assertFalse(booking['confirmed'])
        other = self.user('other@example.org', (self.alpha, {'role': 'member'}))
        status, _ = self.api('post', f"/huts/{self.hut.id}/bookings/{booking['id']}/confirm",
                             self.token(other))
        self.assertEqual(status, 403)
        trainer = self.token(self.trainer)
        _, data = self.api('get', f'/huts/{self.hut.id}', trainer)
        self.assertEqual(len(data['pending']), 1)
        _, data = self.api('post', f"/huts/{self.hut.id}/bookings/{booking['id']}/confirm", trainer)
        self.assertTrue(data['confirmed'])


if __name__ == '__main__':
    unittest.main()
