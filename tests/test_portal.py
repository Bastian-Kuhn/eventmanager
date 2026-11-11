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
from application.events.models import Event, EventParticipation
from application.huts.models import Hut, HutBooking
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
        for model in (User, Section, Config, Event, Hut, HutBooking):
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

    def test_club_portal_mirrors_with_verein_wording(self):
        client = app.test_client()
        club = 'https://verein.example.org'
        self.assertEqual(client.get('/', base_url=club).headers['Location'], '/portal/')
        page = client.get('/portal/registrieren', base_url=club).get_data(as_text=True)
        self.assertIn('Verein registrieren', page)
        self.assertIn('Name des Vereins', page)
        self.assertNotIn('Sektion', page)
        self.assertIn('Sektion registrieren', client.get('/portal/registrieren', base_url=PORTAL).get_data(as_text=True))
        with mock.patch('application.modules.email.Thread'):
            response = client.post('/portal/registrieren', base_url=club,
                                   data={**NEW_SECTION, 'submit': 'Verein registrieren'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Section.objects.get(slug='neu').hostnames, ['neu.example.org'])
        self.assertIn('Der Verein Sektion Neu ist angelegt',
                      client.get('/portal/', base_url=club).get_data(as_text=True))
        response, _ = self.register(slug='verein')
        self.assertEqual(response.status_code, 200)

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
        section.mail_server, section.mail_password = 'smtp.neu.example.org', 'geheim'
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
            'logo': (png, 'logo.png'), 'own_mail': 'y', 'mail_server': 'smtp.neu.example.org',
            'submit': 'Speichern'})
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

    def test_partner_page_search_request_and_end(self):
        self.register()
        section = Section.objects.get(slug='neu')
        for idx in range(25):
            Section(slug=f'verein{idx}', name=f'Verein {idx:02d}').save()
        client = self.login(self.user('a@example.org', (section, {'admin': True})))
        url = '/portal/sektion/neu/partner'
        page = client.get(url, base_url=PORTAL).get_data(as_text=True)
        self.assertIn('Ihr teilt eure Touren noch mit niemandem', page)
        # Suche statt Liste aller: hoechstens 20 Treffer, nie die eigene Sektion
        page = client.get(url + '?q=verein', base_url=PORTAL).get_data(as_text=True)
        self.assertEqual(page.count('Anfrage senden'), 20)
        self.assertIn('Es gibt mehr Treffer', page)
        page = client.get(url + '?q=Neu', base_url=PORTAL).get_data(as_text=True)
        self.assertIn('Nichts gefunden', page)
        page = client.get(url + '?q=.*', base_url=PORTAL).get_data(as_text=True)
        self.assertIn('Nichts gefunden', page)
        # Anfrage, dann Zustimmung der anderen Seite, dann beenden
        self.assertEqual(client.post(url, base_url=PORTAL, data={'partner_id': str(self.dav.pk)}).status_code, 302)
        page = client.get(url, base_url=PORTAL).get_data(as_text=True)
        self.assertIn('DAV, wartet auf Zustimmung', page)
        with app.app_context():
            self.assertEqual(section.partners(), [])
        self.dav.update(set__partner_sections=[section])
        page = client.get(url, base_url=PORTAL).get_data(as_text=True)
        self.assertIn('Teilen mit DAV beenden', page)
        with app.app_context():
            self.assertEqual([p.slug for p in Section.objects.get(slug='neu').partners()], ['dav'])
        # Einstellungen speichern laesst die Partner stehen
        client.post('/portal/sektion/neu', base_url=PORTAL, data={
            'name': 'Sektion Neu', 'member_count': '300', 'submit': 'Speichern'})
        self.assertEqual([p.pk for p in Section.objects.get(slug='neu').partner_sections], [self.dav.pk])
        client.post(url, base_url=PORTAL, data={'partner_id': str(self.dav.pk), 'action': 'remove'})
        self.assertEqual(Section.objects.get(slug='neu').partner_sections, [])
        self.assertEqual(client.post(url, base_url=PORTAL, data={'partner_id': 'kaputt'}).status_code, 404)

    def test_partner_request_notice_and_accept(self):
        self.register()
        section = Section.objects.get(slug='neu')
        self.dav.update(set__partner_sections=[section])
        admin = self.user('a@example.org', (section, {'admin': True}))
        client = self.login(admin)
        page = client.get('/portal/', base_url=PORTAL).get_data(as_text=True)
        self.assertIn('DAV</strong> möchte die Touren mit Sektion Neu teilen', page)
        # Nur Sektionen, die wirklich anfragen, und nur fuer Admins der Sektion
        self.assertEqual(client.post('/portal/sektion/neu/partner', base_url=PORTAL,
                                     data={'partner_id': str(section.pk)}).status_code, 404)
        stranger = self.user('s@example.org', (self.dav, {'admin': True}))
        self.assertEqual(self.login(stranger).post('/portal/sektion/neu/partner', base_url=PORTAL,
                                                   data={'partner_id': str(self.dav.pk)}).status_code, 404)
        response = client.post('/portal/sektion/neu/partner', base_url=PORTAL, data={'partner_id': str(self.dav.pk)})
        self.assertEqual(response.status_code, 302)
        section.reload()
        self.assertEqual([p.pk for p in section.partner_sections], [self.dav.pk])
        page = client.get('/portal/', base_url=PORTAL).get_data(as_text=True)
        self.assertNotIn('möchte die Touren', page)

    def test_global_admin_deletes_section_with_everything(self):
        self.register()
        neu = Section.objects.get(slug='neu')
        event = Event(event_name='Neutour', section=neu, start_date=datetime.now()).save()
        other_event = Event(event_name='Davtour', section=self.dav, start_date=datetime.now()).save()
        hut = Hut(name='Neuhuette', section=neu).save()
        HutBooking(hut=hut, section=neu, name='Gruppe').save()
        only_neu = self.user('only@example.org', (neu, {'role': 'member'}))
        both = self.user('both@example.org', (neu, {'role': 'guide'}), (self.dav, {'role': 'member'}))
        guest = self.user('guest@example.org', (self.dav, {'role': 'member'}))
        for user in (only_neu, guest):
            User.objects(id=user.id).update_one(add_to_set__event_registrations=event)
            Event.all_objects(id=event.id).update_one(push__participations=EventParticipation(user=user))
        User.objects(id=both.id).update_one(add_to_set__event_registrations=other_event)
        Event.all_objects(id=other_event.id).update_one(push__participations=EventParticipation(user=only_neu))
        self.dav.update(set__partner_sections=[neu])
        root = self.user('root@example.org', (neu, {'admin': True}), global_admin=True)
        url = '/portal/sektion/neu/loeschen'

        # Nur globale Admins, auch ein Admin der Sektion nicht
        admin = self.user('a@example.org', (neu, {'admin': True}))
        self.assertEqual(self.login(admin).get(url, base_url=PORTAL).status_code, 404)
        self.assertNotIn('Weiter zum Löschen', self.login(admin).get('/portal/sektion/neu', base_url=PORTAL)
                         .get_data(as_text=True))
        client = self.login(root)
        self.assertIn('Weiter zum Löschen', client.get('/portal/sektion/neu', base_url=PORTAL).get_data(as_text=True))
        page = client.get(url, base_url=PORTAL).get_data(as_text=True)
        self.assertIn('1 Touren', page)
        self.assertIn('3 Konten, die nur in Sektion Neu', page)  # Ada, a und only
        # Falscher Kurzname: nichts passiert
        client.post(url, base_url=PORTAL, data={'confirm': 'dav'})
        self.assertEqual(Section.objects(slug='neu').count(), 1)
        self.assertEqual(Event.all_objects(id=event.id).count(), 1)

        response = client.post(url, base_url=PORTAL, data={'confirm': 'neu'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Section.objects(slug='neu').count(), 0)
        self.assertEqual(Event.all_objects(section=neu.pk).count(), 0)
        self.assertEqual(Hut.all_objects.count(), 0)
        self.assertEqual(HutBooking.all_objects.count(), 0)
        self.assertEqual(User.objects(email__in=['only@example.org', 'ada@example.org', 'a@example.org']).count(), 0)
        both.reload()
        self.assertEqual([m.section.pk for m in both.memberships], [self.dav.pk])
        self.assertTrue(both.participate_event(other_event.id))
        guest.reload()
        self.assertFalse(guest.participate_event(event.id))
        root.reload()
        self.assertEqual(root.memberships, [])
        self.dav.reload()
        self.assertEqual(self.dav.partner_sections, [])
        # Buchung des geloeschten Kontos bei der anderen Sektion bleibt fuer die Abrechnung
        self.assertEqual(len(Event.all_objects.get(id=other_event.id).participations), 1)

    def test_delete_blocked_for_default_and_running_subscription(self):
        self.register()
        root = self.user('root@example.org', global_admin=True)
        client = self.login(root)
        page = client.get('/portal/sektion/dav/loeschen', base_url=PORTAL).get_data(as_text=True)
        self.assertIn('Standardsektion kann nicht gelöscht werden', page)
        client.post('/portal/sektion/dav/loeschen', base_url=PORTAL, data={'confirm': 'dav'})
        self.assertEqual(Section.objects(slug='dav').count(), 1)
        Section.objects(slug='neu').update_one(set__subscription_status='active')
        client.post('/portal/sektion/neu/loeschen', base_url=PORTAL, data={'confirm': 'neu'})
        self.assertEqual(Section.objects(slug='neu').count(), 1)
        Section.objects(slug='neu').update_one(set__cancel_at_period_end=True)
        client.post('/portal/sektion/neu/loeschen', base_url=PORTAL, data={'confirm': 'neu'})
        self.assertEqual(Section.objects(slug='neu').count(), 0)

    def test_global_admin_approves_unconfirmed_section_admin(self):
        self.register()
        ada = User.objects.get(email='ada@example.org')
        self.assertTrue(ada.email_unconfirmed)
        self.user('member@example.org', email_unconfirmed=True)  # kein Admin: nicht in der Liste
        admin = self.user('a@example.org', (self.dav, {'admin': True}))
        page = self.login(admin).get('/portal/', base_url=PORTAL).get_data(as_text=True)
        self.assertNotIn('Warten auf Freigabe', page)
        self.assertEqual(self.login(admin).post('/portal/freigabe', base_url=PORTAL,
                                                data={'user_id': str(ada.id)}).status_code, 404)
        client = self.login(self.user('root@example.org', global_admin=True))
        page = client.get('/portal/', base_url=PORTAL).get_data(as_text=True)
        self.assertIn('Warten auf Freigabe', page)
        self.assertIn('ada@example.org', page)
        self.assertNotIn('member@example.org', page)
        self.assertIn('https://neu.example.org/admin/', page)
        response = client.post('/portal/freigabe', base_url=PORTAL, data={'user_id': str(ada.id)})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.get(email='ada@example.org').email_unconfirmed)
        self.assertNotIn('Warten auf Freigabe', client.get('/portal/', base_url=PORTAL).get_data(as_text=True))
        self.login(User.objects.get(email='ada@example.org'))  # Anmeldung geht jetzt
        self.assertEqual(client.post('/portal/freigabe', base_url=PORTAL,
                                     data={'user_id': str(ada.id)}).status_code, 404)

    def test_global_admin_sees_own_and_all_sections_apart(self):
        self.register()
        root = self.user('root@example.org', (self.dav, {'admin': True}), global_admin=True)
        client = self.login(root)
        page = client.get('/portal/', base_url=PORTAL).get_data(as_text=True)
        mine, _, everything = page.partition('id="all-heading"')
        self.assertIn('Einstellungen DAV', mine)
        self.assertNotIn('Einstellungen Sektion Neu', mine)
        self.assertIn('Alle Sektionen (2)', page)
        self.assertIn('Einstellungen Sektion Neu', everything)
        page = client.get('/portal/?q=neu', base_url=PORTAL).get_data(as_text=True)
        everything = page.partition('id="all-heading"')[2]
        self.assertIn('Sektion Neu', everything)
        self.assertNotIn('Einstellungen DAV', everything)
        # Sektionsadmins sehen keine fremden und keine Suche
        page = self.login(self.user('a@example.org', (self.dav, {'admin': True}))) \
            .get('/portal/?q=neu', base_url=PORTAL).get_data(as_text=True)
        self.assertNotIn('Sektion Neu', page)
        self.assertNotIn('all-heading', page)

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

    def test_legal_pages_everywhere(self):
        app.config['SUBSCRIPTIONS_ENABLED'] = True
        self.register()
        Section.objects(slug='neu').update_one(set__trial_until=datetime.now() - timedelta(days=1))
        for host in (PORTAL, 'https://touren.example.org', 'https://neu.example.org'):
            for path, text in (('/impressum', 'DE319059455'), ('/datenschutz', 'Hetzner Online GmbH')):
                response = app.test_client().get(path, base_url=host)
                self.assertEqual(response.status_code, 200, (host, path))
                body = response.get_data(as_text=True)
                self.assertIn(text, body)
                self.assertIn('href="/impressum"', body)
                self.assertIn('href="/datenschutz"', body)
        self.assertIn('Stripe', app.test_client().get('/datenschutz', base_url=PORTAL).get_data(as_text=True))
        app.config['SUBSCRIPTIONS_ENABLED'] = False
        self.assertNotIn('Stripe', app.test_client().get('/datenschutz', base_url=PORTAL).get_data(as_text=True))

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
        # Rechnungsadresse und USt-IdNr., Stripe Tax nur mit Schalter
        self.assertEqual(kwargs['billing_address_collection'], 'required')
        self.assertEqual(kwargs['tax_id_collection'], {'enabled': True})
        self.assertNotIn('customer_update', kwargs)
        self.assertNotIn('automatic_tax', kwargs)
        section.update(set__stripe_customer_id='cus_1')
        app.config['STRIPE_AUTOMATIC_TAX'] = True
        self.addCleanup(app.config.update, STRIPE_AUTOMATIC_TAX=False)
        with mock.patch.object(billing, '_stripe', return_value=stripe):
            client.post('/portal/sektion/neu/abrechnung/abo', base_url=PORTAL)
        kwargs = stripe.checkout.Session.create.call_args.kwargs
        self.assertEqual(kwargs['customer'], 'cus_1')
        self.assertEqual(kwargs['customer_update'], {'address': 'auto', 'name': 'auto'})
        self.assertEqual(kwargs['automatic_tax'], {'enabled': True})

    def test_billing_page_lists_invoices(self):
        app.config['SUBSCRIPTIONS_ENABLED'] = True
        self.register()
        section = Section.objects.get(slug='neu')
        client = self.login(self.user('a@example.org', (section, {'admin': True})))
        page = client.get('/portal/sektion/neu/abrechnung', base_url=PORTAL).get_data(as_text=True)
        self.assertNotIn('Rechnungen</h2>', page)
        section.update(set__stripe_customer_id='cus_1')
        stripe = mock.MagicMock()
        stripe.Invoice.list.return_value = {'data': [
            {'number': 'RE-0002', 'created': 1767225600, 'total': 4000, 'currency': 'eur', 'status': 'paid',
             'hosted_invoice_url': 'https://invoice.stripe.example/2', 'invoice_pdf': 'https://invoice.stripe.example/2.pdf'},
            {'number': None, 'created': 1767225600, 'total': 4000, 'currency': 'eur', 'status': 'draft'},
        ]}
        with mock.patch.object(billing, '_stripe', return_value=stripe):
            page = client.get('/portal/sektion/neu/abrechnung', base_url=PORTAL).get_data(as_text=True)
        stripe.Invoice.list.assert_called_once_with(customer='cus_1', limit=12)
        self.assertIn('RE-0002', page)
        self.assertIn('40,00', page)
        self.assertIn('Bezahlt', page)
        self.assertIn('https://invoice.stripe.example/2.pdf', page)
        self.assertEqual(page.count('<tr>'), 2)  # Kopf und eine Rechnung, der Entwurf fehlt
        stripe.Invoice.list.side_effect = RuntimeError('Stripe weg')
        with mock.patch.object(billing, '_stripe', return_value=stripe):
            response = client.get('/portal/sektion/neu/abrechnung', base_url=PORTAL)
        self.assertEqual(response.status_code, 200)
        self.assertIn('konnten gerade nicht geladen werden', response.get_data(as_text=True))

    def test_yearly_checkout_uses_yearly_price(self):
        app.config.update(SUBSCRIPTIONS_ENABLED=True, STRIPE_PRICE_SECTION_YEARLY='price_year')
        try:
            self.register()
            section = Section.objects.get(slug='neu')
            client = self.login(self.user('a@example.org', (section, {'admin': True})))
            page = client.get('/portal/sektion/neu/abrechnung', base_url=PORTAL).get_data(as_text=True)
            self.assertIn('432,00', page)
            stripe = mock.MagicMock()
            stripe.checkout.Session.create.return_value = {'url': 'https://checkout.stripe.example/y'}
            with mock.patch.object(billing, '_stripe', return_value=stripe):
                client.post('/portal/sektion/neu/abrechnung/abo', base_url=PORTAL, data={'interval': 'year'})
            self.assertEqual(stripe.checkout.Session.create.call_args.kwargs['line_items'],
                             [{'price': 'price_year', 'quantity': 4}])
            billing.apply_subscription({'id': 'sub_9', 'status': 'active', 'customer': 'cus_9',
                                        'metadata': {'section_id': str(section.pk)},
                                        'items': {'data': [{'price': {'recurring': {'interval': 'year'}}}]}})
            self.assertEqual(section.reload().billing_interval, 'year')
        finally:
            app.config['STRIPE_PRICE_SECTION_YEARLY'] = ''

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

    def test_incomplete_own_mail_falls_back_to_platform(self):
        self.register()
        section = Section.objects.get(slug='neu')
        section.mail_server, section.mail_sender = 'smtp.neu.example.org', 'neu@example.org'
        section.mail_username = 'neu'
        section.save()
        with mock.patch('application.modules.email.Thread') as thread:
            self.client_post_registration()
        self.assertEqual(thread.call_args.kwargs['args'][2].server, 'smtp.example.org')

    def client_post_registration(self):
        return app.test_client().post('/user/create', base_url='https://neu.example.org', data={
            'first_name': 'Eve', 'last_name': 'Neu', 'email': 'eve@example.org', 'phone': '1',
            'birthdate': '1990-01-01', 'password': PASSWORD, 'password_repeat': PASSWORD,
            'submit': 'Registrieren'})

    def test_settings_own_mail_toggle(self):
        self.register()
        section = Section.objects.get(slug='neu')
        admin = self.user('a@example.org', (section, {'admin': True, 'role': 'guide'}))
        client = self.login(admin)
        base = {'name': 'Sektion Neu', 'member_count': '900', 'submit': 'Speichern'}
        response = client.post('/portal/sektion/neu', base_url=PORTAL, data={
            **base, 'own_mail': 'y', 'mail_server': 'smtp.neu.example.org', 'mail_username': 'neu'})
        self.assertEqual(response.status_code, 200)
        response = client.post('/portal/sektion/neu', base_url=PORTAL, data={**base, 'own_mail': 'y'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(section.reload().mail_server)
        response = client.post('/portal/sektion/neu', base_url=PORTAL, data={
            **base, 'own_mail': 'y', 'mail_server': 'smtp.neu.example.org',
            'mail_username': 'neu', 'mail_password': 'geheim'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(section.reload().mail_password, 'geheim')
        page = client.get('/portal/sektion/neu', base_url=PORTAL).get_data(as_text=True)
        self.assertRegex(page, r'<input[^>]*checked[^>]*id="own_mail"|<input[^>]*id="own_mail"[^>]*checked')
        client.post('/portal/sektion/neu', base_url=PORTAL, data=base)
        section.reload()
        self.assertIsNone(section.mail_server)
        self.assertIsNone(section.mail_password)

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
