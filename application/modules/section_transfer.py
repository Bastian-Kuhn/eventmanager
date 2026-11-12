"""
Umzug einer Sektion zwischen Installationen: Export als Datei, Import als
neue Sektion. Eine Installation ohne Sektionen (vor der Migration) exportiert
alle Daten, Rolle, Vereinsadmin und Mitgliedsnummer aus den Altfeldern.

Die Datei ist gzip-JSON (bson.json_util) mit Touren, Huetten, Huettenbuchungen,
Einstellungen, Logo und den Konten, die Mitglied sind oder in den Daten
vorkommen, samt Passwort-Hash und Profilbild. 2FA, globale Adminrechte und
Mitgliedschaften in anderen Sektionen bleiben draussen.

Beim Import behalten Touren, Huetten und Buchungen ihre ids, damit Links
weiter gehen. Gibt es eine id schon, bricht er vor dem ersten Schreiben ab.
Konten werden ueber die E-Mail zusammengefuehrt: ein vorhandenes Konto
bekommt die Mitgliedschaft dazu, seine Daten bleiben.
"""
import gzip
import io
from base64 import b64decode, b64encode
from datetime import datetime

import gridfs
from bson import DBRef, ObjectId, json_util

from application.events.models import Event
from application.huts.models import Hut, HutBooking
from application.models.section import Section
from application.models.user import User

FORMAT = 'tourenanmeldung-sektion'
VERSION = 1
SECTION_FIELDS = ('name', 'nav_background_color', 'homepage_link', 'event_categories_full',
                  'mail_sender', 'mail_server', 'mail_use_tls', 'mail_username',
                  'mail_subject_prefix', 'mail_password', 'media_consent_text',
                  'member_count', 'require_approval')
USER_DROP = ('memberships', 'join_requests', 'role', 'admin', 'club_id', 'global_admin',
             'tfa_secret', 'tfa_pending_secret', 'tfa_recovery_codes', 'tfa_last_step')


class TransferError(Exception):
    """Datei passt nicht oder die Daten gibt es schon"""


def _collection(model):
    return model._get_collection()  # pylint: disable=protected-access


def _plain_id(value):
    """ObjectId hinter einem Verweis (DBRef oder ObjectId), sonst None"""
    if isinstance(value, DBRef):
        return value.id
    return value if isinstance(value, ObjectId) else None


def _referenced_users(events, huts, bookings):
    ids = set()
    for event in events:
        ids.update(_plain_id(p.get('user')) for p in event.get('participations') or [])
        ids.update(_plain_id(owner) for owner in event.get('event_owners') or [])
    for hut in huts:
        ids.update(_plain_id(admin) for admin in hut.get('admins') or [])
    ids.update(_plain_id(booking.get('user')) for booking in bookings)
    ids.discard(None)
    return ids


def _profile_grid():
    """GridFS der Profilbilder, Bucket wie im Feld"""
    return gridfs.GridFS(_collection(User).database, User._fields['profile_img'].collection_name)  # pylint: disable=protected-access


def _file(grid, file_id):
    """GridFS-Datei als dict fuer die Exportdatei, None wenn sie fehlt"""
    try:
        stored = grid.get(file_id)
    except gridfs.errors.NoFile:
        return None
    return {'data': b64encode(stored.read()).decode(), 'content_type': stored.content_type,
            'filename': stored.filename}


def _membership(user, section):
    """Rolle, Vereinsadmin und Mitgliedsnummer in section, None ohne Mitgliedschaft"""
    if section.pk is None:
        return {'role': user.get('role'), 'admin': bool(user.get('admin')),
                'club_id': user.get('club_id')}
    for entry in user.get('memberships') or []:
        if _plain_id(entry.get('section')) == section.pk:
            return {key: entry.get(key) for key in ('role', 'admin', 'club_id', 'guest')}
    return None


def export_section(section):
    """Alle Daten von section als gzip-JSON (bytes) und die Zahlen"""
    scope = {'section': section.pk}
    events = list(_collection(Event).find(scope))
    huts = list(_collection(Hut).find(scope))
    bookings = list(_collection(HutBooking).find(scope))
    event_ids = {event['_id'] for event in events}

    referenced = _referenced_users(events, huts, bookings)
    if section.pk is None:
        user_query = {}
    else:
        user_query = {'$or': [{'memberships.section': section.pk}, {'_id': {'$in': list(referenced)}}]}
    grid = _profile_grid()
    users = []
    for user in _collection(User).find(user_query):
        entry = {key: value for key, value in user.items() if key not in USER_DROP}
        for field in ('event_registrations', 'favorites'):
            entry[field] = [ref for ref in user.get(field) or [] if _plain_id(ref) in event_ids]
        # Globale Admins brauchen keine Mitgliedschaft, wie bei migrate_section-Logins
        entry['_membership'] = None if user.get('global_admin') else _membership(user, section)
        image = _plain_id(user.get('profile_img'))
        entry['profile_img'] = _file(grid, image) if image else None
        users.append(entry)

    son = section.to_mongo()
    settings = {key: son[key] for key in SECTION_FIELDS if key in son}
    logo = None
    if section.logo_image:
        stored = section.logo_image.get()
        if stored is not None:
            logo = {'data': b64encode(stored.read()).decode(), 'content_type': stored.content_type,
                    'filename': stored.filename}
    payload = {
        'format': FORMAT, 'version': VERSION, 'exported': datetime.now(),
        'section': settings, 'logo': logo,
        'events': events, 'huts': huts, 'hut_bookings': bookings, 'users': users,
    }
    counts = {'events': len(events), 'huts': len(huts), 'hut_bookings': len(bookings), 'users': len(users)}
    return gzip.compress(json_util.dumps(payload).encode()), counts


