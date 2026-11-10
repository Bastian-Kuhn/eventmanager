"""
Regressionstests fuer Sicherheits- und Stabilitaetsfixes, gegen die echte App.
Ausfuehren: .venv/bin/python -m unittest discover tests
Braucht mongomock (requirements-dev.txt), aber kein Mongo/Redis.
"""
import re
import time
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import mongoengine
import mongomock
import pyotp
from mongomock.gridfs import enable_gridfs_integration

from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter

from application import app, limiter
from application.events.models import CustomFieldDefintion, Event, EventCost, EventParticipation, \
    OwnedTicket, Ticket
from application.events.forms import COMMENT_MAX, CUSTOM_FIELD_MAX, EMAIL_MAX, PHONE_MAX, \
    TICKET_NAME_MAX
from application.events.views import difficult_to_icon
from application.huts.models import Hut, HutBooking, HutRoom
from application.huts.views import MAX_NIGHTS, MAX_OPEN_BOOKINGS
from application.models.config import Config
from application.models.user import User
from application.modules import tfa
from application.modules.sanitize import csv_safe, is_local_path, safe_url, sanitize_html

PASSWORD = 'Geheim!123x'
NEW_PASSWORD = 'Anderes!456y'


def setUpModule():
    enable_gridfs_integration()
    mongoengine.disconnect()
    mongoengine.connect('test', mongo_client_class=mongomock.MongoClient)


def tearDownModule():
    mongoengine.disconnect()


class AppTestCase(unittest.TestCase):
    """Saubere DB, CSRF aus (eigene Klasse prueft es), Limiter aus"""
    csrf_enabled = False

    def setUp(self):
        for model in (User, Event, Hut, HutBooking, Config):
            model.drop_collection()
        app.redis = mock.MagicMock()
        limiter.enabled = False
        app.config.update(WTF_CSRF_ENABLED=self.csrf_enabled, PROPAGATE_EXCEPTIONS=False)
        self.addCleanup(app.config.update, WTF_CSRF_ENABLED=False)

    @staticmethod
    def user(email='user@example.org', **kwargs):
        user = User(email=email, first_name=kwargs.pop('first_name', 'Vor'),
                    last_name=kwargs.pop('last_name', 'Nach'), phone='0170',
                    birthdate=date(1990, 1, 1), **kwargs)
        user.set_password(PASSWORD)
        return user.save()

    @staticmethod
    def event(**kwargs):
        now = datetime.now()
        defaults = {
            'event_name': 'Tour', 'event_teaser': 't', 'event_type': 'guided', 'places': 2,
            'start_date': now + timedelta(days=5), 'end_date': now + timedelta(days=6),
            'booking_from': now - timedelta(days=1), 'booking_until': now + timedelta(days=4),
            'tickets': [Ticket(name='Teilnahme', price=10.0, maximum_tickets=2)],
        }
        return Event(**{**defaults, **kwargs}).save()

    def owner(self, event, email='g@example.org', role='guide'):
        """Trainer, der als Verantwortlicher der Tour eingetragen ist"""
        user = self.user(email, role=role)
        Event.objects(id=event.id).update_one(push__event_owners=user)
        return user

    @staticmethod
    def login(user, password=PASSWORD):
        client = app.test_client()
        response = client.post('/login', data={'login_email': user.email, 'password': password,
                                               'login_submit': 'Anmelden'})
        assert response.status_code == 302, response.status_code
        return client

    @staticmethod
    def booking(user, event, ticket_name='Teilnahme', **ticket_args):
        ticket = OwnedTicket(ticket_id=ticket_args.pop('ticket_id', 't1'), ticket_name=ticket_name,
                             name_on_ticket=ticket_args.pop('name', 'Vor Nach'), **ticket_args)
        event.participations.append(EventParticipation(
            user=user, booking_date=datetime.now(), tickets=[ticket]))
        event.save()
        User.objects(id=user.id).update_one(add_to_set__event_registrations=event)
        return ticket


REGISTRATION = {
    'first_name': 'Eve', 'last_name': 'Evil', 'email': 'Eve@Example.org', 'phone': '1',
    'birthdate': '1990-01-01', 'password': PASSWORD, 'password_repeat': PASSWORD,
    'submit': 'Registrieren',
}


class RegistrationTest(AppTestCase):
    """Anonyme Registrierung"""

    def test_normal_registration(self):
        response = app.test_client().post('/user/create', data=REGISTRATION)
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='eve@example.org')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(user.birthdate, date(1990, 1, 1))
        self.assertIsNotNone(user.date_added)

    def test_role_cannot_be_set(self):
        app.test_client().post('/user/create', data={**REGISTRATION, 'role': 'guide'})
        self.assertFalse(User.objects.get(email='eve@example.org').has_right('guide'))

    def test_rights_cannot_be_set(self):
        app.test_client().post('/user/create', data={**REGISTRATION, 'global_admin': 'y', 'admin': 'y'})
        user = User.objects.get(email='eve@example.org')
        self.assertFalse(user.is_admin())

    def test_existing_user_cannot_be_overwritten(self):
        victim = self.user('admin@example.org', global_admin=True)
        app.test_client().post('/user/create', data={**REGISTRATION, 'id': str(victim.id)})
        victim.reload()
        self.assertEqual(victim.email, 'admin@example.org')
        self.assertTrue(victim.global_admin)
        self.assertEqual(User.objects.count(), 2)

    def test_existing_email_gets_the_same_answer(self):
        self.user('taken@example.org')
        new = app.test_client().post('/user/create', data={**REGISTRATION, 'email': 'new@example.org'})
        taken = app.test_client().post('/user/create', data={**REGISTRATION, 'email': 'taken@example.org'})
        self.assertEqual((new.status_code, new.location), (taken.status_code, taken.location))
        self.assertFalse(User.objects.get(email='taken@example.org').check_password('x'))
        self.assertTrue(User.objects.get(email='taken@example.org').check_password(PASSWORD))
        self.assertEqual(User.objects(email='taken@example.org').count(), 1)

    def test_names_with_umlauts_and_separators(self):
        for first, last in (('Jürgen', "O'Brien"), ('Anna-Lena', 'von der Heide'),
                            ('J. R.', 'Müller-Lüdenscheidt'), ('  Eve ', 'Evil')):
            User.drop_collection()
            app.test_client().post('/user/create', data={**REGISTRATION, 'first_name': first,
                                                         'last_name': last})
            user = User.objects.get(email='eve@example.org')
            self.assertEqual(user.last_name, last)
        self.assertEqual(user.first_name, 'Eve')

    def test_name_cannot_carry_text_or_links(self):
        for name in ('Konto gesperrt, bitte melde dich unter www.example.com',
                     'example.com', 'https://example.com', 'Eve<b>', 'Eve 123',
                     'A' * 51):
            response = app.test_client().post('/user/create', data={**REGISTRATION, 'first_name': name})
            self.assertEqual(response.status_code, 200, name)
            response = app.test_client().post('/user/create', data={**REGISTRATION, 'last_name': name})
            self.assertEqual(response.status_code, 200, name)
        self.assertEqual(User.objects.count(), 0)


class EmailConfirmTest(AppTestCase):
    """Selbstregistrierung erst nach Bestaetigung der E-Mail-Adresse nutzbar"""

    def _register(self, **data):
        Config.objects(enabled=True).first() or Config(enabled=True, mail_sender='a@example.org').save()
        with mock.patch('application.modules.email.Thread') as thread:
            app.test_client().post('/user/create', data={**REGISTRATION, **data})
        message = thread.call_args.kwargs['args'][1]
        return re.search(r'/user/confirm/[^\s<]+', message.html).group(0)

    def _login_works(self, password=PASSWORD):
        response = app.test_client().post('/login', data={
            'login_email': 'eve@example.org', 'password': password, 'login_submit': 'Anmelden'})
        return response.status_code == 302

    def test_login_only_after_confirmation(self):
        link = self._register()
        self.assertFalse(self._login_works())
        self.assertEqual(app.test_client().get(link).status_code, 200)
        self.assertFalse(self._login_works())
        app.test_client().post(link, data={'password': 'falsch', 'submit': 'x'})
        self.assertFalse(self._login_works())
        app.test_client().post(link, data={'password': PASSWORD, 'submit': 'x'})
        self.assertTrue(self._login_works())

    def test_new_registration_replaces_unconfirmed_account(self):
        first_link = self._register(first_name='Mallory')
        self._register(password=NEW_PASSWORD, password_repeat=NEW_PASSWORD)
        self.assertEqual(User.objects(email='eve@example.org').count(), 1)
        self.assertEqual(User.objects.get(email='eve@example.org').first_name, 'Eve')
        app.test_client().post(first_link, data={'password': PASSWORD, 'submit': 'x'})
        self.assertFalse(self._login_works())

    def test_password_reset_confirms(self):
        self._register()
        user = User.objects.get(email='eve@example.org')
        with app.test_request_context():
            token = user.generate_token()
        app.test_client().post(f'/reset-password/{token}', data={
            'password': NEW_PASSWORD, 'password_repeat': NEW_PASSWORD, 'submit': 'x'})
        self.assertTrue(self._login_works(NEW_PASSWORD))

    def test_existing_accounts_keep_working(self):
        self.user('eve@example.org')
        User.objects(email='eve@example.org').update_one(unset__email_unconfirmed=True)
        self.assertTrue(self._login_works())


