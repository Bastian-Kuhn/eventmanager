"""
Umzug: Installation ohne Sektionen exportieren, in eine andere als neue
Sektion importieren. Konten werden ueber die E-Mail zusammengefuehrt.
Ausfuehren: .venv/bin/python -m unittest tests.test_section_transfer (mongomock)
"""
import io
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import mongoengine
import mongomock
from mongomock.gridfs import enable_gridfs_integration
from PIL import Image

from application import app, limiter
from application.events.models import Event, EventParticipation, OwnedTicket, Ticket
from application.huts.models import Hut, HutBooking, HutRoom
from application.models.config import Config
from application.models.section import Section
from application.models.user import Membership, User
from tests.test_security import PASSWORD

OLD = 'https://touren.example.org'
NEW = 'https://main.example.org'


def setUpModule():
    enable_gridfs_integration()
    mongoengine.disconnect()
    mongoengine.connect('test', mongo_client_class=mongomock.MongoClient)


def tearDownModule():
    mongoengine.disconnect()


def user(email, **kwargs):
    kwargs.setdefault('first_name', 'Vor')
    account = User(email=email, last_name='Nach', **kwargs)
    account.set_password(PASSWORD)
    return account.save()


def png():
    out = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(out, 'PNG')
    return out.getvalue()


def login(account, host):
    client = app.test_client()
    response = client.post('/login', base_url=host, data={
        'login_email': account.email, 'password': PASSWORD, 'login_submit': 'Anmelden'})
    assert response.status_code == 302, response.status_code
    return client


