"""
Partnersektionen: Alpha und Beta teilen ihre Touren, wenn beide einander
eintragen. Mitglieder buchen geteilte Touren auf der eigenen Seite, verwaltet
wird nur in der Sektion der Tour.
Ausfuehren: .venv/bin/python -m unittest tests.test_partner_sections (mongomock)
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

import mongoengine
import mongomock
from mongomock.gridfs import enable_gridfs_integration

from application import app, limiter
from application.events.models import Event
from application.huts.models import Hut, HutBooking
from application.models.config import Config
from application.models.section import Section
from application.models.user import User
from tests import test_section_isolation as isolation
from tests.test_section_isolation import A, B


def setUpModule():
    enable_gridfs_integration()
    mongoengine.disconnect()
    mongoengine.connect('test', mongo_client_class=mongomock.MongoClient)


def tearDownModule():
    mongoengine.disconnect()


class PartnerTest(unittest.TestCase):
    """Alphatour und Betatour, Beta teilt mit Alpha erst, wenn beide eingetragen sind"""

    event = staticmethod(isolation.IsolationTest.event)
    user = staticmethod(isolation.IsolationTest.user)
    login = staticmethod(isolation.IsolationTest.login)

    def setUp(self):
        for model in (User, Event, Hut, HutBooking, Section, Config):
            model.drop_collection()
        app.redis = mock.MagicMock()
        limiter.enabled = False
        old = {key: app.config.get(key) for key in ('SUBSCRIPTIONS_ENABLED',)}
        self.addCleanup(app.config.update, old)
        app.config.update(WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=False)
        self.alpha = Section(slug='alpha', name='Alpha', hostnames=['alpha.example.org']).save()
        self.beta = Section(slug='beta', name='Beta', hostnames=['beta.example.org']).save()
        self.event_a = self.event('Alphatour', self.alpha)
        self.event_b = self.event('Betatour', self.beta)

    def partner(self):
        self.alpha.update(set__partner_sections=[self.beta])
        self.beta.update(set__partner_sections=[self.alpha])

    @staticmethod
    def page(client, path, host=A):
        response = client.get(path, base_url=host)
        return response.status_code, response.get_data(as_text=True)

    def test_one_side_is_not_enough(self):
        self.beta.update(set__partner_sections=[self.alpha])
        _, page = self.page(app.test_client(), '/')
        self.assertNotIn('Betatour', page)
        status, _ = self.page(app.test_client(), f'/event/details?event_id={self.event_b.id}')
        self.assertEqual(status, 404)

    def test_partners_see_each_others_tours_with_hint(self):
        self.partner()
        _, page = self.page(app.test_client(), '/')
        self.assertIn('Alphatour', page)
        self.assertIn('Betatour', page)
        self.assertIn('Tour von Beta', page)
        status, page = self.page(app.test_client(), f'/event/details?event_id={self.event_b.id}')
        self.assertEqual(status, 200)
        self.assertIn('Diese Tour gehört zu Beta', page)
        _, page = self.page(app.test_client(), '/', B)
        self.assertIn('Tour von Alpha', page)

    def test_members_only_tour_stays_home(self):
        self.partner()
        Event.all_objects(id=self.event_b.id).update_one(set__members_only=True)
        _, page = self.page(app.test_client(), '/')
        self.assertNotIn('Betatour', page)
        status, _ = self.page(app.test_client(), f'/event/details?event_id={self.event_b.id}')
        self.assertEqual(status, 404)
        _, page = self.page(app.test_client(), '/', B)
        self.assertIn('Betatour', page)

    def test_booking_uses_consent_of_tour_section(self):
        self.partner()
        self.alpha.update(set__media_consent_text='<p>Einwilligung Alpha</p>')
        self.beta.update(set__media_consent_text='<p>Einwilligung Beta</p>')
        member = self.user('member@example.org', (self.alpha, {'role': 'member'}), media_optin=True)
        client = self.login(member, A)
        _, page = self.page(client, f'/event/details?event_id={self.event_b.id}')
        self.assertIn('Einwilligung Beta', page)
        self.assertNotIn('Einwilligung Alpha', page)
        _, page = self.page(client, f'/event/details?event_id={self.event_a.id}')
        self.assertIn('Einwilligung Alpha', page)
        # Ohne Haken gilt fuer die Tour Nein, auch wenn das Konto sonst zugestimmt hat
        client.post(f'/event/details?event_id={self.event_b.id}', base_url=A, data={
            'comment': '', 'ticket_Teilnahme': '1', 'submit': 'Anmelden'})
        self.assertIs(Event.all_objects.get(id=self.event_b.id).participations[0].media_optin, False)
        other = self.user('other@example.org', (self.alpha, {'role': 'member'}))
        self.login(other, A).post(f'/event/details?event_id={self.event_b.id}', base_url=A, data={
            'comment': '', 'ticket_Teilnahme': '1', 'media_optin_event': 'y', 'submit': 'Anmelden'})
        self.assertIs(Event.all_objects.get(id=self.event_b.id).participations[1].media_optin, True)

    def test_guest_shows_home_section_in_member_list(self):
        self.partner()
        member = self.user('member@example.org', (self.alpha, {'role': 'member'}))
        self.login(member, B)
        admin = self.user('admin-b@example.org', (self.beta, {'admin': True, 'role': 'guide'}))
        status, page = self.page(self.login(admin, B), '/admin/Members/', B)
        self.assertEqual(status, 200)
        self.assertIn('Gast aus', page)
        self.assertIn('member@example.org', page)
        self.assertIn('Alpha', page)

    def test_inactive_partner_is_hidden(self):
        self.partner()
        app.config['SUBSCRIPTIONS_ENABLED'] = True
        self.beta.update(set__trial_until=datetime.now() - timedelta(days=1))
        _, page = self.page(app.test_client(), '/')
        self.assertNotIn('Betatour', page)

    def test_book_at_home_manage_only_at_owner(self):
        self.partner()
        member = self.user('member@example.org', (self.alpha, {'role': 'member'}))
        client = self.login(member, A)
        response = client.post(f'/event/details?event_id={self.event_b.id}', base_url=A, data={
            'comment': 'Hallo', 'ticket_Teilnahme': '1', 'submit': 'Anmelden'})
        self.assertEqual(response.status_code, 302)
        event = Event.all_objects.get(id=self.event_b.id)
        self.assertEqual(len(event.participations), 1)
        self.assertTrue(User.objects.get(id=member.id).participate_event(event.id))
        status, page = self.page(client, f'/user/booking?event_id={event.id}')
        self.assertEqual(status, 200)
        self.assertIn('Betatour', page)
        _, page = self.page(client, '/?filter=my_events')
        self.assertIn('Betatour', page)
        # Teilnehmerliste und Verwaltung gibt es bei Alpha nicht
        self.assertNotIn(f'/event/participants?event_id={event.id}', page)
        status, _ = self.page(client, f'/event/participants?event_id={event.id}')
        self.assertEqual(status, 404)

        alpha_guide = self.user('guide-a@example.org', (self.alpha, {'role': 'guide', 'admin': True}))
        client = self.login(alpha_guide, A)
        _, page = self.page(client, f'/event/details?event_id={event.id}')
        self.assertNotIn(f'/event/admin?event_id={event.id}', page)
        status, _ = self.page(client, f'/event/admin?event_id={event.id}')
        self.assertEqual(status, 403)

        beta_guide = self.user('guide-b@example.org', (self.beta, {'role': 'guide'}))
        status, page = self.page(self.login(beta_guide, B), f'/event/participants?event_id={event.id}', B)
        self.assertEqual(status, 200)
        self.assertIn('Mitglied anderer Sektion', page)


if __name__ == '__main__':
    unittest.main()
