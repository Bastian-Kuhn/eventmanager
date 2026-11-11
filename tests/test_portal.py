"""
Portal: Sektionen registrieren sich, verwalten Einstellungen und Abrechnung.
Stripe ist gemockt.
Ausfuehren: .venv/bin/python -m unittest tests.test_portal (mongomock)
"""
import io
import unittest
from datetime import datetime, timedelta
from unittest import mock

import mongoengine
import mongomock
from mongomock.gridfs import enable_gridfs_integration
from PIL import Image

from application import app, limiter
from application.events.models import Event
from application.models.config import Config
from application.models.section import Section
from application.models.user import Membership, User
from application.modules import billing
from tests.test_security import PASSWORD

PORTAL = 'https://sektion.example.org'
SETTINGS = {
    'PORTAL_HOSTNAME': 'sektion.example.org', 'SECTION_BASE_DOMAIN': 'example.org',
    'TRIAL_DAYS': 30, 'SUBSCRIPTIONS_ENABLED': False, 'STRIPE_SECRET_KEY': 'sk_test',
    'STRIPE_WEBHOOK_SECRET': 'whsec', 'STRIPE_PRICE_SECTION': 'price_1',
    'PLATFORM_MAIL_SERVER': 'smtp.example.org', 'PLATFORM_MAIL_SENDER': 'noreply@example.org',
}
NEW_SECTION = {'section_name': 'Sektion Neu', 'slug': 'neu', 'member_count': '900',
               'first_name': 'Ada', 'last_name': 'Admin', 'email': 'Ada@Example.org',
               'password': PASSWORD, 'password_repeat': PASSWORD, 'submit': 'Sektion registrieren'}


def setUpModule():
    enable_gridfs_integration()
    mongoengine.disconnect()
    mongoengine.connect('test', mongo_client_class=mongomock.MongoClient)


def tearDownModule():
    mongoengine.disconnect()


