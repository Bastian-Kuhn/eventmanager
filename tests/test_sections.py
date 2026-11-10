"""
Mehrere Sektionen in einer Installation: Auswahl ueber den Hostnamen, Design
und Mailversand je Sektion.
Ausfuehren: .venv/bin/python -m unittest tests.test_sections (mongomock, ohne Mongo/Redis)
"""
import re
import unittest
from unittest import mock

import mongoengine
import mongomock
from mongomock.gridfs import enable_gridfs_integration

from application import app, limiter
from application.models.config import Category, Config
from application.models.section import Section
from application.models.user import User
from tests.test_security import REGISTRATION


def setUpModule():
    enable_gridfs_integration()
    mongoengine.disconnect()
    mongoengine.connect('test', mongo_client_class=mongomock.MongoClient)


def tearDownModule():
    mongoengine.disconnect()


class SectionTest(unittest.TestCase):
    """Zwei Sektionen mit eigenem Host, Design und Mailserver"""

    def setUp(self):
        for model in (User, Section, Config):
            model.drop_collection()
        app.redis = mock.MagicMock()
        limiter.enabled = False
        app.config.update(WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=False)
        self.alpha = Section(slug='alpha', name='Sektion Alpha', hostnames=['Alpha.Example.org'],
                             nav_background_color='#111111', mail_server='smtp.alpha.example.org',
                             mail_sender='alpha@example.org', mail_subject_prefix='[Alpha]',
                             event_categories_full=[Category(name='Skitour', color='#0000ff')]).save()
        self.beta = Section(slug='beta', name='Sektion Beta', hostnames=['beta.example.org'],
                            nav_background_color='#222222', mail_server='smtp.beta.example.org',
                            mail_sender='beta@example.org').save()

    @staticmethod
    def get(host, path='/login'):
        return app.test_client().get(path, base_url=f'https://{host}')

    def test_host_selects_section_design(self):
        alpha = self.get('alpha.example.org').get_data(as_text=True)
        beta = self.get('beta.example.org:443').get_data(as_text=True)
        self.assertIn('#111111', alpha)
        self.assertIn('alt="Sektion Alpha"', alpha)
        self.assertIn('#222222', beta)
        self.assertNotIn('#111111', beta)

    def test_hostnames_are_stored_lowercase(self):
        self.assertEqual(Section.objects.get(slug='alpha').hostnames, ['alpha.example.org'])

    def test_unknown_host_without_default_is_404(self):
        self.assertEqual(self.get('other.example.org').status_code, 404)

    def test_unknown_host_uses_default_section(self):
        self.beta.is_default = True
        self.beta.save()
        page = self.get('neu.tourenanmeldung.example.org')
        self.assertEqual(page.status_code, 200)
        self.assertIn('#222222', page.get_data(as_text=True))

    def test_categories_come_from_section(self):
        alpha = self.get('alpha.example.org', '/').get_data(as_text=True)
        beta = self.get('beta.example.org', '/').get_data(as_text=True)
        self.assertIn('Skitour', alpha)
        self.assertNotIn('Skitour', beta)

    def test_mail_uses_smtp_of_section(self):
        for host, server, sender in (('alpha.example.org', 'smtp.alpha.example.org', 'alpha@example.org'),
                                     ('beta.example.org', 'smtp.beta.example.org', 'beta@example.org')):
            User.drop_collection()
            with mock.patch('application.modules.email.Thread') as thread:
                app.test_client().post('/user/create', data=REGISTRATION, base_url=f'https://{host}')
            message, state = thread.call_args.kwargs['args'][1:3]
            self.assertEqual(state.server, server)
            self.assertEqual(message.sender, sender)
        self.assertNotIn('[Alpha]', message.subject)

    def test_legacy_config_without_sections(self):
        Section.drop_collection()
        Config(enabled=True, nav_background_color='#333333').save()
        page = self.get('irgendwas.example.org')
        self.assertEqual(page.status_code, 200)
        self.assertIn('#333333', page.get_data(as_text=True))

    def test_confirm_link_points_to_own_host(self):
        with mock.patch('application.modules.email.Thread') as thread:
            app.test_client().post('/user/create', data=REGISTRATION, base_url='https://beta.example.org')
        message = thread.call_args.kwargs['args'][1]
        self.assertTrue(re.search(r'https://beta\.example\.org/user/confirm/', message.html))

    def test_only_global_admins_manage_sections(self):
        from tests.test_security import AppTestCase  # pylint: disable=import-outside-toplevel
        self.alpha.is_default = True  # login() laeuft ueber localhost
        self.alpha.save()
        root = AppTestCase.user('root@example.org', global_admin=True)
        club = AppTestCase.user('club@example.org', admin=True)
        for user, allowed in ((root, True), (club, False)):
            client = AppTestCase.login(user)
            for path in ('/admin/section/', '/admin/section/new/'):
                response = client.get(path)
                self.assertEqual(response.status_code == 200, allowed, (user.email, path))


if __name__ == '__main__':
    unittest.main()
