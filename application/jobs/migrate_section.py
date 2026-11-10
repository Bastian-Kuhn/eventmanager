"""
Bestehende Installation in eine Sektion ueberfuehren (an Ort und Stelle).

    flask --app app migrate_section beispiel "DAV Sektion Beispiel" \
        --host touren.example.org --default [--dry-run]

Legt die Section aus dem alten Config-Doc an (Design, Logo, Mailversand,
Kategorien), haengt alle Touren, Huetten und Huettenbuchungen ohne Sektion
an sie und macht jedes Konto zum Mitglied mit seiner bisherigen Rolle,
Vereinsadmin und Mitgliedsnummer. Nichts wird geloescht: Config-Doc und
Altfelder der Konten bleiben stehen. Ein zweiter Lauf aendert nichts mehr.
"""
import io

import click

from application import app
from application.events.models import Event
from application.huts.models import Hut, HutBooking
from application.models.config import Config
from application.models.section import Section
from application.models.user import Membership, User

DESIGN_FIELDS = ('nav_background_color', 'homepage_link', 'event_categories_full',
                 'mail_sender', 'mail_server', 'mail_use_tls', 'mail_username',
                 'mail_subject_prefix', 'mail_password', 'media_consent_text')


def _new_section(slug, name, hostnames, default):
    section = Section(slug=slug, name=name, hostnames=list(hostnames), is_default=default)
    old = Config.objects(enabled=True).first()
    if old:
        for field in DESIGN_FIELDS:
            setattr(section, field, getattr(old, field))
    section.validate()
    if old and old.logo_image:
        logo = old.logo_image.get()
        if logo is not None:
            section.logo_image.put(io.BytesIO(logo.read()), content_type=logo.content_type,
                                   filename=logo.filename)
    return section.save()


def migrate_to_section(slug, name, hostnames=(), default=False, dry_run=False):
    """Fuehrt die Migration aus, gibt die Zahlen je Bereich zurueck"""
    section = Section.objects(slug=slug).first()
    report = {'section': 'vorhanden' if section else 'neu'}
    unassigned = {'section': None}
    for label, model in (('touren', Event), ('huetten', Hut), ('huettenbuchungen', HutBooking)):
        collection = model._get_collection()  # pylint: disable=protected-access
        report[label] = collection.count_documents(unassigned)
    users = [user for user in User.objects
             if not section or not user.membership(section)]
    report['konten'] = len(users)
    if dry_run:
        return report

    if not section:
        section = _new_section(slug, name, hostnames, default)
    for model in (Event, Hut, HutBooking):
        model._get_collection().update_many(unassigned, {'$set': {'section': section.pk}})  # pylint: disable=protected-access
    for user in users:
        # Atomar und nur ohne Mitgliedschaft, damit ein zweiter Lauf nichts doppelt anlegt
        User.objects(id=user.id, memberships__section__ne=section.pk).update_one(
            push__memberships=Membership(section=section, role=user.legacy_role,
                                         admin=bool(user.legacy_admin),
                                         club_id=user.legacy_club_id))
    return report


@app.cli.command('migrate_section')
@click.argument('slug')
@click.argument('name')
@click.option('--host', 'hostnames', multiple=True, help='Hostname der Sektion, mehrfach moeglich')
@click.option('--default', is_flag=True, help='Gilt fuer alle Adressen ohne eigene Sektion')
@click.option('--dry-run', is_flag=True, help='Nur zaehlen, nichts aendern')
def migrate_section(slug, name, hostnames, default, dry_run):
    """Bestehende Daten einer Sektion zuordnen"""
    report = migrate_to_section(slug, name, hostnames, default, dry_run)
    prefix = 'Probelauf, nichts geaendert: ' if dry_run else ''
    print(prefix + ', '.join(f'{key} {value}' for key, value in report.items()))
