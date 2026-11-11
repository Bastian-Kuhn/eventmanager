"""
Zwei Sektionen in einer Installation: Nutzer, Trainer und Admins der einen
sehen und aendern nichts aus der anderen. Rolle und Vereinsadmin gelten je
Sektion.
Ausfuehren: .venv/bin/python -m unittest tests.test_section_isolation (mongomock)
"""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import mongoengine
import mongomock
from mongomock.gridfs import enable_gridfs_integration

from application import app, limiter
from application.events.models import Event, EventParticipation, OwnedTicket, Ticket
from application.huts.models import Hut, HutBooking, HutRoom
from application.models.config import Config
from application.models.section import Section
from application.models.user import Membership, User
from tests.test_security import PASSWORD

A = 'https://alpha.example.org'
B = 'https://beta.example.org'


def setUpModule():
    enable_gridfs_integration()
    mongoengine.disconnect()
    mongoengine.connect('test', mongo_client_class=mongomock.MongoClient)


def tearDownModule():
    mongoengine.disconnect()


class IsolationTest(unittest.TestCase):
    """Sektion Alpha und Beta mit je einer Tour, Huette und Buchung"""

    def setUp(self):
        for model in (User, Event, Hut, HutBooking, Section, Config):
            model.drop_collection()
        app.redis = mock.MagicMock()
        limiter.enabled = False
        app.config.update(WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=False)
        self.alpha = Section(slug='alpha', name='Alpha', hostnames=['alpha.example.org']).save()
        self.beta = Section(slug='beta', name='Beta', hostnames=['beta.example.org']).save()
        self.event_a = self.event('Alphatour', self.alpha)
        self.event_b = self.event('Betatour', self.beta)
        self.hut_a = Hut(name='Alphahuette', section=self.alpha, rooms=[HutRoom(name='Lager', places=5)]).save()
        self.hut_b = Hut(name='Betahuette', section=self.beta, rooms=[HutRoom(name='Lager', places=5)]).save()
        self.booking_b = HutBooking(hut=self.hut_b, section=self.beta, name='Betagruppe', places=2,
                                    from_date=date.today() + timedelta(days=3),
                                    to_date=date.today() + timedelta(days=4)).save()

    @staticmethod
    def event(name, section):
        now = datetime.now()
        return Event(event_name=name, event_teaser='t', event_type='guided', places=5,
                     start_date=now + timedelta(days=5), end_date=now + timedelta(days=6),
                     booking_from=now - timedelta(days=1), booking_until=now + timedelta(days=4),
                     tickets=[Ticket(name='Teilnahme', price=10.0, maximum_tickets=2)],
                     section=section).save()

    @staticmethod
    def user(email, *memberships, **kwargs):
        user = User(email=email, first_name='Vor', last_name='Nach', phone='0170',
                    birthdate=date(1990, 1, 1), **kwargs)
        user.memberships = [Membership(section=section, **values) for section, values in memberships]
        user.set_password(PASSWORD)
        return user.save()

    @staticmethod
    def login(user, host):
        client = app.test_client()
        response = client.post('/login', base_url=host, data={
            'login_email': user.email, 'password': PASSWORD, 'login_submit': 'Anmelden'})
        assert response.status_code == 302, response.status_code
        return client

    def text(self, client, path, host=A):
        response = client.get(path, base_url=host)
        return response.status_code, response.get_data(as_text=True)

    # Oeffentliche Seiten

    def test_event_list_shows_only_own_section(self):
        _, page = self.text(app.test_client(), '/')
        self.assertIn('Alphatour', page)
        self.assertNotIn('Betatour', page)

    def test_foreign_event_details_are_404(self):
        status, _ = self.text(app.test_client(), f'/event/details?event_id={self.event_b.id}')
        self.assertEqual(status, 404)
        status, _ = self.text(app.test_client(), f'/event/details?event_id={self.event_b.id}', B)
        self.assertEqual(status, 200)

    def test_huts_only_own_section(self):
        status, page = self.text(app.test_client(), '/huetten')
        # Nur eine Huette in der Sektion: direkt zu ihr
        self.assertEqual(status, 302)
        status, _ = self.text(app.test_client(), f'/huetten/{self.hut_b.id}')
        self.assertEqual(status, 404)
        status, _ = self.text(app.test_client(), f'/huetten/{self.hut_b.id}/kalender')
        self.assertEqual(status, 404)

    # Rollen je Sektion

    def test_role_counts_only_in_its_section(self):
        guide = self.user('guide@example.org', (self.alpha, {'role': 'guide'}))
        client_a = self.login(guide, A)
        status, _ = self.text(client_a, f'/event/participants?event_id={self.event_a.id}')
        self.assertEqual(status, 200)
        status, _ = self.text(client_a, f'/event/participants?event_id={self.event_b.id}')
        self.assertIn(status, (403, 404))
        # Als Gast einer Partnersektion hat er dort keine Rolle
        self.alpha.update(set__partner_sections=[self.beta])
        self.beta.update(set__partner_sections=[self.alpha])
        client_b = self.login(guide, B)
        status, _ = self.text(client_b, f'/event/participants?event_id={self.event_b.id}', B)
        self.assertEqual(status, 403)
        status, _ = self.text(client_b, '/admin/event/', B)
        self.assertNotEqual(status, 200)

    def test_login_joins_only_partner_sections_as_guest(self):
        guide = self.user('guide@example.org', (self.alpha, {'role': 'guide'}))
        self.alpha.update(set__partner_sections=[self.beta])
        self.beta.update(set__partner_sections=[self.alpha])
        self.login(guide, B)
        self.login(guide, B)
        guide.reload()
        self.assertEqual(len(guide.memberships), 2)
        self.assertTrue(guide.membership(self.beta).guest)
        self.assertFalse(guide.membership(self.alpha).guest)
        self.assertEqual(guide.role_in(self.alpha), 'guide')
        self.assertIsNone(guide.role_in(self.beta))
        with app.test_request_context(base_url=B):
            self.assertEqual(guide.guest_from(), ['Alpha'])
        # Globale Admins werden nirgends Mitglied, Konten ohne jede Mitgliedschaft normal
        root = self.user('root@example.org', global_admin=True)
        self.login(root, B)
        self.assertEqual(User.objects.get(id=root.id).memberships, [])
        fresh = self.user('fresh@example.org')
        self.login(fresh, B)
        fresh.reload()
        self.assertFalse(fresh.membership(self.beta).guest)

    def try_login(self, user, host):
        """Login-Versuch, gibt (Status, Seite) zurueck"""
        response = app.test_client().post('/login', base_url=host, data={
            'login_email': user.email, 'password': PASSWORD, 'login_submit': 'Anmelden'})
        return response.status_code, response.get_data(as_text=True)

    def test_foreign_login_becomes_join_request(self):
        guide = self.user('guide@example.org', (self.alpha, {'role': 'guide'}))
        admin_b = self.user('admin-b@example.org', (self.beta, {'admin': True}))
        self.user('admin-a@example.org', (self.alpha, {'admin': True}))
        with mock.patch('application.auth.views.send_email') as send:
            status, page = self.try_login(guide, B)
            self.assertEqual(status, 200)
            self.assertIn('gefragt, ob sie dich aufnehmen', page)
            status, page = self.try_login(guide, B)
            self.assertIn('noch offen', page)
        # Nur die Admins von Beta, nur beim ersten Versuch
        self.assertEqual([call.args[0] for call in send.call_args_list], ['admin-b@example.org'])
        guide.reload()
        self.assertEqual(len(guide.memberships), 1)
        self.assertEqual([r.section.pk for r in guide.join_requests], [self.beta.pk])
        # Admins von Alpha sehen die Anfrage nicht, die von Beta nehmen auf
        status, page = self.text(self.login(User.objects.get(email='admin-a@example.org'), A),
                                 '/admin/JoinRequests/')
        self.assertNotIn('guide@example.org', page)
        client = self.login(admin_b, B)
        status, page = self.text(client, '/admin/JoinRequests/', B)
        self.assertEqual(status, 200)
        self.assertIn('guide@example.org', page)
        self.assertIn('aus Alpha', page)
        response = client.post('/admin/JoinRequests/antwort', base_url=B,
                               data={'user_id': str(guide.id), 'action': 'accept'})
        self.assertEqual(response.status_code, 302)
        guide.reload()
        self.assertEqual(guide.join_requests, [])
        self.assertTrue(guide.membership(self.beta).guest)
        self.login(guide, B)

    def test_rejected_join_request(self):
        guide = self.user('guide@example.org', (self.alpha, {'role': 'guide'}))
        admin_b = self.user('admin-b@example.org', (self.beta, {'admin': True}))
        self.try_login(guide, B)
        client = self.login(admin_b, B)
        client.post('/admin/JoinRequests/antwort', base_url=B,
                    data={'user_id': str(guide.id), 'action': 'reject'})
        guide.reload()
        self.assertEqual(guide.join_requests, [])
        self.assertIsNone(guide.membership(self.beta))
        status, _ = self.try_login(guide, B)
        self.assertEqual(status, 200)

    def test_approval_needed_for_new_accounts_and_partners(self):
        self.beta.update(set__require_approval=True)
        self.alpha.update(set__partner_sections=[self.beta])
        self.beta.update(set__partner_sections=[self.alpha])
        self.beta.reload()
        member = self.user('member@example.org', (self.beta, {}))
        self.login(member, B)
        guide = self.user('guide@example.org', (self.alpha, {'role': 'guide'}))
        fresh = self.user('fresh@example.org')
        admin_b = self.user('admin-b@example.org', (self.beta, {'admin': True}))
        for user in (guide, fresh):
            self.assertEqual(self.try_login(user, B)[0], 200)
            user.reload()
            self.assertIsNone(user.membership(self.beta))
            self.assertIsNotNone(user.join_request(self.beta))
        client = self.login(admin_b, B)
        for user in (guide, fresh):
            client.post('/admin/JoinRequests/antwort', base_url=B,
                        data={'user_id': str(user.id), 'action': 'accept'})
            self.login(user, B)
        self.assertTrue(User.objects.get(id=guide.id).membership(self.beta).guest)
        self.assertFalse(User.objects.get(id=fresh.id).membership(self.beta).guest)

    def test_registration_with_approval(self):
        self.beta.update(set__require_approval=True)
        admin_b = self.user('admin-b@example.org', (self.beta, {'admin': True}))
        with mock.patch('application.user.views.send_email'):
            response = app.test_client().post('/user/create', base_url=B, data={
                'first_name': 'Neu', 'last_name': 'Person', 'email': 'neu@example.org',
                'phone': '0170', 'birthdate': '1990-01-01', 'club_id': '4711',
                'password': PASSWORD, 'password_repeat': PASSWORD, 'submit': 'Registrieren'})
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='neu@example.org')
        self.assertEqual(user.memberships, [])
        self.assertEqual(user.join_request(self.beta).club_id, '4711')
        # Unbestaetigt: noch nicht in der Liste, Mail an die Admins erst beim Login
        client = self.login(admin_b, B)
        self.assertNotIn('neu@example.org', self.text(client, '/admin/JoinRequests/', B)[1])
        user.update(set__email_unconfirmed=False)
        with mock.patch('application.auth.views.send_email') as send:
            self.try_login(user, B)
            self.try_login(user, B)
        self.assertEqual(send.call_count, 1)
        page = self.text(client, '/admin/JoinRequests/', B)[1]
        self.assertIn('neu@example.org', page)
        self.assertIn('4711', page)
        client.post('/admin/JoinRequests/antwort', base_url=B, data={'user_id': str(user.id), 'action': 'accept'})
        user.reload()
        self.assertEqual(user.membership(self.beta).club_id, '4711')
        self.assertFalse(user.membership(self.beta).guest)
        self.login(user, B)

    def test_session_without_membership_is_logged_out(self):
        guide = self.user('guide@example.org', (self.alpha, {'role': 'guide'}))
        self.alpha.update(set__partner_sections=[self.beta])
        self.beta.update(set__partner_sections=[self.alpha])
        client = self.login(guide, B)
        self.assertEqual(self.text(client, '/user/profile', B)[0], 200)
        guide.reload()
        guide.leave_section(self.beta)
        self.beta.update(set__partner_sections=[])
        response = client.get('/user/profile', base_url=B)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_foreign_bookings_cannot_be_changed(self):
        guide = self.user('guide@example.org', (self.alpha, {'role': 'guide'}))
        member = self.user('m@example.org', (self.beta, {}))
        self.event_b.participations.append(EventParticipation(
            user=member, booking_date=datetime.now(),
            tickets=[OwnedTicket(ticket_id='t1', ticket_name='Teilnahme', name_on_ticket='M')]))
        self.event_b.save()
        client = self.login(guide, A)
        response = client.post('/event/change_paidstatus', base_url=A, data={
            'event_id': str(self.event_b.id), 'ticket_id': 't1', 'job': 'paid'})
        self.assertIn(response.status_code, (403, 404))
        self.event_b.reload()
        self.assertFalse(self.event_b.participations[0].tickets[0].is_paid)

    # Admin

    def test_admin_lists_only_own_section(self):
        admin = self.user('admin@example.org', (self.alpha, {'admin': True, 'role': 'guide'}))
        self.user('nur-beta@example.org', (self.beta, {}))
        client = self.login(admin, A)
        for path, own, foreign in (('/admin/event/', 'Alphatour', 'Betatour'),
                                   ('/admin/hut/', 'Alphahuette', 'Betahuette'),
                                   ('/admin/hutbooking/', '', 'Betagruppe'),
                                   ('/admin/Members/', 'admin@example.org', 'nur-beta@example.org')):
            status, page = self.text(client, path)
            self.assertEqual(status, 200, path)
            self.assertIn(own, page, path)
            self.assertNotIn(foreign, page, path)

    def test_admin_cannot_open_foreign_records(self):
        admin = self.user('admin@example.org', (self.alpha, {'admin': True, 'role': 'guide'}))
        client = self.login(admin, A)
        for path in (f'/admin/event/edit/?id={self.event_b.id}',
                     f'/admin/hut/edit/?id={self.hut_b.id}',
                     f'/admin/hutbooking/edit/?id={self.booking_b.id}'):
            status, page = self.text(client, path)
            self.assertEqual(status, 302, path)
        client.post('/admin/event/delete/', base_url=A, data={'id': str(self.event_b.id)})
        self.assertEqual(Event.all_objects(id=self.event_b.id).count(), 1)

    def test_admin_of_one_section_is_not_admin_of_the_other(self):
        admin = self.user('admin@example.org', (self.alpha, {'admin': True}), (self.beta, {}))
        client = self.login(admin, B)
        status, _ = self.text(client, '/admin/Members/', B)
        self.assertNotEqual(status, 200)

    def test_choices_only_from_own_section(self):
        admin = self.user('admin@example.org', (self.alpha, {'admin': True, 'role': 'guide'}))
        beta = self.user('nur-beta@example.org', (self.beta, {'role': 'guide'}))
        User.objects(id=beta.id).update_one(set__first_name='Nurbeta')
        client = self.login(admin, A)
        for path in ('/admin/hutbooking/new/', '/admin/hut/new/'):
            status, page = self.text(client, path)
            self.assertEqual(status, 200, path)
            self.assertNotIn('Nurbeta', page, path)
            self.assertNotIn('Betahuette', page, path)
            self.assertNotIn('Betatour', page, path)

    def test_created_records_belong_to_section(self):
        admin = self.user('admin@example.org', (self.alpha, {'admin': True, 'role': 'guide'}))
        client = self.login(admin, A)
        client.post('/admin/hut/new/', base_url=A, data={'name': 'Neue Huette'})
        self.assertEqual(Hut.all_objects.get(name='Neue Huette').section.id, self.alpha.id)

    def test_member_role_is_set_per_section(self):
        admin = self.user('admin@example.org', (self.alpha, {'admin': True}))
        member = self.user('m@example.org', (self.alpha, {}), (self.beta, {'role': 'member'}))
        client = self.login(admin, A)
        response = client.post('/admin/Members/ajax/update/', base_url=A, data={
            'list_form_pk': str(member.id), 'role': 'guide'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        member.reload()
        self.assertEqual(member.role_in(self.alpha), 'guide')
        self.assertEqual(member.role_in(self.beta), 'member')

    def test_admin_cannot_change_account_shared_with_other_section(self):
        admin = self.user('admin@example.org', (self.alpha, {'admin': True}))
        member = self.user('m@example.org', (self.alpha, {}), (self.beta, {'admin': True}))
        client = self.login(admin, A)
        client.post(f'/admin/Members/edit/?id={member.id}', base_url=A, data={
            'email': 'attacker@example.org', 'first_name': 'Vor', 'last_name': 'Nach'})
        member.reload()
        self.assertEqual(member.email, 'm@example.org')

    def test_delete_of_shared_account_only_leaves_section(self):
        admin = self.user('admin@example.org', (self.alpha, {'admin': True}))
        member = self.user('m@example.org', (self.alpha, {}), (self.beta, {'role': 'member'}))
        only_alpha = self.user('a@example.org', (self.alpha, {}))
        client = self.login(admin, A)
        client.post('/admin/Members/delete/', base_url=A, data={'id': str(member.id)})
        client.post('/admin/Members/delete/', base_url=A, data={'id': str(only_alpha.id)})
        member.reload()
        self.assertIsNone(member.membership(self.alpha))
        self.assertEqual(member.role_in(self.beta), 'member')
        self.assertEqual(User.objects(email='a@example.org').count(), 0)


if __name__ == '__main__':
    unittest.main()