class TransferTest(unittest.TestCase):

    def setUp(self):
        self.wipe()
        app.redis = mock.MagicMock()
        limiter.enabled = False
        app.config.update(WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=False)
        # Alte Installation ohne Sektionen
        config = Config(enabled=True, nav_background_color='#123456')
        config.logo_image.put(io.BytesIO(png()), content_type='image/png', filename='logo.png')
        config.save()
        self.root = user('root@example.org', global_admin=True)
        self.guide = user('guide@example.org', role='guide', admin=True, club_id='7')
        self.guide.profile_img.put(io.BytesIO(png()), content_type='image/png', filename='me.png')
        self.guide.save()
        self.member = user('member@example.org', role='member')
        now = datetime.now()
        self.hut = Hut(name='Huette', admins=[self.guide], rooms=[HutRoom(name='Lager', places=5)]).save()
        self.event = Event(event_name='Tour', event_type='guided', places=5, event_owners=[self.guide],
                           start_date=now + timedelta(days=5), end_date=now + timedelta(days=6),
                           tickets=[Ticket(name='Teilnahme', price=10.0)], hut=self.hut,
                           participations=[EventParticipation(user=self.member, tickets=[
                               OwnedTicket(ticket_id='1', name_on_ticket='Member')])]).save()
        self.member.update(push__event_registrations=self.event)
        self.booking = HutBooking(hut=self.hut, user=self.member, name='Gruppe', places=2,
                                  from_date=date.today() + timedelta(days=3),
                                  to_date=date.today() + timedelta(days=4)).save()

    @staticmethod
    def wipe():
        for model in (User, Event, Hut, HutBooking, Section, Config):
            model.drop_collection()

    def export(self):
        response = login(self.root, OLD).post('/admin/Transfer/export', base_url=OLD)
        self.assertEqual(response.status_code, 200)
        self.assertIn('.json.gz', response.headers['Content-Disposition'])
        return response.data

    def new_installation(self):
        """Ziel: eigene Sektion, dasselbe Mitglied mit anderer id, globaler Admin"""
        self.wipe()
        main = Section(slug='main', name='Main', hostnames=['main.example.org'], is_default=True).save()
        existing = user('member@example.org', first_name='Bleibt')
        existing.update(push__memberships=Membership(section=main, role='member'))
        user('root@example.org', global_admin=True)
        return existing

    def do_import(self, data, slug='tsv', host='https://TSV.example.org/'):
        client = login(User.objects.get(email='root@example.org'), NEW)
        return client.post('/admin/Transfer/import', base_url=NEW, data={
            'file': (io.BytesIO(data), 'export.json.gz'), 'slug': slug, 'name': 'TSV',
            'hostname': host}, content_type='multipart/form-data',
            follow_redirects=True).get_data(as_text=True)

    def test_export_and_import(self):
        data = self.export()
        old_guide_id = self.guide.id
        existing = self.new_installation()
        page = self.do_import(data)
        self.assertIn('TSV ist angelegt: 1 Touren, 1 Hütten, 1 Hüttenbuchungen, 1 neue Konten, '
                      '2 vorhandene Konten ergänzt.', page)

        section = Section.objects.get(slug='tsv')
        self.assertEqual(section.hostnames, ['tsv.example.org'])
        self.assertEqual(section.nav_background_color, '#123456')
        self.assertIsNone(section.trial_until)
        self.assertFalse(section.is_default)

        event = Event.all_objects.get(id=self.event.id)
        self.assertEqual(event.section.pk, section.pk)
        self.assertEqual(event.participations[0].user.pk, existing.pk)
        self.assertEqual(event.hut.pk, self.hut.id)
        guide = User.objects.get(email='guide@example.org')
        self.assertEqual(guide.pk, old_guide_id)
        self.assertEqual([owner.pk for owner in event.event_owners], [guide.pk])
        self.assertEqual(guide.role_in(section), 'guide')
        self.assertTrue(guide.membership(section).admin)
        self.assertEqual(guide.membership(section).club_id, '7')
        self.assertTrue(guide.check_password(PASSWORD))
        self.assertFalse(guide.global_admin)
        self.assertEqual(guide.profile_img.read(), png())
        self.assertEqual(section.logo_image.read(), png())

        existing.reload()
        self.assertEqual(existing.first_name, 'Bleibt')
        self.assertEqual(existing.role_in(section), 'member')
        self.assertEqual(len(existing.memberships), 2)
        self.assertIn(self.event.id, [ref.pk for ref in existing.event_registrations])
        self.assertEqual(HutBooking.all_objects.get(id=self.booking.id).user.pk, existing.pk)
        self.assertEqual(Hut.all_objects.get(id=self.hut.id).admins[0].pk, guide.pk)
        # Der globale Admin bleibt, wie er war, ohne Mitgliedschaft
        self.assertEqual(User.objects.get(email='root@example.org').memberships, [])

        # Zweiter Import derselben Datei aendert nichts
        page = self.do_import(data, slug='tsv2', host='tsv2.example.org')
        self.assertIn('schon vorhanden', page)
        self.assertIsNone(Section.objects(slug='tsv2').first())
        self.assertEqual(Event.all_objects.count(), 1)

    def test_section_export_only_own_data(self):
        alpha = Section(slug='alpha', name='Alpha', hostnames=['touren.example.org']).save()
        beta = Section(slug='beta', name='Beta', hostnames=['beta.example.org']).save()
        Event.all_objects.update(set__section=alpha)
        Hut.all_objects.update(set__section=alpha)
        HutBooking.all_objects.update(set__section=alpha)
        self.member.update(push__memberships=Membership(section=alpha, role='member'))
        user('other@example.org').update(push__memberships=Membership(section=beta, role='guide'))
        data = self.export()
        self.new_installation()
        self.do_import(data)
        self.assertIsNone(User.objects(email='other@example.org').first())
        section = Section.objects.get(slug='tsv')
        self.assertEqual(section.name, 'TSV')
        # guide steht nur als Besitzer in den Daten: Konto kommt mit, ohne Mitgliedschaft
        self.assertIsNone(User.objects.get(email='guide@example.org').membership(section))

    def test_garbage_and_rights(self):
        self.new_installation()
        page = self.do_import(b'kein export')
        self.assertIn('kein Export', page)
        self.assertEqual(Section.objects.count(), 1)
        guide = user('admin@example.org')
        guide.update(push__memberships=Membership(section=Section.objects.first(), role='guide', admin=True))
        client = login(guide, NEW)
        self.assertEqual(client.get('/admin/Transfer/', base_url=NEW).status_code, 404)
        self.assertEqual(client.post('/admin/Transfer/export', base_url=NEW).status_code, 404)


if __name__ == '__main__':
    unittest.main()