class PortalTest(unittest.TestCase):
    """Portal unter sektion.example.org, Sektionen unter <kurzname>.example.org"""

    def setUp(self):
        for model in (User, Section, Config, Event):
            model.drop_collection()
        app.redis = mock.MagicMock()
        limiter.enabled = False
        old = {key: app.config.get(key) for key in SETTINGS}
        app.config.update(WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=False, **SETTINGS)
        self.addCleanup(app.config.update, old)
        self.dav = Section(slug='dav', name='DAV', hostnames=['touren.example.org'],
                           is_default=True).save()

    @staticmethod
    def user(email, *memberships, **kwargs):
        user = User(email=email, first_name='Vor', last_name='Nach', **kwargs)
        user.memberships = [Membership(section=section, **values) for section, values in memberships]
        user.set_password(PASSWORD)
        return user.save()

    @staticmethod
    def login(user, host=PORTAL):
        client = app.test_client()
        response = client.post('/login', base_url=host, data={
            'login_email': user.email, 'password': PASSWORD, 'login_submit': 'Anmelden'})
        assert response.status_code == 302, response.status_code
        return client

    def register(self, client=None, **data):
        client = client or app.test_client()
        with mock.patch('application.modules.email.Thread') as thread:
            response = client.post('/portal/registrieren', base_url=PORTAL, data={**NEW_SECTION, **data})
        return response, thread

    # Portal-Host

    def test_portal_has_only_its_pages(self):
        client = app.test_client()
        self.assertEqual(client.get('/', base_url=PORTAL).headers['Location'], '/portal/')
        self.assertEqual(client.get('/portal/', base_url=PORTAL).status_code, 200)
        self.assertEqual(client.get('/huetten', base_url=PORTAL).status_code, 404)
        self.assertEqual(client.get('/admin/', base_url=PORTAL).status_code, 404)
        self.assertEqual(client.get('/login', base_url=PORTAL).status_code, 200)
        self.assertEqual(client.get('/portal/', base_url='https://touren.example.org').status_code, 200)

    # Registrieren

    def test_register_creates_section_admin_and_trial(self):
        response, thread = self.register()
        self.assertEqual(response.status_code, 302)
        section = Section.objects.get(slug='neu')
        self.assertEqual(section.hostnames, ['neu.example.org'])
        self.assertEqual(section.member_count, 900)
        self.assertAlmostEqual(section.trial_until, datetime.now() + timedelta(days=30),
                               delta=timedelta(minutes=1))
        admin = User.objects.get(email='ada@example.org')
        self.assertTrue(admin.email_unconfirmed)
        entry = admin.membership(section)
        self.assertTrue(entry.admin)
        self.assertEqual(entry.role, 'guide')
        message = thread.call_args.kwargs['args'][1]
        self.assertEqual(message.sender, 'Tourenanmeldung <noreply@example.org>')
        self.assertIn('https://sektion.example.org/user/confirm/', message.html)
        page = app.test_client().get('/login', base_url='https://neu.example.org')
        self.assertIn('site-brand" href="/">Sektion Neu<', page.get_data(as_text=True))

    def test_register_refuses_existing_account(self):
        self.user('ada@example.org')
        response, _ = self.register()
        self.assertEqual(response.status_code, 200)
        self.assertIn('erst an', response.get_data(as_text=True).replace('zuerst an', 'erst an'))
        self.assertEqual(Section.objects(slug='neu').count(), 0)

    def test_register_refuses_reserved_and_taken_slugs(self):
        for slug in ('www', 'dav', 'Neu Sektion', 'ab'):
            response, _ = self.register(slug=slug)
            self.assertEqual(response.status_code, 200, slug)
        self.assertEqual(Section.objects.count(), 1)
        self.assertEqual(User.objects.count(), 0)

    def test_register_while_logged_in_uses_own_account(self):
        member = self.user('m@example.org', (self.dav, {'role': 'member'}))
        client = self.login(member)
        response, _ = self.register(client, section_name='Zweite', slug='zweite')
        self.assertEqual(response.status_code, 302)
        member.reload()
        section = Section.objects.get(slug='zweite')
        self.assertTrue(member.membership(section).admin)
        self.assertEqual(member.role_in(self.dav), 'member')
        self.assertEqual(User.objects.count(), 1)

    # Einstellungen

    def test_settings_only_for_admins_of_the_section(self):
        self.register()
        section = Section.objects.get(slug='neu')
        stranger = self.user('s@example.org', (self.dav, {'admin': True}))
        client = self.login(stranger)
        self.assertEqual(client.get('/portal/sektion/neu', base_url=PORTAL).status_code, 404)
        self.assertEqual(client.get('/portal/sektion/neu/abrechnung', base_url=PORTAL).status_code, 404)
        admin = self.user('a@example.org', (section, {'admin': True}))
        client = self.login(admin)
        self.assertEqual(client.get('/portal/sektion/neu', base_url=PORTAL).status_code, 200)
        root = self.user('root@example.org', global_admin=True)
        self.assertEqual(self.login(root).get('/portal/sektion/neu', base_url=PORTAL).status_code, 200)

    def test_settings_change_design_categories_and_logo(self):
        self.register()
        section = Section.objects.get(slug='neu')
        section.mail_password = 'geheim'
        section.save()
        admin = self.user('a@example.org', (section, {'admin': True, 'role': 'guide'}))
        client = self.login(admin)
        png = io.BytesIO()
        Image.new('RGB', (40, 20), 'blue').save(png, 'PNG')
        png.seek(0)
        response = client.post('/portal/sektion/neu', base_url=PORTAL, content_type='multipart/form-data', data={
            'name': 'Sektion Neu', 'member_count': '300', 'nav_background_color': '#112233',
            'categories-0-name': 'Skitour', 'categories-0-color': '#0000ff',
            'categories-1-name': '', 'categories-1-color': '#5aaa28',
            'logo': (png, 'logo.png'), 'submit': 'Speichern'})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:3000])
        section.reload()
        self.assertEqual(section.nav_background_color, '#112233')
        self.assertEqual([c.name for c in section.event_categories_full], ['Skitour'])
        self.assertEqual(section.member_count, 300)
        self.assertEqual(section.mail_password, 'geheim')
        self.assertEqual(Image.open(section.logo_image).size, (40, 20))
        page = app.test_client().get('/', base_url='https://neu.example.org').get_data(as_text=True)
        self.assertIn('#112233', page)
        self.assertIn('Skitour', page)

    def test_settings_reject_bad_color(self):
        self.register()
        section = Section.objects.get(slug='neu')
        admin = self.user('a@example.org', (section, {'admin': True}))
        response = self.login(admin).post('/portal/sektion/neu', base_url=PORTAL, data={
            'name': 'X', 'member_count': '10', 'nav_background_color': 'red;}', 'submit': 'Speichern'})
        self.assertEqual(response.status_code, 200)
        section.reload()
        self.assertEqual(section.nav_background_color, '#5aaa28')

    # Sperre und Abrechnung

    def test_expired_trial_blocks_section_until_paid(self):
        app.config['SUBSCRIPTIONS_ENABLED'] = True
        self.register()
        section = Section.objects.get(slug='neu')
        host = 'https://neu.example.org'
        self.assertEqual(app.test_client().get('/', base_url=host).status_code, 200)
        section.trial_until = datetime.now() - timedelta(days=1)
        section.save()
        response = app.test_client().get('/', base_url=host)
        self.assertEqual(response.status_code, 503)
        self.assertIn('sektion.example.org', response.get_data(as_text=True))
        section.subscription_status = 'active'
        section.save()
        self.assertEqual(app.test_client().get('/', base_url=host).status_code, 200)
        # Sektionen ohne Testphase (vom globalen Admin angelegt) laufen immer
        self.assertEqual(app.test_client().get('/', base_url='https://touren.example.org').status_code, 200)

    def test_units_and_price(self):
        section = Section(slug='x', name='X')
        with app.app_context():
            for members, units in ((1, 1), (250, 1), (251, 2), (900, 4)):
                section.member_count = members
                self.assertEqual(billing.units(section), units, members)
            self.assertEqual(billing.monthly_price(section), 40)

    def test_checkout_goes_to_stripe_with_quantity(self):
        app.config['SUBSCRIPTIONS_ENABLED'] = True
        self.register()
        section = Section.objects.get(slug='neu')
        admin = self.user('a@example.org', (section, {'admin': True}))
        client = self.login(admin)
        page = client.get('/portal/sektion/neu/abrechnung', base_url=PORTAL).get_data(as_text=True)
        self.assertIn('40,00', page)
        stripe = mock.MagicMock()
        stripe.checkout.Session.create.return_value = {'url': 'https://checkout.stripe.example/s'}
        with mock.patch.object(billing, '_stripe', return_value=stripe):
            response = client.post('/portal/sektion/neu/abrechnung/abo', base_url=PORTAL)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['Location'], 'https://checkout.stripe.example/s')
        kwargs = stripe.checkout.Session.create.call_args.kwargs
        self.assertEqual(kwargs['line_items'], [{'price': 'price_1', 'quantity': 4}])
        self.assertEqual(kwargs['metadata']['section_id'], str(section.pk))
        self.assertEqual(kwargs['customer_email'], 'a@example.org')

    def test_webhook_activates_and_ignores_stale_events(self):
        self.register()
        section = Section.objects.get(slug='neu')
        event = {'type': 'customer.subscription.updated', 'data': {'object': {
            'id': 'sub_1', 'status': 'active', 'customer': 'cus_1',
            'metadata': {'section_id': str(section.pk)}, 'current_period_end': 1893456000}}}
        with mock.patch.object(billing, 'verify_webhook', return_value=event):
            response = app.test_client().post('/portal/stripe/webhook', base_url=PORTAL, data=b'{}')
        self.assertEqual(response.status_code, 200)
        section.reload()
        self.assertTrue(section.is_paid)
        self.assertEqual(section.stripe_customer_id, 'cus_1')
        stale = {'type': 'customer.subscription.deleted', 'data': {'object': {
            'id': 'sub_0', 'status': 'canceled', 'customer': 'cus_1', 'metadata': {}}}}
        with mock.patch.object(billing, 'verify_webhook', return_value=stale):
            app.test_client().post('/portal/stripe/webhook', base_url=PORTAL, data=b'{}')
        section.reload()
        self.assertEqual(section.subscription_status, 'active')

    def test_webhook_rejects_bad_signature(self):
        app.config['SUBSCRIPTIONS_ENABLED'] = True
        response = app.test_client().post('/portal/stripe/webhook', base_url=PORTAL, data=b'{}',
                                          headers={'Stripe-Signature': 'falsch'})
        self.assertEqual(response.status_code, 400)

    def test_member_count_change_updates_subscription(self):
        app.config['SUBSCRIPTIONS_ENABLED'] = True
        self.register()
        section = Section.objects.get(slug='neu')
        section.subscription_id, section.subscription_status = 'sub_1', 'active'
        section.save()
        admin = self.user('a@example.org', (section, {'admin': True}))
        stripe = mock.MagicMock()
        stripe.Subscription.retrieve.return_value = {'items': {'data': [{'id': 'si_1', 'quantity': 4}]}}
        with mock.patch.object(billing, '_stripe', return_value=stripe):
            self.login(admin).post('/portal/sektion/neu', base_url=PORTAL, data={
                'name': 'Sektion Neu', 'member_count': '1200', 'submit': 'Speichern'})
        stripe.Subscription.modify.assert_called_once_with('sub_1', items=[{'id': 'si_1', 'quantity': 5}])

    def test_section_mail_without_own_server_uses_platform(self):
        self.register()
        section = Section.objects.get(slug='neu')
        with mock.patch('application.modules.email.Thread') as thread:
            app.test_client().post('/user/create', base_url='https://neu.example.org', data={
                'first_name': 'Eve', 'last_name': 'Neu', 'email': 'eve@example.org', 'phone': '1',
                'birthdate': '1990-01-01', 'password': PASSWORD, 'password_repeat': PASSWORD,
                'submit': 'Registrieren'})
        message, state = thread.call_args.kwargs['args'][1:3]
        self.assertEqual(state.server, 'smtp.example.org')
        self.assertEqual(message.sender, f'{section.name} <noreply@example.org>')


if __name__ == '__main__':
    unittest.main()