class RateLimitTest(AppTestCase):
    """Der Limiter muss wirklich aktiv sein"""

    def setUp(self):
        super().setUp()
        # Eigener Speicher statt Redis
        storage = MemoryStorage()
        patcher = mock.patch.multiple(limiter, _storage=storage,
                                      _limiter=FixedWindowRateLimiter(storage), enabled=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _posts(self, url, count, data=None):
        client = app.test_client()
        return [client.post(url, data=data or {}).status_code for _ in range(count)]

    def test_login(self):
        self.assertIn(429, self._posts('/login', 15, {'login_email': 'a@example.org', 'password': 'x',
                                                      'login_submit': 'Anmelden'}))

    def test_registration(self):
        self.assertIn(429, self._posts('/user/create', 6))

    def test_password_request(self):
        self.assertIn(429, self._posts('/request-password', 8))

    def test_login_on_event_page(self):
        event = self.event()
        self.assertIn(429, self._posts(f'/event/details?event_id={event.id}', 15))

    def test_booking_is_limited_per_account(self):
        event = self.event(places=50, tickets=[Ticket(name='Teilnahme', price=0.0, maximum_tickets=0)])
        client = self.login(self.user())
        codes = [client.post(f'/event/details?event_id={event.id}', data={
            'comment': '', 'ticket_Teilnahme': '0', 'submit': 'Anmelden'}).status_code
                 for _ in range(8)]
        self.assertIn(429, codes)
        # Ein anderes Konto hinter derselben IP bucht weiter
        other = self.login(self.user('other@example.org'))
        self.assertNotEqual(other.post(f'/event/details?event_id={event.id}', data={
            'comment': '', 'ticket_Teilnahme': '0', 'submit': 'Anmelden'}).status_code, 429)

    def test_hut_booking_is_limited_per_account(self):
        hut = Hut(name='H', rooms=[HutRoom(name='Lager', places=2)]).save()
        client = self.login(self.user())
        codes = [client.post(f'/huetten/{hut.id}/book', data={}).status_code for _ in range(8)]
        self.assertIn(429, codes)

    def test_ipv6_counts_per_64(self):
        data = {'login_email': 'a@example.org', 'password': 'x', 'login_submit': 'Anmelden'}
        client = app.test_client()
        codes = [client.post('/login', data={**data, 'login_email': f'a{i}@example.org'},
                             environ_base={'REMOTE_ADDR': f'2001:db8:1:2::{i:x}'}).status_code
                 for i in range(1, 15)]
        self.assertIn(429, codes)
        self.assertEqual(client.post('/login', data=data, environ_base={
            'REMOTE_ADDR': '2001:db8:1:3::1'}).status_code, 200)

    def _login_from(self, number, password):
        return app.test_client().post('/login', data={
            'login_email': 'user@example.org', 'password': password, 'login_submit': 'Anmelden'},
            environ_base={'REMOTE_ADDR': f'192.0.2.{number}'})

    def test_account_is_locked_after_failures(self):
        user = self.user()
        for number in range(10):
            self.assertEqual(self._login_from(number, 'falsch').status_code, 200)
        locked = self._login_from(50, PASSWORD)
        self.assertEqual(locked.status_code, 200)
        self.assertIn('Zu viele Fehlversuche'.encode(), locked.data)
        # Ein anderes Konto ist nicht betroffen
        self.login(self.user('other@example.org'))
        # Der Reset-Link hebt die Sperre auf
        with app.app_context():
            token = user.generate_token()
        app.test_client().post(f'/reset-password/{token}', data={
            'password': NEW_PASSWORD, 'password_repeat': NEW_PASSWORD})
        self.assertEqual(self._login_from(51, NEW_PASSWORD).status_code, 302)

    def test_lock_expires(self):
        self.user()
        with mock.patch('time.time', return_value=1_000_000):
            for number in range(10):
                self._login_from(number, 'falsch')
            self.assertEqual(self._login_from(50, PASSWORD).status_code, 200)
        with mock.patch('time.time', return_value=1_000_000 + 16 * 60):
            self.assertEqual(self._login_from(51, PASSWORD).status_code, 302)

    def _mails_to(self, path, data, count):
        Config.objects(enabled=True).first() or Config(enabled=True, mail_sender='a@example.org').save()
        with mock.patch('application.modules.email.Thread') as thread:
            for number in range(count):
                app.test_client().post(path, data=data,
                                       environ_base={'REMOTE_ADDR': f'192.0.2.{number}'})
        return thread.call_count

    def test_reset_mails_are_limited_per_address(self):
        self.user()
        data = {'request_email': 'user@example.org', 'request_submit': 'Senden'}
        self.assertEqual(self._mails_to('/request-password', data, 6), 3)

    def test_confirm_mails_are_limited_per_address(self):
        self.assertEqual(self._mails_to('/user/create', REGISTRATION, 6), 3)
        # Reset-Mails an dieselbe Adresse zaehlen mit
        data = {'request_email': 'eve@example.org', 'request_submit': 'Senden'}
        self.assertEqual(self._mails_to('/request-password', data, 1), 0)

    def test_login_works_without_redis(self):
        with mock.patch.multiple(limiter, _storage=mock.MagicMock(),
                                 _limiter=mock.MagicMock(**{'hit.side_effect': ConnectionError})):
            self.assertEqual(self._posts('/login', 1), [200])

    def test_get_is_not_limited(self):
        client = app.test_client()
        codes = {client.get('/login').status_code for _ in range(15)}
        self.assertEqual(codes, {200})



class SessionTest(AppTestCase):
    """Sessions, Sperren, Passwortwechsel"""

    def test_disabled_user_loses_session(self):
        user = self.user()
        client = self.login(user)
        self.assertEqual(client.get('/user/profile').status_code, 200)
        User.objects(id=user.id).update_one(set__disabled=True)
        self.assertEqual(client.get('/user/profile').status_code, 302)

    def test_deleted_user_session_is_harmless(self):
        user = self.user()
        client = self.login(user)
        User.objects(id=user.id).delete()
        self.assertEqual(client.get('/login').status_code, 200)
        self.assertEqual(client.get('/').status_code, 200)

    def test_password_change_ends_other_sessions_only(self):
        user = self.user()
        other_device = self.login(user)
        client = self.login(user)
        response = client.post('/user/profile/password', data={
            'old_password': PASSWORD, 'password': NEW_PASSWORD, 'password_repeat': NEW_PASSWORD,
            'submit_password': 'x'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(id=user.id).check_password(NEW_PASSWORD))
        self.assertEqual(client.get('/user/profile').status_code, 200)
        self.assertEqual(other_device.get('/user/profile').status_code, 302)

    def test_force_password_change_blocks_until_changed(self):
        user = self.user(force_password_change=True)
        client = self.login(user)
        response = client.get('/user/profile')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/change-password', response.location)

        form = {'old_password': PASSWORD, 'password': NEW_PASSWORD, 'password_repeat': NEW_PASSWORD}
        client.post('/change-password', data={**form, 'old_password': 'falsch'})
        self.assertTrue(User.objects.get(id=user.id).check_password(PASSWORD))

        self.assertEqual(client.post('/change-password', data=form).status_code, 302)
        user.reload()
        self.assertFalse(user.force_password_change)
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertEqual(client.get('/user/profile').status_code, 200)

    def test_change_password_needs_login(self):
        self.assertEqual(app.test_client().post('/change-password', data={}).status_code, 302)

    def test_wrong_and_unknown_login_answer_alike(self):
        self.user()
        client = app.test_client()
        wrong = client.post('/login', data={'login_email': 'user@example.org', 'password': 'x',
                                            'login_submit': 'Anmelden'})
        unknown = client.post('/login', data={'login_email': 'no@example.org', 'password': 'x',
                                              'login_submit': 'Anmelden'})
        self.assertEqual(wrong.status_code, unknown.status_code)
        self.assertIn('E-Mail oder Passwort ist falsch'.encode(), wrong.data)
        self.assertIn('E-Mail oder Passwort ist falsch'.encode(), unknown.data)


class ResetTokenTest(AppTestCase):
    """Passwort-Reset per Link"""

    def _token(self, user):
        with app.test_request_context():
            return user.generate_token()

    def _reset(self, token, password=NEW_PASSWORD):
        return app.test_client().post(f'/reset-password/{token}', data={
            'password': password, 'password_repeat': password, 'submit': 'x'})

    def test_token_works_once(self):
        user = self.user()
        token = self._token(user)
        self._reset(token)
        self.assertTrue(User.objects.get(id=user.id).check_password(NEW_PASSWORD))
        self._reset(token, 'Dritter!789z')
        self.assertTrue(User.objects.get(id=user.id).check_password(NEW_PASSWORD))

    def test_disabled_user_cannot_reset(self):
        user = self.user(disabled=True)
        self._reset(self._token(user))
        self.assertTrue(User.objects.get(id=user.id).check_password(PASSWORD))

    def test_garbage_and_forged_tokens(self):
        user = self.user()
        token = self._token(user)
        for bad in ('kaputt', token[:-3] + 'abc'):
            self.assertEqual(self._reset(bad).status_code, 302)
        self.assertTrue(User.objects.get(id=user.id).check_password(PASSWORD))

    def test_mail_without_subject_prefix_and_with_name(self):
        self.user()
        Config(enabled=True, mail_subject_prefix=None, mail_sender='a@example.org').save()
        with mock.patch('application.modules.email.Thread') as thread:
            response = app.test_client().post('/request-password', data={
                'request_email': 'user@example.org', 'request_submit': 'Senden'})
        self.assertEqual(response.status_code, 302)
        message = thread.call_args.kwargs['args'][1]
        self.assertEqual(message.subject, 'Neues Passwort')
        self.assertIn('Hallo Vor,', message.html)


class CsrfTest(AppTestCase):
    """Globaler CSRF-Schutz inkl. Admin-Panel"""
    csrf_enabled = True

    @staticmethod
    def _token(html):
        return re.search(rb'name="csrf_token"[^>]*value="([^"]+)"|value="([^"]+)"[^>]*name="csrf_token"',
                         html).group(1).decode()

    def _guide(self, **kwargs):
        user = self.user('guide@example.org', role='guide', **kwargs)
        client = app.test_client()
        token = self._token(client.get('/login').data)
        response = client.post('/login', data={'login_email': user.email, 'password': PASSWORD,
                                               'login_submit': 'Anmelden', 'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        return client

    def test_post_without_token_is_rejected(self):
        client = self._guide()
        event = self.event()
        self.assertEqual(client.post('/event/delete', data={'event_id': event.id}).status_code, 400)
        self.assertEqual(Event.objects.count(), 1)

    def test_form_token_is_accepted(self):
        client = self._guide()
        event = self.event(event_owners=[User.objects.get(email='guide@example.org')])
        page = client.get(f'/event/admin?event_id={event.id}').data
        token = self._token(page[page.index(b'/event/delete'):])
        response = client.post('/event/delete', data={'event_id': event.id, 'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Event.objects.count(), 0)

    def test_ajax_header_token_is_accepted(self):
        client = self._guide()
        event = self.event()
        page = client.get('/').data
        token = re.search(rb"'X-CSRFToken': \"([^\"]+)\"", page).group(1).decode()
        response = client.post('/event/favorite', data={'event_id': event.id},
                               headers={'X-CSRFToken': token})
        self.assertEqual(response.status_code, 200)

    def test_booking_form_contains_token(self):
        client = self._guide()
        event = self.event()
        page = client.get(f'/event/details?event_id={event.id}').data
        response = client.post(f'/event/details?event_id={event.id}', data={
            'comment': '', 'ticket_Teilnahme': '1', 'submit': 'Anmelden',
            'csrf_token': self._token(page)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(Event.objects.get(id=event.id).participations), 1)

    def test_all_plain_post_forms_carry_a_token(self):
        """Jedes Template-Formular mit POST braucht ein Token, sonst geht es nach dem Fix nicht mehr"""
        import glob
        for path in glob.glob('application/templates/*.html'):
            with open(path, encoding='utf-8') as template:
                source = template.read()
            for match in re.finditer(r'<form\b[^>]*method="post"[^>]*>', source, re.I):
                body = source[match.end():source.index('</form>', match.end())]
                if 'id="add_ticket_form"' in match.group(0):
                    continue  # geht per $.post, Token kommt aus ajaxSetup
                self.assertTrue('csrf_token' in body or 'hidden_tag' in body,
                                f"{path}: {match.group(0)}")

    def test_admin_delete_needs_token(self):
        admin = self.user('admin@example.org', global_admin=True)
        client = app.test_client()
        token = self._token(client.get('/login').data)
        client.post('/login', data={'login_email': admin.email, 'password': PASSWORD,
                                    'login_submit': 'Anmelden', 'csrf_token': token})
        victim = self.user('victim@example.org')
        client.post('/admin/user/delete/', data={'id': victim.id, 'url': '/admin/user/'})
        self.assertEqual(User.objects(id=victim.id).count(), 1)

        page = client.get('/admin/user/').data
        client.post('/admin/user/delete/', data={'id': victim.id, 'url': '/admin/user/',
                                                 'csrf_token': self._token(page)})
        self.assertEqual(User.objects(id=victim.id).count(), 0)

    def test_admin_inline_edit_needs_token(self):
        admin = self.user('admin@example.org', global_admin=True)
        client = app.test_client()
        token = self._token(client.get('/login').data)
        client.post('/login', data={'login_email': admin.email, 'password': PASSWORD,
                                    'login_submit': 'Anmelden', 'csrf_token': token})
        victim = self.user('victim@example.org')
        form = {'list_form_pk': victim.id, 'disabled': 'y'}
        self.assertEqual(client.post('/admin/user/ajax/update/', data=form).status_code, 400)
        self.assertFalse(User.objects.get(id=victim.id).disabled)

        page = client.get('/admin/user/').data.decode()
        token = re.search(r'data-csrf="([^"]+)"', page).group(1)
        response = client.post('/admin/user/ajax/update/', data={**form, 'csrf_token': token})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(User.objects.get(id=victim.id).disabled)

    def test_admin_edit_with_embedded_documents(self):
        """Eingebettete Formulare (Zimmer) funktionieren mit dem globalen Token"""
        admin = self.user('admin@example.org', global_admin=True)
        client = app.test_client()
        token = self._token(client.get('/login').data)
        client.post('/login', data={'login_email': admin.email, 'password': PASSWORD,
                                    'login_submit': 'Anmelden', 'csrf_token': token})
        hut = Hut(name='Alt', rooms=[HutRoom(name='Lager', places=4)]).save()
        url = f'/admin/hut/edit/?id={hut.id}'
        page = client.get(url).data
        response = client.post(url, data={
            'csrf_token': self._token(page), 'name': 'Neu', 'allow_self_booking': 'y',
            'rooms-0-name': 'Lager', 'rooms-0-places': '6'})
        self.assertEqual(response.status_code, 302, response.data[:2000])
        hut.reload()
        self.assertEqual(hut.name, 'Neu')
        self.assertEqual(hut.rooms[0].places, 6)


class MemberAdminTest(AppTestCase):
    """Vereins-Admins duerfen Global Admins nicht anfassen"""

    def test_admin_cannot_change_global_admin(self):
        boss = self.user('boss@example.org', global_admin=True)
        client = self.login(self.user('admin@example.org', admin=True))
        client.post(f'/admin/Members/edit/?id={boss.id}', data={'email': 'evil@example.org'})
        self.assertEqual(User.objects.get(id=boss.id).email, 'boss@example.org')

    def test_admin_cannot_delete_global_admin(self):
        boss = self.user('boss@example.org', global_admin=True)
        client = self.login(self.user('admin@example.org', admin=True))
        client.post('/admin/Members/delete/', data={'id': boss.id, 'url': '/admin/Members/'})
        self.assertEqual(User.objects(id=boss.id).count(), 1)

    def test_admin_can_change_normal_member(self):
        member = self.user('member@example.org')
        client = self.login(self.user('admin@example.org', admin=True))
        response = client.post(f'/admin/Members/edit/?id={member.id}', data={
            'email': 'neu@example.org', 'first_name': 'Neu'})
        self.assertEqual(response.status_code, 302, response.data[:1500])
        self.assertEqual(User.objects.get(id=member.id).email, 'neu@example.org')


class XssTest(AppTestCase):
    """Ausgabe von Nutzereingaben"""

    def test_description_is_cleaned_but_keeps_formatting(self):
        event = self.event(event_description=(
            '<p style="text-align:center"><b>Tour</b> <a href="https://x.example/">Link</a></p>'
            '<script>alert(1)</script><img src=x onerror=alert(2)>'
            '<a href="javascript:alert(3)">y</a>'))
        html = app.test_client().get(f'/event/details?event_id={event.id}').data.decode()
        self.assertIn('text-align:center', html)
        self.assertIn('<b>Tour</b>', html)
        self.assertIn('https://x.example/', html)
        for payload in ('alert(1)', 'onerror', 'javascript:alert'):
            self.assertNotIn(payload, html)

    def test_ticket_name_is_escaped_in_label(self):
        name = '<img src=x onerror=alert(1)>'
        event = self.event(tickets=[Ticket(name=name, price=0.0, maximum_tickets=2)])
        client = self.login(self.user())
        html = client.get(f'/event/details?event_id={event.id}').data.decode()
        self.assertNotIn('<img src=x', html)

    def test_extra_ticket_page_has_no_html_sink(self):
        with open('application/templates/event_extra_tickets.html', encoding='utf-8') as template:
            source = template.read()
        self.assertNotIn('.html(', source)
        self.assertNotIn("'<option value=", source)

    def test_hut_link_scheme(self):
        hut = Hut(name='H', link='javascript:alert(1)').save()
        self.assertNotIn(b'javascript:alert', app.test_client().get(f'/huetten/{hut.id}').data)
        hut.link = 'https://example.org/'
        hut.save()
        self.assertIn(b'https://example.org/', app.test_client().get(f'/huetten/{hut.id}').data)

    def test_helpers(self):
        self.assertEqual(safe_url(' JAVASCRIPT:alert(1)'), '')
        self.assertEqual(safe_url('java\nscript:alert(1)'), '')
        self.assertEqual(safe_url('http://a.example'), 'http://a.example')
        self.assertEqual(sanitize_html(None), '')
        for good in ('/', '/huetten/1?x=1'):
            self.assertTrue(is_local_path(good))
        for bad in (None, '', 'https://evil.example', '//evil.example', '/\\evil.example',
                    '/\t/evil.example', 'javascript:alert(1)'):
            self.assertFalse(is_local_path(bad), bad)
        self.assertEqual(csv_safe('=1+1'), "'=1+1")
        self.assertEqual(csv_safe('@SUM(A1)'), "'@SUM(A1)")
        self.assertEqual(csv_safe('+49 170 123'), '+49 170 123')
        self.assertEqual(csv_safe('+1+cmd|x'), "'+1+cmd|x")
        self.assertEqual(csv_safe('Name'), 'Name')
        self.assertEqual(csv_safe(5), 5)


class PrivacyTest(AppTestCase):
    """Sammel-Mailto nur fuer Guides"""

    def _event_with_hidden_address(self):
        event = self.event()
        hidden = self.user('hidden@example.org', data_optin=False)
        self.booking(hidden, event, confirmed=True, email_on_ticket='hidden@example.org')
        return event

    def test_participant_does_not_get_addresses(self):
        event = self._event_with_hidden_address()
        participant = self.user('p@example.org', data_optin=True)
        self.booking(participant, event, ticket_id='t2', confirmed=True)
        html = self.login(participant).get(f'/event/participants?event_id={event.id}').data
        self.assertNotIn(b'hidden@example.org', html)
        self.assertNotIn(b'Mail an Teilnehmer', html)

    def test_guide_gets_addresses(self):
        event = self._event_with_hidden_address()
        guide = self.user('g@example.org', role='guide')
        Event.objects(id=event.id).update_one(push__event_owners=guide)
        html = self.login(guide).get(
            f'/event/participants?event_id={event.id}').data
        self.assertIn(b'mailto:hidden@example.org', html)

    def test_unverified_open_booking_gets_no_list(self):
        event = self._event_with_hidden_address()
        newcomer = self.user('new@example.org')
        self.booking(newcomer, event, ticket_id='t2')
        client = self.login(newcomer)
        self.assertEqual(client.get(f'/event/participants?event_id={event.id}').status_code, 403)
        self.assertNotIn(b'/event/participants', client.get(f'/event/details?event_id={event.id}').data)

    def test_member_with_open_booking_gets_list(self):
        event = self._event_with_hidden_address()
        member = self.user('m@example.org', role='member')
        self.booking(member, event, ticket_id='t2')
        response = self.login(member).get(f'/event/participants?event_id={event.id}')
        self.assertEqual(response.status_code, 200)

    def test_member_without_booking_gets_no_list(self):
        event = self._event_with_hidden_address()
        member = self.user('m@example.org', role='member')
        response = self.login(member).get(f'/event/participants?event_id={event.id}')
        self.assertEqual(response.status_code, 403)


class HutTest(AppTestCase):
    """Huettenbuchung"""

    def setUp(self):
        super().setUp()
        self.hut = Hut(name='H', rooms=[HutRoom(name='Lager', places=2)]).save()
        self.client = self.login(self.user())
        self.start = date.today() + timedelta(days=3)

    def _book(self, nights=1, **form):
        data = {'from_date': self.start.isoformat(),
                'to_date': (self.start + timedelta(days=nights)).isoformat(), 'places': '1', **form}
        return self.client.post(f'/huetten/{self.hut.id}/book', data=data)

    def test_every_account_can_book(self):
        for number, role in enumerate((None, 'no_member')):
            self.client = self.login(self.user(f'u{number}@example.org', role=role))
            self._book()
            self.assertEqual(HutBooking.objects.count(), number + 1, role)

    def test_maximum_nights(self):
        self._book(nights=MAX_NIGHTS + 1)
        self.assertEqual(HutBooking.objects.count(), 0)
        self._book(nights=MAX_NIGHTS)
        self.assertEqual(HutBooking.objects.count(), 1)

    def test_open_bookings_per_account(self):
        for week in range(MAX_OPEN_BOOKINGS + 1):
            self.start = date.today() + timedelta(days=3 + 7 * week)
            self._book()
        self.assertEqual(HutBooking.objects.count(), MAX_OPEN_BOOKINGS)

    def test_managers_are_not_limited(self):
        self.client = self.login(self.user('g@example.org', role='guide'))
        self._book(nights=MAX_NIGHTS + 1)
        self.assertEqual(HutBooking.objects.count(), 1)

    def test_booking_works_under_lock(self):
        self._book(rooms='Lager')
        self.assertEqual(HutBooking.objects.count(), 1)
        app.redis.lock.assert_called_once()

    def test_unknown_room_is_rejected(self):
        self._book(rooms='Gibtsnicht')
        self.assertEqual(HutBooking.objects.count(), 0)

    def test_overbooking_is_rejected(self):
        self._book(places='3')
        self.assertEqual(HutBooking.objects.count(), 0)

    def test_past_date_only_for_managers(self):
        self.start = date.today() - timedelta(days=3)
        self._book()
        self.assertEqual(HutBooking.objects.count(), 0)
        self.client = self.login(self.user('g@example.org', role='guide'))
        self._book()
        self.assertEqual(HutBooking.objects.count(), 1)

    def test_open_redirect(self):
        booking = HutBooking(hut=self.hut, from_date=self.start, to_date=self.start,
                             user=User.objects.first(), places=1).save()
        guide = self.login(self.user('g@example.org', role='guide'))
        url = f'/huetten/{self.hut.id}/booking/{booking.id}/confirm'
        for target in ('https://evil.example/', '//evil.example/'):
            response = guide.post(url, data={'next': target})
            self.assertNotIn('evil.example', response.location)
        self.assertTrue(guide.post(url, data={'next': '/huetten'}).location.endswith('/huetten'))

    def test_calendar_with_absurd_year(self):
        for query in ('year=0', 'year=9999&month=12', 'year=x'):
            response = self.client.get(f'/huetten/{self.hut.id}/kalender?{query}')
            self.assertEqual(response.status_code, 200, query)


class CrashTest(AppTestCase):
    """Eingaben und Datenstaende, die frueher mit 500 endeten"""

    def test_bad_public_urls(self):
        client = app.test_client()
        self.assertEqual(client.get('/?filter_date=kaputt').status_code, 200)
        self.assertEqual(client.get('/event/details?event_id=kaputt').status_code, 404)
        self.assertEqual(client.get('/event/details').status_code, 404)
        self.assertEqual(client.get('/event/details?event_id=0123456789abcdef01234567').status_code, 404)
        self.assertEqual(client.get('/huetten/kaputt').status_code, 404)

    def test_filter_date_still_filters(self):
        event = self.event(start_date=datetime(2030, 5, 5, 8, 0), event_name='Mai-Tour')
        self.assertEqual(event.event_name, 'Mai-Tour')
        self.assertIn(b'Mai-Tour', app.test_client().get('/?filter_date=2030-05-05').data)
        self.assertNotIn(b'Mai-Tour', app.test_client().get('/?filter_date=2030-05-07').data)

    def test_anonymous_gets_login_redirect(self):
        event = self.event()
        client = app.test_client()
        gets = ['/event/admin', '/user/booking', '/change-password', f'/user/get_data/{event.id}']
        posts = ['/user/change_ticket', '/user/add_ticket', '/event/change_participants',
                 '/event/change_paidstatus', '/event/bulk_paidstatus', '/event/manage_hidden_categories',
                 '/event/finance/add_cost', '/event/finance/edit_cost',
                 '/event/finance/mark_person_paid', '/event/finance/delete_cost']
        for url in gets:
            self.assertEqual(client.get(url).status_code, 302, url)
        for url in posts:
            self.assertEqual(client.post(url, data={'event_id': event.id}).status_code, 302, url)

    def test_owner_without_role(self):
        owner = self.user('admin@example.org', global_admin=True)
        event = self.event(event_owners=[owner])
        response = app.test_client().get(f'/event/details?event_id={event.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Verantwortlicher', response.data)

    def test_event_without_end_date(self):
        event = self.event(end_date=None)
        self.assertEqual(app.test_client().get(f'/event/details?event_id={event.id}').status_code, 200)

    def test_renamed_ticket_type(self):
        event = self.event()
        self.booking(self.user(), event, ticket_name='Alter Name', confirmed=True)
        client = self.login(self.owner(event))
        for page in ('participants', 'billing', 'finance'):
            self.assertEqual(client.get(f'/event/{page}?event_id={event.id}').status_code, 200, page)

    def test_deleted_user_via_admin(self):
        admin = self.user('admin@example.org', global_admin=True)
        doomed = self.user('doomed@example.org', role='guide')
        event = self.event(event_owners=[doomed])
        hut = Hut(name='H', admins=[doomed]).save()
        self.booking(doomed, event, confirmed=True, name='Doomed')
        client = self.login(admin)
        client.post('/admin/user/delete/', data={'id': doomed.id, 'url': '/admin/user/'})
        self.assertEqual(User.objects(id=doomed.id).count(), 0)

        event.reload()
        hut.reload()
        self.assertEqual(event.event_owners, [])
        self.assertEqual(hut.admins, [])
        self.assertEqual(client.get(f'/event/details?event_id={event.id}').status_code, 200)
        response = client.get(f'/event/participants?event_id={event.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Gelöschter Account'.encode(), response.data)
        # Andere Teilnehmer und ihre Buchung sind davon nicht betroffen
        other = self.user('other@example.org')
        self.booking(other, event, ticket_id='t2')
        self.assertEqual(self.login(other).get(f'/user/booking?event_id={event.id}').status_code, 200)
        # Ticket eines geloeschten Users laesst sich noch entfernen
        response = client.post('/event/change_participants', data={
            'job': 'delete', 'ticket_id': 't1', 'event_id': event.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(Event.objects.get(id=event.id).participations), 1)

    def test_deleted_event_via_admin(self):
        booker = self.user()
        event = self.event()
        self.booking(booker, event)
        User.objects(id=booker.id).update_one(add_to_set__favorites=event)
        client = self.login(self.owner(event))
        client.post('/admin/event/delete/', data={'id': event.id, 'url': '/admin/event/'})
        self.assertEqual(Event.objects.count(), 0)
        booker.reload()
        self.assertEqual(booker.to_mongo().get('event_registrations', []), [])
        self.assertEqual(booker.to_mongo().get('favorites', []), [])
        self.assertEqual(self.login(booker).get('/user/profile').status_code, 200)

    def test_profile_with_old_dangling_event(self):
        booker = self.user()
        event = self.event()
        User.objects(id=booker.id).update_one(add_to_set__event_registrations=event)
        Event.objects(id=event.id).delete()
        self.assertEqual(self.login(booker).get('/user/profile').status_code, 200)

    def test_export_without_confirmed(self):
        event = self.event()
        client = self.login(self.owner(event))
        response = client.get(f'/event/participants/export?event_id={event.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Bucher', response.data)

    def test_export_neutralises_formulas_and_header(self):
        event = self.event(event_name='Tour\r\nX: y ä')
        self.booking(self.user(), event, confirmed=True, name='=HYPERLINK("http://evil")',
                     comment_on_ticket='@cmd', phone_on_ticket='+49 170 1')
        client = self.login(self.owner(event))
        response = client.get(f'/event/participants/export?event_id={event.id}')
        self.assertEqual(response.status_code, 200)
        text = response.data.decode()
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("'@cmd", text)
        self.assertIn('+49 170 1', text)
        self.assertNotIn("'+49", text)
        self.assertNotIn('\n', response.headers['Content-Disposition'])

    def test_extra_ticket_export_sorting(self):
        event = self.event(tickets=[Ticket(name='Bus', price=0.0, maximum_tickets=5, is_extra_ticket=True)])
        first, second = self.user('a@example.org'), self.user('b@example.org')
        self.booking(first, event, ticket_name='Bus', ticket_id='a', name='Zoe', is_extra_ticket=True)
        self.booking(second, event, ticket_name='Bus', ticket_id='b', name='Anna', is_extra_ticket=True)
        client = self.login(self.owner(event))
        text = client.get(f'/event/extra_tickets_export/Bus?event_id={event.id}').data.decode()
        self.assertLess(text.index('Anna'), text.index('Zoe'))

    def test_empty_birthdate_on_ticket(self):
        user = self.user()
        event = self.event()
        self.booking(user, event)
        response = self.login(user).post('/user/change_ticket', data={
            'event_id': event.id, 'ticket_id': 't1', 'new_name': 'N', 'new_email': 'n@example.org',
            'new_phone': '1', 'new_birthdate': '', 'new_comment': ''})
        self.assertEqual(response.status_code, 200)

    def test_nan_price_rejected(self):
        event = self.event()
        self.booking(self.user(), event)
        client = self.login(self.owner(event))
        for price in ('nan', 'inf', 'abc'):
            response = client.post('/event/change_price', data={
                'event_id': event.id, 'ticket_id': 't1', 'new_price': price})
            self.assertEqual(response.status_code, 400, price)
            response = client.post('/event/bulk_change_price', data={
                'event_id': event.id, 'ticket_name': 'Teilnahme', 'new_price': price})
            self.assertEqual(response.status_code, 400, price)
        client.post('/event/finance/add_cost', data={'event_id': event.id, 'name': 'x', 'price': 'nan'})
        self.assertEqual(Event.objects.get(id=event.id).costs[0].price, 0)

    def test_unknown_ticket_id(self):
        event = self.event()
        client = self.login(self.owner(event))
        for url in ('/event/change_participants', '/event/change_paidstatus'):
            response = client.post(url, data={'job': 'paid', 'ticket_id': 'x', 'event_id': event.id})
            self.assertEqual(response.status_code, 404, url)

    def test_cost_index(self):
        event = self.event(costs=[EventCost(name='a', price=1.0), EventCost(name='b', price=2.0)])
        client = self.login(self.owner(event))
        client.post('/event/finance/delete_cost', data={'event_id': event.id, 'index': '-1'})
        self.assertEqual(len(Event.objects.get(id=event.id).costs), 2)
        response = client.post('/event/finance/edit_cost', data={
            'event_id': event.id, 'index': '0', 'name': 'a', 'date': 'kaputt'})
        self.assertEqual(response.status_code, 302)
        client.post('/event/finance/delete_cost', data={'event_id': event.id, 'index': '0'})
        self.assertEqual([x.name for x in Event.objects.get(id=event.id).costs], ['b'])

    def test_booking_survives_mail_problem(self):
        event = self.event(event_owners=[self.user('g@example.org', role='guide')])
        client = self.login(self.user())
        with mock.patch('application.events.views.send_email', side_effect=RuntimeError):
            response = client.post(f'/event/details?event_id={event.id}', data={
                'comment': '', 'ticket_Teilnahme': '1', 'submit': 'Anmelden'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(Event.objects.get(id=event.id).participations), 1)


class BookingLogicTest(AppTestCase):
    """Fachlich falsche Ergebnisse"""

    def _book(self, client, event, count=1):
        return client.post(f'/event/details?event_id={event.id}', data={
            'comment': '', 'ticket_Teilnahme': str(count), 'submit': 'Anmelden'})

    def test_maximum_counts_over_all_bookings_of_an_account(self):
        event = self.event(places=10)
        client = self.login(self.user())
        self.assertEqual(self._book(client, event, 2).status_code, 302)
        self.assertEqual(self._book(client, event, 1).status_code, 200)
        self.assertEqual(sum(len(x.tickets) for x in Event.objects.get(id=event.id).participations), 2)

    def test_second_booking_up_to_maximum(self):
        event = self.event(places=10)
        client = self.login(self.user())
        self._book(client, event, 1)
        self.assertEqual(self._book(client, event, 1).status_code, 302)
        self.assertEqual(sum(len(x.tickets) for x in Event.objects.get(id=event.id).participations), 2)

    def test_waitlist_does_not_take_places(self):
        event = self.event()
        self._book(self.login(self.user('group@example.org')), event, 3 if False else 2)
        Event.objects(id=event.id).update_one(set__tickets__0__maximum_tickets=3)
        event.reload()
        event.participations[0].tickets[0].waitinglist = True
        event.participations[0].tickets[1].waitinglist = True
        event.save()
        self._book(self.login(self.user('single@example.org')), event, 1)
        tickets = Event.objects.get(id=event.id).participations[1].tickets
        self.assertFalse(tickets[0].waitinglist)

    def test_numbers_do_not_count_waitlist_twice(self):
        event = self.event(places=5, participations=[EventParticipation(tickets=[
            OwnedTicket(ticket_name='Teilnahme', confirmed=True),
            OwnedTicket(ticket_name='Teilnahme', confirmed=False, waitinglist=True),
            OwnedTicket(ticket_name='Teilnahme', confirmed=False),
        ])])
        numbers = event.get_numbers()
        self.assertEqual((numbers['confirmed'], numbers['wait_for_confirm'], numbers['waitlist']), (1, 1, 1))

    def test_free_places_on_details_page(self):
        event = self.event(places=5, participations=[EventParticipation(tickets=[
            OwnedTicket(ticket_name='Teilnahme', confirmed=True),
            OwnedTicket(ticket_name='Teilnahme', confirmed=False),
        ], user=self.user())])
        html = app.test_client().get(f'/event/details?event_id={event.id}').data.decode()
        match = re.search(r'Freie Plätze insgesamt</th>\s*<td>(\d+)</td>', html)
        self.assertEqual(match.group(1), '3')

    def test_custom_price_zero(self):
        user = self.user()
        event = self.event()
        self.booking(user, event, custom_price=0.0)
        html = self.login(user).get(f'/user/booking?event_id={event.id}').data.decode()
        self.assertNotIn('10,00', html)

    def test_guide_added_ticket_sets_back_reference(self):
        member = self.user()
        event = self.event()
        guide = self.login(self.owner(event))
        response = guide.post('/user/add_ticket', data={
            'event_id': event.id, 'user_id': member.id, 'ticket_type': 'Teilnahme', 'new_name': '',
            'new_email': '', 'new_phone': '', 'new_birthdate': '', 'new_comment': ''})
        self.assertEqual(response.json['success'], True)
        self.assertTrue(User.objects.get(id=member.id).participate_event(event.id))

    def test_second_booking_keeps_back_reference(self):
        user = self.user()
        event = self.event()
        self.booking(user, event, ticket_id='a')
        self.booking(user, event, ticket_id='b')
        event.reload()
        event.delete_ticket('a')
        event.save()
        self.assertTrue(User.objects.get(id=user.id).participate_event(event.id))
        event.delete_ticket('b')
        event.save()
        self.assertFalse(User.objects.get(id=user.id).participate_event(event.id))

    def test_favorite_toggle(self):
        user = self.user()
        event = self.event()
        client = self.login(user)
        self.assertTrue(client.post('/event/favorite', data={'event_id': event.id}).json['favorite'])
        self.assertTrue(User.objects.get(id=user.id).is_favorite(event.id))
        self.assertFalse(client.post('/event/favorite', data={'event_id': event.id}).json['favorite'])
        self.assertFalse(User.objects.get(id=user.id).is_favorite(event.id))

    def test_difficulty_icons(self):
        self.assertEqual(difficult_to_icon('keine', 1), '□□□□□')
        self.assertEqual(difficult_to_icon('sehr schwer', 1), '■■■■■')
        self.assertEqual(difficult_to_icon('leicht', 2), '●●○○○')

    def test_event_form_ignores_foreign_fields(self):
        event = self.event()
        guide = self.owner(event)
        self.booking(self.user(), event)
        client = self.login(guide)
        client.post(f'/event/admin?event_id={event.id}', data={
            'event_name': 'Neu', 'event_teaser': 't', 'event_category': 'None', 'event_type': 'guided',
            'places': '5', 'min_places': '0', 'start_date': '2030-01-01T10:00', 'difficulty': 'keine', 'shape': 'keine',
            'length_h': '0', 'length_km': '0', 'altitude_difference': '0',
            'tickets-1-name': 'Teilnahme', 'tickets-1-maximum_tickets': '5', 'tickets-1-price': '1',
            'hidden_categories': 'x', 'participations': '', 'event_owners': ''})
        event.reload()
        self.assertEqual(event.event_name, 'Neu')
        self.assertEqual(len(event.participations), 1)
        self.assertEqual(list(event.hidden_categories), [])


class CommentLengthTest(AppTestCase):
    """Kommentare landen in jedem Ticket im Event-Dokument und sind begrenzt"""

    def _book(self, client, event, comment):
        return client.post(f'/event/details?event_id={event.id}', data={
            'comment': comment, 'ticket_Teilnahme': '1', 'submit': 'Anmelden'})

    def test_booking_rejects_long_comment(self):
        event = self.event()
        client = self.login(self.user())
        response = self._book(client, event, 'x' * (COMMENT_MAX + 1))
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'Höchstens {COMMENT_MAX} Zeichen', response.get_data(as_text=True))
        self.assertEqual(Event.objects.get(id=event.id).participations, [])
        self.assertEqual(self._book(client, event, 'x' * COMMENT_MAX).status_code, 302)
        self.assertEqual(len(Event.objects.get(id=event.id).participations), 1)

    def test_change_ticket_rejects_long_comment(self):
        user = self.user()
        event = self.event()
        self.booking(user, event, comment_on_ticket='kurz')
        data = {'event_id': str(event.id), 'ticket_id': 't1', 'new_name': 'Vor Nach',
                'new_email': user.email, 'new_phone': '1', 'new_birthdate': ''}
        client = self.login(user)
        response = client.post('/user/change_ticket',
                               data={**data, 'new_comment': 'x' * (COMMENT_MAX + 1)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Event.objects.get(id=event.id).participations[0].tickets[0]
                         .comment_on_ticket, 'kurz')

    def test_existing_long_comment_stays_editable(self):
        user = self.user()
        event = self.event()
        old = 'x' * (COMMENT_MAX + 50)
        self.booking(user, event, comment_on_ticket=old)
        client = self.login(user)
        page = client.get(f'/user/booking?event_id={event.id}')
        self.assertIn(old, page.get_data(as_text=True))
        self.assertIn(f'maxlength="{COMMENT_MAX}"', page.get_data(as_text=True))
        response = client.post('/user/change_ticket', data={
            'event_id': str(event.id), 'ticket_id': 't1', 'new_name': 'Neu Name',
            'new_email': user.email, 'new_phone': '1', 'new_birthdate': '', 'new_comment': old})
        self.assertEqual(response.status_code, 200)
        ticket = Event.objects.get(id=event.id).participations[0].tickets[0]
        self.assertEqual((ticket.name_on_ticket, ticket.comment_on_ticket), ('Neu Name', old))

    def test_guide_add_ticket_rejects_long_comment(self):
        user = self.user()
        event = self.event()
        client = self.login(self.owner(event))
        response = client.post('/user/add_ticket', data={
            'event_id': str(event.id), 'user_id': str(user.id), 'ticket_type': 'Teilnahme',
            'new_name': '', 'new_email': '', 'new_phone': '', 'new_birthdate': '',
            'new_comment': 'x' * (COMMENT_MAX + 1)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Event.objects.get(id=event.id).participations, [])


class InputLengthTest(AppTestCase):
    """Weitere Freitexte bei Buchung und Registrierung sind begrenzt"""

    def _change(self, client, event, user, **form):
        data = {'event_id': str(event.id), 'ticket_id': 't1', 'new_name': 'Vor Nach',
                'new_email': user.email, 'new_phone': '1', 'new_birthdate': '',
                'new_comment': '', **form}
        return client.post('/user/change_ticket', data=data)

    def test_booking_rejects_long_custom_field(self):
        event = self.event(custom_fields=[CustomFieldDefintion(field_name='Allergien')])
        client = self.login(self.user())
        data = {'comment': '', 'ticket_Teilnahme': '1', 'submit': 'Anmelden'}
        response = client.post(f'/event/details?event_id={event.id}',
                               data={**data, 'custom_0': 'x' * (CUSTOM_FIELD_MAX + 1)})
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'Höchstens {CUSTOM_FIELD_MAX} Zeichen erlaubt', page)
        self.assertIn('aria-describedby="custom_0-error"', page)
        self.assertIn('id="custom_0-error"', page)
        self.assertEqual(Event.objects.get(id=event.id).participations, [])
        response = client.post(f'/event/details?event_id={event.id}',
                               data={**data, 'custom_0': 'x' * CUSTOM_FIELD_MAX})
        self.assertEqual(response.status_code, 302)

    def test_comment_error_keeps_hint(self):
        event = self.event()
        client = self.login(self.user())
        page = client.post(f'/event/details?event_id={event.id}', data={
            'comment': 'x' * (COMMENT_MAX + 1), 'ticket_Teilnahme': '1',
            'submit': 'Anmelden'}).get_data(as_text=True)
        self.assertIn('aria-describedby="comment_hint comment-error"', page)
        self.assertIn('aria-invalid="true"', page)

    def test_change_ticket_rejects_long_contact_data(self):
        user = self.user()
        event = self.event()
        self.booking(user, event)
        client = self.login(user)
        for field, limit in (('new_name', TICKET_NAME_MAX), ('new_email', EMAIL_MAX),
                             ('new_phone', PHONE_MAX)):
            response = self._change(client, event, user, **{field: 'x' * (limit + 1)})
            self.assertEqual(response.status_code, 400, field)
            self.assertIn(f'höchstens {limit} Zeichen', response.get_json()['error'])
        self.assertEqual(Event.objects.get(id=event.id).participations[0].tickets[0]
                         .name_on_ticket, 'Vor Nach')
        self.assertEqual(self._change(client, event, user, new_name='y' * TICKET_NAME_MAX)
                         .status_code, 200)

    def test_existing_long_name_stays_editable(self):
        user = self.user()
        event = self.event()
        old = 'x' * (TICKET_NAME_MAX + 20)
        self.booking(user, event, name=old)
        client = self.login(user)
        self.assertIn(old, client.get(f'/user/booking?event_id={event.id}').get_data(as_text=True))
        response = self._change(client, event, user, new_name=old, new_comment='neu')
        self.assertEqual(response.status_code, 200)
        ticket = Event.objects.get(id=event.id).participations[0].tickets[0]
        self.assertEqual((ticket.name_on_ticket, ticket.comment_on_ticket), (old, 'neu'))

    def test_guide_add_ticket_rejects_long_email(self):
        user = self.user()
        event = self.event()
        client = self.login(self.owner(event))
        response = client.post('/user/add_ticket', data={
            'event_id': str(event.id), 'user_id': str(user.id), 'ticket_type': 'Teilnahme',
            'new_name': '', 'new_email': 'x' * (EMAIL_MAX + 1), 'new_phone': '',
            'new_birthdate': '', 'new_comment': ''})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Event.objects.get(id=event.id).participations, [])

    def test_registration_rejects_long_contact_data(self):
        long_email = 'a' * 64 + '@' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 63 + '.org'
        for field, value in (('email', long_email), ('phone', '1' * (PHONE_MAX + 1)),
                             ('club_id', '1' * 51)):
            response = app.test_client().post('/user/create', data={**REGISTRATION, field: value})
            self.assertEqual(response.status_code, 200, field)
            self.assertIn(f'id="{field}-error"', response.get_data(as_text=True))
        self.assertEqual(User.objects.count(), 0)
        page = app.test_client().get('/user/create').get_data(as_text=True)
        self.assertIn(f'maxlength="{EMAIL_MAX}"', page)

    def test_hut_booking_rejects_long_comment(self):
        hut = Hut(name='H', rooms=[HutRoom(name='Lager', places=2)]).save()
        client = self.login(self.user())
        start = date.today() + timedelta(days=3)
        data = {'from_date': start.isoformat(), 'to_date': (start + timedelta(days=1)).isoformat(),
                'places': '1'}
        client.post(f'/huetten/{hut.id}/book', data={**data, 'comment': 'x' * (COMMENT_MAX + 1)})
        self.assertEqual(HutBooking.objects.count(), 0)
        client.post(f'/huetten/{hut.id}/book', data={**data, 'comment': 'x' * COMMENT_MAX})
        self.assertEqual(HutBooking.objects.count(), 1)
        page = client.get(f'/huetten/{hut.id}', query_string=data).get_data(as_text=True)
        self.assertIn(f'maxlength="{COMMENT_MAX}"', page)


class TourOwnerTest(AppTestCase):
    """Trainer und Tourenbegleiter verwalten nur Touren, bei denen sie Verantwortliche sind"""

    FORM = {
        'event_name': 'Neu', 'event_teaser': 't', 'event_category': 'None', 'event_type': 'guided',
        'places': '5', 'min_places': '0', 'start_date': '2030-01-01T10:00', 'difficulty': 'keine',
        'shape': 'keine', 'length_h': '0', 'length_km': '0', 'altitude_difference': '0',
        'tickets-1-name': 'Teilnahme', 'tickets-1-maximum_tickets': '5', 'tickets-1-price': '1',
    }

    def setUp(self):
        super().setUp()
        self.member = self.user('m@example.org', role='member')
        self.event_ = self.event(costs=[EventCost(name='a', price=1.0)])
        self.booking(self.member, self.event_, confirmed=True, phone_on_ticket='0170 999')

    def assert_denied(self, client, event):
        """Jede Verwaltungsseite der Tour ist gesperrt und aendert nichts"""
        eid = str(event.id)
        gets = [f'/event/admin?event_id={eid}', f'/event/participants/export?event_id={eid}',
                f'/event/billing?event_id={eid}', f'/event/finance?event_id={eid}',
                f'/event/extra_tickets/Bus?event_id={eid}',
                f'/event/extra_tickets_export/Bus?event_id={eid}']
        for url in gets:
            self.assertEqual(client.get(url).status_code, 403, url)
        ticket = {'event_id': eid, 'ticket_id': 't1'}
        posts = [
            ('/event/change_participants', {**ticket, 'job': 'delete'}),
            ('/event/change_paidstatus', {**ticket, 'job': 'paid'}),
            ('/event/bulk_paidstatus', {'event_id': eid, 'job': 'all_paid'}),
            ('/event/change_price', {**ticket, 'new_price': '0'}),
            ('/event/bulk_change_price', {'event_id': eid, 'ticket_name': 'Teilnahme', 'new_price': '0'}),
            ('/event/manage_hidden_categories', {'event_id': eid, 'action': 'hide_category',
                                                 'category': 'x'}),
            ('/event/finance/add_cost', {'event_id': eid, 'name': 'b', 'price': '5'}),
            ('/event/finance/edit_cost', {'event_id': eid, 'index': '0', 'name': 'z'}),
            ('/event/finance/mark_person_paid', {'event_id': eid, 'job': 'paid'}),
            ('/event/finance/delete_cost', {'event_id': eid, 'index': '0'}),
            ('/user/add_ticket', {'event_id': eid, 'user_id': str(self.member.id),
                                  'ticket_type': 'Teilnahme', 'new_name': '', 'new_email': '',
                                  'new_phone': '', 'new_birthdate': '', 'new_comment': ''}),
            ('/event/admin?event_id=' + eid, self.FORM),
            ('/event/delete', {'event_id': eid}),
        ]
        for url, data in posts:
            self.assertEqual(client.post(url, data=data).status_code, 403, url)
        # Fremde Buchungen bleiben unsichtbar und unveraendert
        client.post('/user/change_ticket', data={
            **ticket, 'target_user_id': str(self.member.id), 'new_name': 'X', 'new_email': '',
            'new_phone': '', 'new_birthdate': '', 'new_comment': ''})
        page = client.get(f'/user/booking?event_id={eid}&user_id={self.member.id}').data
        self.assertNotIn(b'0170 999', page)
        # Flask-Admin: weder sichtbar noch loeschbar
        self.assertNotIn(eid.encode(), client.get('/admin/event/').data)
        client.post('/admin/event/delete/', data={'id': eid, 'url': '/admin/event/'})
        event.reload()
        self.assertEqual(event.event_name, 'Tour')
        self.assertEqual(len(event.participations), 1)
        ticket = event.participations[0].tickets[0]
        self.assertEqual((ticket.name_on_ticket, ticket.is_paid, ticket.custom_price),
                         ('Vor Nach', False, None))
        self.assertEqual([(x.name, x.is_paid) for x in event.costs], [('a', False)])
        self.assertEqual(list(event.hidden_categories), [])

    def test_own_tour_allowed(self):
        client = self.login(self.owner(self.event_))
        eid = self.event_.id
        for url in ('admin', 'billing', 'finance', 'participants/export'):
            self.assertEqual(client.get(f'/event/{url}?event_id={eid}').status_code, 200, url)
        self.assertIn(b'0170 999', client.get(f'/event/participants?event_id={eid}').data)
        response = client.post('/event/change_price', data={
            'event_id': eid, 'ticket_id': 't1', 'new_price': '3'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.objects.get(id=eid).participations[0].tickets[0].custom_price, 3)
        self.assertIn(str(eid).encode(), client.get('/admin/event/').data)

    def test_attendant_on_own_tour_allowed(self):
        client = self.login(self.owner(self.event_, 'a@example.org', role='attendant'))
        self.assertEqual(client.get(f'/event/admin?event_id={self.event_.id}').status_code, 200)

    def test_foreign_tour_forbidden_for_guide(self):
        self.owner(self.event_, 'other@example.org')
        self.assert_denied(self.login(self.user('g@example.org', role='guide')), self.event_)

    def test_foreign_tour_forbidden_for_attendant(self):
        self.owner(self.event_, 'other@example.org')
        self.assert_denied(self.login(self.user('a@example.org', role='attendant')), self.event_)

    def test_tour_without_leader_only_for_admins(self):
        self.assert_denied(self.login(self.user('g@example.org', role='guide')), self.event_)
        admin = self.login(self.user('admin@example.org', global_admin=True))
        self.assertEqual(admin.get(f'/event/admin?event_id={self.event_.id}').status_code, 200)

    def test_global_admin_manages_foreign_tour(self):
        self.owner(self.event_, 'other@example.org')
        client = self.login(self.user('admin@example.org', global_admin=True))
        eid = self.event_.id
        for url in ('admin', 'billing', 'finance', 'participants/export'):
            self.assertEqual(client.get(f'/event/{url}?event_id={eid}').status_code, 200, url)
        self.assertIn(str(eid).encode(), client.get('/admin/event/').data)
        self.assertEqual(client.post('/event/delete', data={'event_id': eid}).status_code, 302)
        self.assertEqual(Event.objects(id=eid).count(), 0)

    def test_foreign_guide_sees_list_only_as_participant(self):
        guide = self.user('g@example.org', role='guide')
        client = self.login(guide)
        eid = self.event_.id
        self.assertEqual(client.get(f'/event/participants?event_id={eid}').status_code, 403)
        self.booking(guide, self.event_, ticket_id='t2', confirmed=True)
        page = client.get(f'/event/participants?event_id={eid}').data
        self.assertNotIn(b'0170 999', page)
        self.assertNotIn(b'participants/export', page)

    def test_creator_becomes_leader(self):
        guide = self.user('g@example.org', role='guide')
        client = self.login(guide)
        self.assertEqual(client.post('/create', data=self.FORM).status_code, 302)
        event = Event.objects.get(event_name='Neu')
        self.assertEqual([x.id for x in event.event_owners], [guide.id])
        self.assertEqual(client.get(f'/event/admin?event_id={event.id}').status_code, 200)


class SecurityHeaderTest(AppTestCase):
    """Grundlegende Sicherheits-Header auf Seiten und statischen Dateien"""

    def test_headers_set(self):
        client = app.test_client()
        for url in ('/', '/static/js/main.js'):
            response = client.get(url)
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn("object-src 'none'", response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['Referrer-Policy'],
                             'strict-origin-when-cross-origin')
            response.close()


class SecretKeyTest(unittest.TestCase):
    """Fail closed: ohne SECRET_KEY startet die Prod-Config nicht"""

    def test_refuses_to_start(self):
        import os
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if k != 'SECRET_KEY'}
        env['config'] = 'prod_compose'
        result = subprocess.run([sys.executable, '-c', 'import application'], env=env,
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SECRET_KEY', result.stderr)


class TwoFactorTest(AppTestCase):
    """Freiwillige Zwei-Faktor-Anmeldung per TOTP"""

    def setUp(self):
        super().setUp()
        storage = MemoryStorage()
        patcher = mock.patch.multiple(limiter, _storage=storage,
                                      _limiter=FixedWindowRateLimiter(storage), enabled=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ip_counter = 0

    def _ip(self):
        """Jede Anfrage von einer anderen IP, damit nur die Kontosperre greift"""
        self.ip_counter += 1
        return {'REMOTE_ADDR': f'198.51.100.{self.ip_counter}'}

    @staticmethod
    def _code(secret, offset=30):
        """Code des naechsten Zeitfensters: der aktuelle ist nach der Einrichtung verbraucht"""
        return pyotp.TOTP(secret).at(time.time() + offset)

    def _enable(self, user):
        """Einrichtung ueber die Seiten, gibt Geheimnis und Wiederherstellungscodes zurueck"""
        client = self.login(user)
        self.assertEqual(client.post('/user/2fa/setup').status_code, 302)
        page = client.get('/user/2fa/setup')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'<svg', page.data)
        user.reload()
        with app.app_context():
            secret = tfa.decrypt_secret(user.tfa_pending_secret)
        self.assertNotIn(secret, user.tfa_pending_secret)
        self.assertIn(' '.join(secret[i:i + 4] for i in range(0, len(secret), 4)).encode(),
                      page.data)

        wrong = client.post('/user/2fa/confirm', data={'code': '000000'})
        self.assertIn('Der Code passt nicht'.encode(), wrong.data)
        self.assertFalse(User.objects.get(id=user.id).tfa_enabled)

        done = client.post('/user/2fa/confirm', data={'code': pyotp.TOTP(secret).now()})
        self.assertEqual(done.headers['Cache-Control'], 'no-store')
        codes = re.findall(r'<code[^>]*>([0-9a-f]{5}-[0-9a-f]{5})</code>', done.data.decode())
        self.assertEqual(len(codes), 10)
        user.reload()
        self.assertTrue(user.tfa_enabled)
        self.assertFalse(set(codes) & set(user.tfa_recovery_codes))
        return secret, codes

    def _password_step(self, user, password=PASSWORD, path='/login'):
        client = app.test_client()
        response = client.post(path, data={'login_email': user.email, 'password': password,
                                           'login_submit': 'Anmelden'},
                               environ_base=self._ip())
        return client, response

    def _code_step(self, client, **data):
        return client.post('/login/2fa', data=data, environ_base=self._ip())

    def test_login_without_2fa_is_unchanged(self):
        _client, response = self._password_step(self.user())
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('/login/2fa', response.location)

    def test_login_needs_code(self):
        user = self.user()
        secret, _codes = self._enable(user)
        client, response = self._password_step(user)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/2fa', response.location)
        # Nach dem Passwort allein ist niemand eingeloggt
        self.assertEqual(client.get('/user/profile').status_code, 302)
        page = client.get('/login/2fa')
        self.assertIn(b'autocomplete="one-time-code"', page.data)
        self.assertIn(b'inputmode="numeric"', page.data)

        wrong = self._code_step(client, code='123456')
        self.assertEqual(wrong.status_code, 200)
        self.assertIn('Der Code ist falsch'.encode(), wrong.data)
        self.assertIn(b'aria-invalid="true"', wrong.data)
        self.assertEqual(client.get('/user/profile').status_code, 302)

        self.assertEqual(self._code_step(client, code=self._code(secret)).status_code, 302)
        self.assertEqual(client.get('/user/profile').status_code, 200)

    def test_code_works_only_once(self):
        user = self.user()
        secret, _codes = self._enable(user)
        code = self._code(secret)
        client, _ = self._password_step(user)
        self.assertEqual(self._code_step(client, code=code).status_code, 302)
        other, _ = self._password_step(user)
        self.assertEqual(self._code_step(other, code=code).status_code, 200)
        self.assertEqual(other.get('/user/profile').status_code, 302)

    def test_code_step_needs_password_step(self):
        user = self.user()
        secret, _codes = self._enable(user)
        client = app.test_client()
        response = self._code_step(client, code=self._code(secret))
        self.assertIn('/login', response.location)
        self.assertEqual(client.get('/user/profile').status_code, 302)

    def test_code_step_expires(self):
        user = self.user()
        secret, _codes = self._enable(user)
        client, _ = self._password_step(user)
        later = time.time() + 6 * 60
        with mock.patch('application.auth.views.time.time', return_value=later):
            response = self._code_step(client, code=self._code(secret))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get('/user/profile').status_code, 302)

    def test_recovery_code_works_once(self):
        user = self.user()
        _secret, codes = self._enable(user)
        client, _ = self._password_step(user)
        response = self._code_step(client, recovery_code=codes[0].upper())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get('/user/profile').status_code, 200)
        self.assertEqual(len(User.objects.get(id=user.id).tfa_recovery_codes), 9)

        other, _ = self._password_step(user)
        self.assertEqual(self._code_step(other, recovery_code=codes[0]).status_code, 200)
        self.assertEqual(other.get('/user/profile').status_code, 302)
        self.assertEqual(self._code_step(other, recovery_code=codes[1]).status_code, 302)

    def test_event_page_login_continues_to_event(self):
        user = self.user()
        secret, _codes = self._enable(user)
        event = self.event()
        client, response = self._password_step(user, path=f'/event/details?event_id={event.id}')
        self.assertIn('/login/2fa', response.location)
        response = self._code_step(client, code=self._code(secret))
        self.assertIn(f'/event/details?event_id={event.id}', response.location)

    def test_disable_needs_password_and_code(self):
        user = self.user()
        secret, codes = self._enable(user)
        client, _ = self._password_step(user)
        self._code_step(client, code=self._code(secret))
        response = client.post('/user/2fa/disable', data={'password': 'falsch', 'code': codes[0]},
                               environ_base=self._ip())
        self.assertIn('Passwort oder Code ist falsch'.encode(), response.data)
        # Das falsche Passwort hat keinen Code verbraucht
        self.assertEqual(len(User.objects.get(id=user.id).tfa_recovery_codes), 10)
        client.post('/user/2fa/disable', data={'password': PASSWORD, 'code': '000000'},
                    environ_base=self._ip())
        self.assertTrue(User.objects.get(id=user.id).tfa_enabled)
        response = client.post('/user/2fa/disable', data={'password': PASSWORD, 'code': codes[0]},
                               environ_base=self._ip())
        self.assertEqual(response.status_code, 302)
        user.reload()
        self.assertFalse(user.tfa_enabled)
        self.assertFalse(user.tfa_recovery_codes)
        _client, response = self._password_step(user)
        self.assertNotIn('/login/2fa', response.location)

    def test_global_admin_resets_2fa(self):
        user = self.user()
        self._enable(user)
        admin = self.login(self.user('admin@example.org', global_admin=True))
        response = admin.post('/admin/user/action/', data={'action': 'reset_tfa',
                                                           'rowid': [str(user.id)]})
        self.assertEqual(response.status_code, 302)
        user.reload()
        self.assertFalse(user.tfa_enabled)
        self.assertFalse(user.tfa_recovery_codes)

    def test_member_admin_cannot_reset_2fa(self):
        user = self.user()
        self._enable(user)
        admin = self.login(self.user('admin@example.org', admin=True))
        admin.post('/admin/user/action/', data={'action': 'reset_tfa', 'rowid': [str(user.id)]})
        admin.post(f'/admin/members/edit/?id={user.id}', data={
            'email': user.email, 'first_name': 'Vor', 'last_name': 'Nach', 'tfa_secret': ''})
        self.assertTrue(User.objects.get(id=user.id).tfa_enabled)

    def test_wrong_codes_lock_account(self):
        user = self.user()
        secret, _codes = self._enable(user)
        client, _ = self._password_step(user)
        for _ in range(10):
            self.assertEqual(self._code_step(client, code='000000').status_code, 200)
        locked = self._code_step(client, code=self._code(secret))
        self.assertIn('Zu viele Fehlversuche'.encode(), locked.data)
        self.assertEqual(client.get('/user/profile').status_code, 302)
        # Auch ein neuer Anlauf mit dem richtigen Passwort ist gesperrt
        _client, response = self._password_step(user)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Zu viele Fehlversuche'.encode(), response.data)

    def test_reset_link_does_not_skip_2fa(self):
        user = self.user()
        self._enable(user)
        with app.app_context():
            token = User.objects.get(id=user.id).generate_token()
        client = app.test_client()
        response = client.post(f'/reset-password/{token}', data={
            'password': NEW_PASSWORD, 'password_repeat': NEW_PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(id=user.id).check_password(NEW_PASSWORD))
        self.assertEqual(client.get('/user/profile').status_code, 302)

    def test_admin_enforcement_flag(self):
        admin = self.login(self.user('admin@example.org', global_admin=True))
        member = self.login(self.user())
        self.assertEqual(admin.get('/user/profile').status_code, 200)
        app.config['TFA_REQUIRED_FOR_ADMINS'] = True
        self.addCleanup(app.config.update, TFA_REQUIRED_FOR_ADMINS=False)
        response = admin.get('/user/profile')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/user/2fa', response.location)
        self.assertEqual(admin.get('/user/2fa').status_code, 200)
        self.assertEqual(member.get('/user/profile').status_code, 200)
