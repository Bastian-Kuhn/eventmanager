"""
Websites mit dem WordPress-Plugin: Touren mit dem Website-Schluessel lesen,
Bucher per Ruecksprung oder SSO anmelden, Bucher-Schluessel duerfen nur buchen.
Ausfuehren: .venv/bin/python -m unittest tests.test_website (mongomock)
"""
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

import mongoengine
import mongomock
from mongomock.gridfs import enable_gridfs_integration

from application import app, limiter
from application.events.models import Event
from application.models.api_token import ApiToken
from application.models.config import Config
from application.models.section import Section
from application.models.user import User
from application.models.website import Website, WebsiteCode, WebsiteLink
from tests import test_section_isolation as isolation
from tests.test_section_isolation import A, B

RETURN_URL = 'https://www.example.org/wp-json/tourentool/v1/callback'


def setUpModule():
    enable_gridfs_integration()
    mongoengine.disconnect()
    mongoengine.connect('test', mongo_client_class=mongomock.MongoClient)


def tearDownModule():
    mongoengine.disconnect()


class WebsiteTest(unittest.TestCase):
    """Sektion Alpha mit Website, Mitglied, Trainer und Admin, Sektion Beta daneben"""

    event = staticmethod(isolation.IsolationTest.event)
    user = staticmethod(isolation.IsolationTest.user)
    login = staticmethod(isolation.IsolationTest.login)

    def setUp(self):
        for model in (User, Event, Section, Config, ApiToken, Website, WebsiteLink, WebsiteCode):
            model.drop_collection()
        app.redis = mock.MagicMock()
        limiter.enabled = False
        app.config.update(WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=False)
        self.alpha = Section(slug='alpha', name='Alpha', hostnames=['alpha.example.org']).save()
        self.beta = Section(slug='beta', name='Beta', hostnames=['beta.example.org']).save()
        self.tour = self.event('Alphatour', self.alpha)
        self.member = self.user('member@example.org', (self.alpha, {'role': 'member'}))
        self.trainer = self.user('trainer@example.org', (self.alpha, {'role': 'guide'}))
        self.admin = self.user('admin@example.org', (self.alpha, {'admin': True}))
        self.website, self.key = self.new_website('auto')
        mails = mock.patch('application.events.views.send_email')
        mails.start()
        self.addCleanup(mails.stop)

    def new_website(self, mode, section=None):
        website = Website(section=section or self.alpha, name='Vereinsseite', return_url=RETURN_URL, sso_mode=mode)
        key = website.new_key()
        return website.save(), key

    @staticmethod
    def call(method, path, token=None, host=A, **kwargs):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        response = getattr(app.test_client(), method)(path, base_url=host, headers=headers, **kwargs)
        return response.status_code, response.get_json()

    def site(self, method, path, key=None, **kwargs):
        return self.call(method, f'/api/site/v1{path}', key or self.key, **kwargs)

    def sso(self, email, wp_user='7', key=None):
        return self.site('post', '/sso', key, json={'wp_user': wp_user, 'email': email,
                                                     'first_name': 'Wp', 'last_name': 'Nutzer'})

    def code_token(self, user):
        """Bucher-Schluessel ueber den echten Ruecksprung holen"""
        client = self.login(user, A)
        path = f'/app/authorize?site={self.website.pk}&state=xyz'
        self.assertEqual(client.get(path, base_url=A).status_code, 200)
        response = client.post('/app/authorize', base_url=A,
                               data={'site': str(self.website.pk), 'state': 'xyz'})
        self.assertEqual(response.status_code, 302)
        target = urlparse(response.headers['Location'])
        self.assertEqual(f'{target.scheme}://{target.netloc}{target.path}', RETURN_URL)
        query = parse_qs(target.query)
        self.assertEqual(query['state'], ['xyz'])
        status, data = self.site('post', '/token', json={'code': query['code'][0]})
        self.assertEqual(status, 200)
        # Ein Code gilt nur einmal
        self.assertEqual(self.site('post', '/token', json={'code': query['code'][0]})[0], 400)
        return data['token']

    # Touren ohne Konto

    def test_events_without_account(self):
        status, data = self.site('get', '/events')
        self.assertEqual(status, 200)
        self.assertEqual([x['name'] for x in data['events']], ['Alphatour'])
        self.assertFalse(data['events'][0]['booked'])
        status, data = self.site('get', '/site')
        self.assertEqual((data['id'], data['sso'], data['section']['slug']), (str(self.website.pk), True, 'alpha'))
        status, detail = self.site('get', f'/events/{self.tour.pk}')
        self.assertEqual(status, 200)
        self.assertEqual(detail['my_tickets'], [])
        self.assertFalse(detail['can_manage'])

    def test_wrong_or_foreign_key(self):
        self.assertEqual(self.site('get', '/events', key='tts_falsch')[0], 401)
        _, beta_key = self.new_website('off', self.beta)
        self.assertEqual(self.site('get', '/events', key=beta_key)[0], 401)
        # Ein Bucher-Schluessel ist kein Website-Schluessel und umgekehrt
        self.assertEqual(self.call('get', '/api/v1/me', self.key)[0], 401)

    # Ruecksprung

    def test_login_by_return_and_booking(self):
        token = self.code_token(self.member)
        status, _ = self.call('post', f'/api/v1/events/{self.tour.pk}/bookings', token,
                              json={'tickets': {'Teilnahme': 1}})
        self.assertEqual(status, 201)
        status, data = self.call('get', '/api/v1/bookings', token)
        self.assertEqual(len(data['bookings']), 1)
        self.assertEqual(ApiToken.objects.get().name, 'Vereinsseite')

    def test_booker_token_cannot_manage(self):
        token = self.code_token(self.trainer)
        self.assertEqual(self.call('get', '/api/v1/form', token)[0], 403)
        self.assertEqual(self.call('get', f'/api/v1/events/{self.tour.pk}/participants', token)[0], 403)
        self.assertEqual(self.call('post', '/api/v1/me/delete', token, json={'password': 'x'})[0], 403)
        status, data = self.call('get', f'/api/v1/events/{self.tour.pk}', token)
        self.assertEqual(status, 200)
        self.assertFalse(data['can_manage'])
        self.assertNotIn('edit', data)
        self.assertFalse(self.call('get', '/api/v1/me', token)[1]['rights']['create_events'])

    def test_unknown_website_in_authorize(self):
        client = self.login(self.member, A)
        beta_site, _ = self.new_website('off', self.beta)
        self.assertEqual(client.get(f'/app/authorize?site={beta_site.pk}&state=x', base_url=A).status_code, 404)

    def test_removed_website_ends_tokens(self):
        token = self.code_token(self.member)
        self.website.delete()
        self.assertEqual(self.call('get', '/api/v1/me', token)[0], 401)

    # SSO

    def test_sso_off(self):
        website, key = self.new_website('off')
        self.assertEqual(website.sso_mode, 'off')
        self.assertEqual(self.sso('member@example.org', key=key)[0], 403)

    def test_sso_auto_links_and_creates(self):
        status, data = self.sso('Member@example.org')
        self.assertEqual(status, 200)
        self.assertEqual(self.call('get', '/api/v1/me', data['token'])[1]['user']['email'], 'member@example.org')
        status, data = self.sso('neu@example.org', wp_user='8')
        self.assertEqual(status, 200)
        created = User.objects.get(email='neu@example.org')
        self.assertIsNotNone(created.membership(self.alpha))
        self.assertEqual(created.first_name, 'Wp')
        # Eine spaetere Anmeldung desselben WP-Kontos ersetzt den alten Schluessel
        self.sso('neu@example.org', wp_user='8')
        self.assertEqual(ApiToken.objects(user=created.pk).count(), 1)

    def test_sso_existing_only(self):
        _, key = self.new_website('existing')
        self.assertEqual(self.sso('member@example.org', key=key)[0], 200)
        self.assertEqual(self.sso('fremd@example.org', wp_user='9', key=key)[0], 403)
        self.assertFalse(User.objects(email='fremd@example.org'))

    def test_sso_approval(self):
        website, key = self.new_website('approval')
        status, data = self.sso('neu@example.org', key=key)
        self.assertEqual((status, data), (202, {'pending': True}))
        self.assertFalse(User.objects(email='neu@example.org'))
        link = WebsiteLink.objects.get(website=website.pk)
        client = self.login(self.admin, A)
        page = client.get('/admin/Websites/', base_url=A).get_data(as_text=True)
        self.assertIn('neu@example.org', page)
        client.post('/admin/Websites/verknuepfung', base_url=A, data={'link_id': str(link.pk), 'action': 'accept'})
        self.assertEqual(self.sso('neu@example.org', key=key)[0], 200)
        self.assertIsNotNone(User.objects.get(email='neu@example.org').membership(self.alpha))

    def test_sso_never_links_global_admin(self):
        self.user('root@example.org', global_admin=True)
        self.assertEqual(self.sso('root@example.org')[0], 403)

    def test_sso_foreign_member_needs_join(self):
        self.user('beta@example.org', (self.beta, {'role': 'member'}))
        self.assertEqual(self.sso('beta@example.org')[0], 202)
        self.assertIsNotNone(User.objects.get(email='beta@example.org').join_request(self.alpha))

    # Admin-Panel

    def test_admin_creates_website_and_sees_key_once(self):
        client = self.login(self.admin, A)
        response = client.post('/admin/Websites/neu', base_url=A, data={
            'name': 'Neue Seite', 'return_url': 'https://neu.example.org/cb', 'sso_mode': 'existing'})
        page = response.get_data(as_text=True)
        self.assertIn('tts_', page)
        self.assertEqual(Website.objects(name='Neue Seite').get().sso_mode, 'existing')
        self.assertNotIn('tts_', client.get('/admin/Websites/', base_url=A).get_data(as_text=True))
        client.post('/admin/Websites/neu', base_url=A, data={
            'name': 'Unsicher', 'return_url': 'http://neu.example.org/cb', 'sso_mode': 'off'})
        self.assertFalse(Website.objects(name='Unsicher'))

    def test_admin_view_only_for_admins(self):
        client = self.login(self.member, A)
        self.assertEqual(client.get('/admin/Websites/', base_url=A).status_code, 404)

    def test_other_section_admin_cannot_touch_website(self):
        beta_admin = self.user('badmin@example.org', (self.beta, {'admin': True}))
        client = self.login(beta_admin, B)
        client.post(f'/admin/Websites/{self.website.pk}/loeschen', base_url=B)
        self.assertTrue(Website.objects(id=self.website.pk))

    def test_section_delete_removes_websites(self):
        from application.modules.section_delete import delete_section  # pylint: disable=import-outside-toplevel
        _, beta_key = self.new_website('auto', self.beta)
        self.assertEqual(self.sso('member@example.org')[0], 200)
        self.call('post', '/api/site/v1/sso', beta_key, host=B, json={'wp_user': '1', 'email': 'x@example.org'})
        delete_section(self.beta)
        self.assertEqual(Website.objects.count(), 1)
        self.assertEqual(WebsiteLink.objects.count(), 1)
        self.assertEqual(ApiToken.objects.count(), 1)


if __name__ == '__main__':
    unittest.main()