def read_export(data):
    """Exportdatei lesen und pruefen"""
    try:
        payload = json_util.loads(gzip.decompress(data).decode())
    except Exception as exc:  # pylint: disable=broad-except
        raise TransferError("Die Datei ist kein Export einer Tourenanmeldung.") from exc
    if not isinstance(payload, dict) or payload.get('format') != FORMAT:
        raise TransferError("Die Datei ist kein Export einer Tourenanmeldung.")
    if payload.get('version') != VERSION:
        raise TransferError("Diese Exportdatei stammt aus einer anderen Version.")
    return payload


def _remap(value, mapping):
    """Verweis auf ein Konto auf dessen id in dieser Installation umbiegen"""
    if isinstance(value, DBRef):
        return DBRef(value.collection, mapping.get(value.id, value.id), value.database)
    if isinstance(value, ObjectId):
        return mapping.get(value, value)
    return value


def _check_free(payload, slug, hostnames):
    if Section.objects(slug=slug).first():
        raise TransferError(f"Den Kurznamen {slug} gibt es schon.")
    for host in hostnames:
        if Section.objects(hostnames=host).first():
            raise TransferError(f"Die Adresse {host} gehört schon zu einer Sektion.")
    for key, model in (('events', Event), ('huts', Hut), ('hut_bookings', HutBooking)):
        ids = [doc['_id'] for doc in payload[key]]
        if ids and _collection(model).count_documents({'_id': {'$in': ids}}):
            raise TransferError("Diese Daten sind hier schon vorhanden, wurde die Datei schon importiert?")


def _put_file(grid, stored):
    return grid.put(b64decode(stored['data']), content_type=stored.get('content_type'),
                    filename=stored.get('filename'))


def _import_users(payload, section, grid):
    """Konten anlegen oder zusammenfuehren, gibt {alte id: id hier} und Zahlen zurueck"""
    users = _collection(User)
    mapping, created, merged = {}, 0, 0
    for entry in payload['users']:
        old_id = entry.pop('_id')
        member = entry.pop('_membership', None)
        image = entry.pop('profile_img', None)
        membership = None
        if member is not None:
            membership = {'section': section.pk, 'role': member.get('role'),
                          'admin': bool(member.get('admin')), 'club_id': member.get('club_id'),
                          'guest': bool(member.get('guest'))}
        email = (entry.get('email') or '').lower()
        existing = users.find_one({'email': email}, {'_id': 1})
        if existing:
            mapping[old_id] = existing['_id']
            update = {'$addToSet': {field: {'$each': entry.get(field) or []}
                                    for field in ('event_registrations', 'favorites')}}
            users.update_one({'_id': existing['_id']}, update)
            if membership:
                users.update_one({'_id': existing['_id'], 'memberships.section': {'$ne': section.pk}},
                                 {'$push': {'memberships': membership}})
            merged += 1
            continue
        new_id = old_id if not users.count_documents({'_id': old_id}) else ObjectId()
        mapping[old_id] = new_id
        entry.update(_id=new_id, email=email, memberships=[membership] if membership else [],
                     global_admin=False)
        if image:
            entry['profile_img'] = _put_file(grid, image)
        users.insert_one(entry)
        created += 1
    return mapping, created, merged


def import_section(payload, slug, name, hostnames=()):
    """Exportdatei als neue Sektion anlegen, gibt die Zahlen zurueck"""
    hostnames = list(hostnames)
    _check_free(payload, slug, hostnames)

    settings = dict(payload['section'], slug=slug, name=name or payload['section'].get('name') or slug,
                    hostnames=hostnames, is_default=False)
    section = Section.from_json(json_util.dumps(settings), created=True)
    section.trial_until = None
    section.validate()
    section.save()
    if payload.get('logo'):
        logo = payload['logo']
        section.logo_image.put(io.BytesIO(b64decode(logo['data'])), content_type=logo.get('content_type'),
                               filename=logo.get('filename'))
        section.save()

    grid = _profile_grid()
    mapping, created, merged = _import_users(payload, section, grid)

    for event in payload['events']:
        event['section'] = section.pk
        event['event_owners'] = [_remap(owner, mapping) for owner in event.get('event_owners') or []]
        for participation in event.get('participations') or []:
            if 'user' in participation:
                participation['user'] = _remap(participation['user'], mapping)
    for hut in payload['huts']:
        hut['section'] = section.pk
        hut['admins'] = [_remap(admin, mapping) for admin in hut.get('admins') or []]
    for booking in payload['hut_bookings']:
        booking['section'] = section.pk
        if 'user' in booking:
            booking['user'] = _remap(booking['user'], mapping)
    for key, model in (('events', Event), ('huts', Hut), ('hut_bookings', HutBooking)):
        if payload[key]:
            _collection(model).insert_many(payload[key])

    return section, {'events': len(payload['events']), 'huts': len(payload['huts']),
                     'hut_bookings': len(payload['hut_bookings']),
                     'users_created': created, 'users_merged': merged}
