"""
/api/v1: Touren ansehen, buchen, anlegen und bearbeiten, Teilnehmerliste,
Huetten. Antworten sind JSON, Fehler {"error": "Text fuer den Nutzer"}.
Zeiten sind lokale Zeiten der Sektion ohne Zone (wie in der Datenbank),
die Zone steht in /me.
"""
# pylint: disable=no-member, protected-access
import hmac
from datetime import datetime, date

from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user
from mongoengine.errors import DoesNotExist, ValidationError
from mongoengine.queryset.visitor import Q
from redis.exceptions import LockError

from application import csrf, limiter, login_manager
from application.auth.views import PASSWORD_LIMIT
from application.events import views as ev
from application.events.forms import COMMENT_MAX, CUSTOM_FIELD_MAX
from application.events.models import Event, difficulties, event_lock, event_types
from application.huts import views as hv
from application.huts.models import Hut, HutBooking
from application.models.api_token import ApiToken, token_hash
from application.models.section import current_section, ref_id
from application.models.user import User, delete_account
from application.models.website import Website
from application.modules.sanitize import sanitize_html

API = Blueprint('API', __name__, url_prefix='/api/v1')
csrf.exempt(API)

# Was ein Schluessel einer Website (scope booker) darf: Touren ansehen und buchen
BOOKER_ENDPOINTS = {'API.me', 'API.events', 'API.event', 'API.book', 'API.bookings'}

DIFFICULTY_KEYS = [x[0] for x in difficulties]
EVENT_TYPE_KEYS = [x[0] for x in event_types]
NAME_MAX = 200


def fail(status, message):
    """Fehlerantwort"""
    return jsonify({'error': message}), status


def bearer():
    """Schluessel aus dem Authorization-Header oder ''"""
    header = request.headers.get('Authorization', '')
    return header[7:].strip() if header[:7].lower() == 'bearer ' else ''


def token_limit_key():
    """Rate-Limits pro Schluessel, der Limiter laeuft vor der Anmeldung"""
    raw = bearer()
    return f"token:{token_hash(raw)}" if raw else f"ip:{request.remote_addr}"


@API.before_request
def token_login():
    """
    Konto aus dem Schluessel. Die Session zaehlt hier nicht: das Cookie
    eines offenen Browsers darf keine Buchung ausloesen.
    """
    g._login_user = login_manager.anonymous_user()
    raw = bearer()
    token = ApiToken.objects(token_hash=token_hash(raw)).first() if raw else None
    if not token or (token.expires and token.expires < datetime.now()):
        return fail(401, "Bitte melde dich in der App neu an.")
    section = current_section()
    if ref_id(token._data.get('section')) != str(section.pk):
        return fail(401, "Dieser Zugang gilt für eine andere Sektion.")
    user = User.objects(id=ref_id(token._data.get('user')), disabled__ne=True).first()
    if not user or not hmac.compare_digest(user.password_fingerprint(),
                                           token.password_fingerprint or ''):
        return fail(401, "Bitte melde dich in der App neu an.")
    if token.scope == 'booker':
        website_id = ref_id(token._data.get('website'))
        if not website_id or not Website.objects(id=website_id, section=section.pk).first():
            return fail(401, "Bitte melde dich neu an.")
        if request.endpoint not in BOOKER_ENDPOINTS:
            return fail(403, "Dieser Zugang darf nur Touren ansehen und buchen.")
    if not user.may_use_section(section):
        return fail(403, "Dein Konto gehört nicht zu dieser Sektion.")
    g._login_user = user
    g.api_booker = token.scope == 'booker'
    # Hoechstens einmal pro Stunde schreiben
    now = datetime.now()
    if not token.last_used or (now - token.last_used).total_seconds() > 3600:
        token.update(set__last_used=now)
    return None


def iso(value):
    """Datum/Zeit als ISO-Text oder None"""
    return value.isoformat() if value else None


def parse_datetime(value):
    """'YYYY-MM-DDTHH:MM[:SS]' -> datetime, leer -> None, sonst ValueError"""
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=None, microsecond=0)


def parse_day(value):
    """'YYYY-MM-DD' -> date oder None"""
    try:
        return date.fromisoformat(value or '')
    except ValueError:
        return None


def full_rights():
    """Angemeldet und nicht nur als Bucher einer Website"""
    return current_user.is_authenticated and not g.get('api_booker')


def categories():
    """Kategorien der Sektion: Schluessel (wie gespeichert), Name, Farbe"""
    return [{'key': x.name.lower(), 'name': x.name, 'color': x.color}
            for x in current_section().event_categories_full]


# Touren

def event_summary(event, now):
    """Was die Liste einer Tour braucht"""
    user = current_user.is_authenticated
    numbers = event.get_numbers()
    category = event.event_category if event.event_category not in (None, 'None') else None
    try:
        hut = event.hut if event._data.get('hut') else None
    except DoesNotExist:
        hut = None
    return {
        'id': str(event.id),
        'name': event.event_name,
        'teaser': event.event_teaser,
        'type': event.event_type,
        'category': category,
        'start': iso(event.start_date),
        'end': iso(event.end_date),
        'booking_from': iso(event.booking_from),
        'booking_until': iso(event.booking_until),
        'registration_open': ev.registration_open(event, now) and not event.is_started(now),
        'places': event.places or None,
        'free_places': event.free_places(),
        'confirmed': numbers['confirmed'],
        'unconfirmed': numbers['wait_for_confirm'],
        'waitlist': numbers['waitlist'],
        'difficulty': event.difficulty,
        'shape': event.shape,
        'length_h': event.length_h,
        'length_km': event.length_km,
        'altitude_difference': event.altitude_difference,
        'hut': hut.name if hut else None,
        'shared_from': event.shared_from(),
        # Ohne Konto (Website ohne Anmeldung) alles False
        'favorite': user and current_user.is_favorite(event.id),
        'booked': user and current_user.participate_event(event.id),
        'can_manage': full_rights() and current_user.may_manage_event(event),
        # Als Verantwortlicher eingetragen; can_manage gilt fuer Trainer und Admins ueberall
        'owner': user and str(current_user.id) in {ref_id(x) for x in event._data.get('event_owners') or []},
    }


def ticket_state(ticket):
    """confirmed, waitlist oder unconfirmed"""
    if ticket.confirmed:
        return 'confirmed'
    if ticket.waitinglist:
        return 'waitlist'
    return 'unconfirmed'


def my_tickets(event):
    """Eigene gebuchte Tickets einer Tour"""
    prices = {x.name: x.price for x in event.tickets}
    result = []
    if not current_user.is_authenticated:
        return result
    for parti in event.participations:
        if parti.account != current_user:
            continue
        for ticket in parti.tickets:
            price = ticket.custom_price if ticket.custom_price is not None \
                else prices.get(ticket.ticket_name, 0)
            result.append({
                'id': ticket.ticket_id,
                'ticket': ticket.ticket_name,
                'name': ticket.name_on_ticket,
                'email': ticket.email_on_ticket,
                'phone': ticket.phone_on_ticket,
                'birthdate': iso(ticket.birthdate_on_ticket),
                'is_extra': bool(ticket.is_extra_ticket),
                'state': ticket_state(ticket),
                'is_paid': bool(ticket.is_paid),
                'is_free': bool(ticket.is_free),
                'price': price or 0,
                'booking_ref': parti.booking_ref,
                'booking_date': iso(parti.booking_date),
            })
    return result


def event_detail(event, now):
    """Detailseite einer Tour mit Anmeldeformular und, fuer Verwalter, den Formularwerten"""
    layout = ev.booking_layout(event)
    data = event_summary(event, now)
    owner_section = event.owner_section()
    data.update({
        'description_html': sanitize_html(event.event_description or ''),
        'min_places': event.min_places or None,
        # E-Mail nur fuer angemeldete Konten, wie auf der Webseite
        'guides': [{'name': f"{x.first_name} {x.last_name}",
                    'email': x.email if current_user.is_authenticated else None}
                   for x in ev.event_guides(event)],
        'tickets': [{'index': idx, 'name': x.name, 'description': x.description,
                     'price': x.price or 0, 'maximum': x.maximum_tickets or 0,
                     'is_extra': bool(x.is_extra_ticket)}
                    for idx, x in enumerate(event.tickets)],
        'extras_per_person': layout.extras_per_person,
        'select_tickets': [x.name for x in layout.select_tickets],
        'questions': [x.field_name for x in event.custom_fields],
        'consent_html': sanitize_html(owner_section.media_consent_text or '')
                        if event.shared_from() and owner_section is not None else None,
        'my_tickets': my_tickets(event),
        'can_see_participants': full_rights() and current_user.may_see_participants(event),
    })
    if data['can_manage']:
        data['edit'] = {
            'name': event.event_name,
            'teaser': event.event_teaser,
            'type': event.event_type,
            'category': data['category'],
            'description_html': event.event_description or '',
            'places': event.places,
            'min_places': event.min_places,
            'start': iso(event.start_date),
            'end': iso(event.end_date),
            'booking_from': iso(event.booking_from),
            'booking_until': iso(event.booking_until),
            'difficulty': event.difficulty,
            'shape': event.shape,
            'length_h': event.length_h,
            'length_km': event.length_km,
            'altitude_difference': event.altitude_difference,
            'hut_id': ref_id(event._data.get('hut')),
            'hut_rooms': list(event.hut_rooms or []),
            'members_only': bool(event.members_only),
            'questions': [x.field_name for x in event.custom_fields],
            'tickets': [{'name': x.name, 'description': x.description, 'price': x.price or 0,
                         'maximum_tickets': x.maximum_tickets or 0,
                         'is_extra_ticket': bool(x.is_extra_ticket)} for x in event.tickets],
        }
    return data


def visible_event(event_id):
    """Tour, die man in dieser Sektion sehen kann, oder None"""
    try:
        return Event.visible(id=event_id).first()
    except ValidationError:
        return None


@API.route('/me')
def me():
    """Konto, Sektion und was das Konto darf"""
    section = current_section()
    return jsonify({
        'user': {'id': str(current_user.id), 'first_name': current_user.first_name,
                 'last_name': current_user.last_name, 'email': current_user.email,
                 'phone': current_user.phone, 'birthdate': iso(current_user.birthdate)},
        'section': {'name': section.name, 'slug': section.slug,
                    'color': section.nav_background_color or None},
        'timezone': current_app.config.get('TIMEZONE') or 'Europe/Berlin',
        'rights': {'create_events': full_rights() and current_user.has_right('guide'),
                   'manage_all_events': full_rights() and current_user.manages_all_events(),
                   'manage_huts': full_rights() and hv.can_manage_huts(current_user)},
        'categories': categories(),
        'has_huts': Hut.objects.count() > 0,
    })


@API.route('/me/delete', methods=['POST'])
@limiter.limit(PASSWORD_LIMIT, key_func=token_limit_key)
def delete_me():
    """Eigenes Konto loeschen, wie im Profil der Webseite: mit Passwort"""
    password = (request.get_json(silent=True) or {}).get('password')
    if current_user.global_admin:
        return fail(403, "Globale Admins können ihr Konto nicht selbst löschen.")
    if not isinstance(password, str) or not current_user.check_password(password):
        return fail(403, "Das Passwort ist falsch.")
    delete_account(current_user._get_current_object())
    return jsonify({'deleted': True})


@API.route('/events')
def events():
    """
    Touren der Sektion samt geteilter. scope=future (Vorgabe, bis zum Ende),
    past oder mine (gebucht oder verantwortlich, kommende).
    """
    now = datetime.now()
    scope = request.args.get('scope', 'future')
    future_q = Q(end_date__gte=now) | (Q(end_date=None) & Q(start_date__gte=now))
    if scope == 'past':
        found = Event.visible(Q(start_date__lt=now)).order_by('-start_date').limit(200)
    else:
        found = Event.visible(future_q).order_by('start_date')
    found = found.exclude('costs')
    result = [event_summary(x, now) for x in found]
    if scope == 'mine':
        result = [x for x in result if x['booked'] or x['owner']]
    return jsonify({'events': result})


@API.route('/events/<event_id>')
def event(event_id):
    """Eine Tour"""
    found = visible_event(event_id)
    if not found:
        return fail(404, "Diese Tour gibt es nicht (mehr).")
    return jsonify(event_detail(found, datetime.now()))


@API.route('/events/<event_id>/favorite', methods=['POST'])
def favorite(event_id):
    """Merken an/aus"""
    found = visible_event(event_id)
    if not found:
        return fail(404, "Diese Tour gibt es nicht (mehr).")
    return jsonify({'favorite': current_user.toggle_favorite(found)})


def booking_form(payload):
    """
    App-Anmeldung in die Felder des Webformulars uebersetzen, damit
    ev.book_event fuer beide gilt. Gibt (Felder, Listenfelder) zurueck.
    """
    form, lists = {}, {}
    for name, count in (payload.get('tickets') or {}).items():
        form[f"ticket_{name}"] = str(count)
    for person in payload.get('persons') or []:
        prefix = f"person_{int(person.get('ticket_index', 0))}_{int(person.get('slot', 0))}_"
        for key in ('name', 'email', 'phone', 'birthdate'):
            form[prefix + key] = str(person.get(key) or '')
        lists[f"extra_{int(person.get('ticket_index', 0))}_{int(person.get('slot', 0))}"] = \
            [str(int(x)) for x in person.get('extras') or []]
    for idx, answer in enumerate(payload.get('answers') or []):
        form[f"custom_{idx}"] = str(answer or '')
    form['comment'] = str(payload.get('comment') or '')
    if payload.get('media_optin'):
        form['media_optin_event'] = 'y'
    return form, lists


@API.route('/events/<event_id>/bookings', methods=['POST'])
@limiter.limit(ev.BOOKING_LIMIT, key_func=token_limit_key)
def book(event_id):
    """Anmelden, Regeln und Mails wie auf der Webseite"""
    found = visible_event(event_id)
    if not found:
        return fail(404, "Diese Tour gibt es nicht (mehr).")
    try:
        form, lists = booking_form(request.get_json(silent=True) or {})
    except (TypeError, ValueError, AttributeError):
        return fail(400, "Die Anmeldung ist unvollständig.")
    participation, errors = ev.book_event(found, form, lambda key: lists.get(key, []))
    if errors:
        return jsonify({'error': errors[0], 'errors': errors}), 400
    found.reload()
    return jsonify({'booking_ref': participation.booking_ref,
                    'event': event_detail(found, datetime.now())}), 201


@API.route('/bookings')
def bookings():
    """Eigene Anmeldungen, kommende zuerst"""
    now = datetime.now()
    ids = current_user._reference_ids('event_registrations')
    found = Event.visible(id__in=list(ids)).exclude('costs').order_by('start_date')
    result = []
    for event in found:
        tickets = my_tickets(event)
        if tickets:
            result.append({'event': event_summary(event, now), 'tickets': tickets})
    return jsonify({'bookings': result})


# Touren anlegen und bearbeiten

@API.route('/form')
def form_options():
    """Auswahllisten fuer das Tourformular"""
    if not current_user.has_right('guide'):
        return fail(403, "Touren anlegen dürfen nur Trainer und Tourenbegleiter.")
    return jsonify({
        'categories': categories(),
        'types': [{'key': key, 'name': name} for key, name in event_types],
        'difficulties': [{'key': key, 'name': name} for key, name in difficulties],
        'huts': [{'id': x['id'], 'name': x['name'],
                  'rooms': [room.name for room in x['rooms']]} for x in ev.hut_rooms_map()],
        'members_only_choice': ev.has_partner_sections(),
    })


def number(payload, key, default=None):
    """Ganze Zahl >= 0 aus dem JSON, sonst ValueError"""
    value = payload.get(key, default)
    if value in (None, ''):
        return default
    value = int(value)
    if value < 0:
        raise ValueError(key)
    return value


def event_values(payload, event):
    """
    JSON der App -> Werte fuer ev.apply_event, mit den Pruefungen des
    Webformulars. Fehlende Schluessel behalten den Wert der Tour.
    Gibt (Werte, Fehlertext) zurueck.
    """
    old = event_detail(event, datetime.now())['edit'] if event.id else {}
    merged = {**old, **payload}
    name = (merged.get('name') or '').strip()
    teaser = (merged.get('teaser') or '').strip()
    if not name or not teaser:
        return None, "Name und Teaser der Tour sind Pflicht."
    if len(name) > NAME_MAX or len(teaser) > NAME_MAX:
        return None, f"Name und Teaser dürfen höchstens {NAME_MAX} Zeichen lang sein."
    if merged.get('type') not in EVENT_TYPE_KEYS:
        return None, "Bitte einen Tourentyp wählen."
    category = merged.get('category') or None
    if category and category not in [x['key'] for x in categories()]:
        return None, "Diese Kategorie gibt es in der Sektion nicht."
    difficulty = merged.get('difficulty') or 'keine'
    shape = merged.get('shape') or 'keine'
    if difficulty not in DIFFICULTY_KEYS or shape not in DIFFICULTY_KEYS:
        return None, "Bitte Schwierigkeit und Kondition aus der Liste wählen."
    try:
        start = parse_datetime(merged.get('start'))
        dates = {key: parse_datetime(merged.get(key)) for key in ('end', 'booking_from', 'booking_until')}
    except (TypeError, ValueError):
        return None, "Ein Datum ist ungültig."
    if not start:
        return None, "Der Beginn der Tour ist Pflicht."
    try:
        places = number(merged, 'places', 0)
        fields = {'min_places': number(merged, 'min_places', 0)}
        for key in ('length_h', 'length_km', 'altitude_difference'):
            value = number(merged, key)
            fields[key] = str(value) if value is not None else None
    except (TypeError, ValueError):
        return None, "Plätze, Länge und Höhenmeter müssen ganze Zahlen sein."

    tickets = []
    for raw in merged.get('tickets') or []:
        ticket_name = (raw.get('name') or '').strip()
        if not ticket_name:
            continue
        try:
            price = ev.parse_price(raw.get('price') or 0)
            maximum = number(raw, 'maximum_tickets', 0)
        except (TypeError, ValueError):
            return None, f"Preis oder Anzahl bei {ticket_name} ist ungültig."
        if price < 0:
            return None, f"Der Preis bei {ticket_name} darf nicht negativ sein."
        tickets.append({'name': ticket_name[:NAME_MAX], 'description': (raw.get('description') or '')[:500],
                        'price': price, 'maximum_tickets': maximum,
                        'is_extra_ticket': bool(raw.get('is_extra_ticket'))})
    if len({x['name'] for x in tickets}) != len(tickets):
        return None, "Zwei Tickets haben denselben Namen."
    if tickets and all(x['is_extra_ticket'] for x in tickets):
        return None, "Mindestens ein Ticket darf kein Zusatzticket sein."
    # Wie im Webformular: ein einzelnes Ticket ohne Anzahl gilt fuer alle Plaetze
    if len(tickets) == 1 and not tickets[0]['maximum_tickets']:
        tickets[0]['maximum_tickets'] = places

    hut_id = merged.get('hut_id') or None
    if hut_id and hut_id not in [x['id'] for x in ev.hut_rooms_map()]:
        return None, "Diese Hütte gibt es in der Sektion nicht."
    questions = [str(x).strip()[:CUSTOM_FIELD_MAX] for x in merged.get('questions') or []
                 if str(x or '').strip()]

    fields.update({
        'event_name': name,
        'event_teaser': teaser,
        'event_type': merged['type'],
        'event_category': category or 'None',
        'event_description': merged.get('description_html') or '',
        'places': places,
        'difficulty': difficulty,
        'shape': shape,
    })
    return {
        'fields': fields,
        'members_only': bool(merged.get('members_only')),
        'add_guide': bool(merged.get('add_guide')),
        'start_date': start,
        'end_date': dates['end'],
        'booking_from': dates['booking_from'],
        'booking_until': dates['booking_until'],
        'hut_id': hut_id,
        'hut_rooms': [str(x) for x in merged.get('hut_rooms') or []],
        'custom_fields': questions,
        'tickets': tickets,
    }, None


@API.route('/events', methods=['POST'])
def create_event():
    """Neue Tour"""
    if not current_user.has_right('guide'):
        return fail(403, "Touren anlegen dürfen nur Trainer und Tourenbegleiter.")
    new = Event()
    values, error = event_values(request.get_json(silent=True) or {}, new)
    if error:
        return fail(400, error)
    ev.apply_event(new, values)
    return jsonify(event_detail(new, datetime.now())), 201


@API.route('/events/<event_id>', methods=['PUT'])
def update_event(event_id):
    """Tour aendern, nur wer sie verwalten darf"""
    found = visible_event(event_id)
    if not found or not current_user.may_manage_event(found):
        return fail(403, "Diese Tour darfst du nicht bearbeiten.")
    values, error = event_values(request.get_json(silent=True) or {}, found)
    if error:
        return fail(400, error)
    try:
        with event_lock(event_id):
            found.reload()
            ev.apply_event(found, values)
    except LockError:
        return fail(503, "Die Tour wird gerade bearbeitet. Bitte versuche es gleich noch einmal.")
    return jsonify(event_detail(found, datetime.now()))


# Teilnehmerliste

@API.route('/events/<event_id>/participants')
def participants(event_id):
    """Teilnehmer wie auf der Webseite: Kontaktdaten nur mit Freigabe oder fuer Verwalter"""
    found = visible_event(event_id)
    if not found or not current_user.may_see_participants(found):
        return fail(403, "Die Teilnehmerliste dieser Tour ist für dich nicht sichtbar.")
    manage = current_user.may_manage_event(found)
    groups = {}
    for state, rows in ev.get_participants(found).items():
        groups[state] = []
        for row in rows:
            info = row['ticket_info']
            show_contact = manage or info['data_optin']
            entry = {
                'id': row['id'],
                'name': row['ticket_owner'],
                'ticket': info['name'],
                'is_extra': bool(row['is_extra_ticket']),
                # Zusatzticket einer Person: id ihres Haupt-Tickets
                'main_ticket_id': row['main_ticket_id'],
                'booker': info['bucher'],
                'phone': info['telefon'] if show_contact else None,
                'email': info['email'] if show_contact else None,
            }
            if manage:
                entry.update({
                    'booking_ref': info['booking_ref'],
                    'booking_date': iso(row['booking_date']),
                    'media_optin': bool(info['media_optin']),
                    'role': info['role'],
                    'age': ev.age(info['birthdate']) if info['birthdate'] else None,
                    'answers': [{'question': q, 'answer': a} for q, a in row['extra_questions']],
                    'comment': info['comment'],
                    'guide_comment': info['guide_comment'],
                    'is_paid': bool(row['is_paid']),
                    'is_free': bool(row['is_free']),
                    'is_transfer': bool(row['is_transfer']),
                    'price': row['price'] or 0,
                })
            groups[state].append(entry)
    return jsonify({'can_manage': manage, 'groups': groups})


PARTICIPANT_JOBS = ('confirm', 'waitlist', 'delete', 'update_guide_comment')
PAID_JOBS = ('paid', 'unpaid', 'free', 'unfree', 'transfer', 'untransfer')


@API.route('/events/<event_id>/participants/<ticket_id>', methods=['POST'])
def change_participant(event_id, ticket_id):
    """Ticket bestaetigen, auf die Warteliste, loeschen, kommentieren oder Zahlstand setzen"""
    payload = request.get_json(silent=True) or {}
    action = payload.get('action')
    if action not in PARTICIPANT_JOBS + PAID_JOBS:
        return fail(400, "Unbekannte Aktion.")
    found = visible_event(event_id)
    if not found or not current_user.may_manage_event(found):
        return fail(403, "Diese Tour darfst du nicht verwalten.")
    try:
        with event_lock(event_id):
            found.reload()
            ticket = found.get_booked_ticket(ticket_id)
            if not ticket:
                return fail(404, "Dieses Ticket gibt es nicht (mehr).")
            if action in PAID_JOBS:
                ev.set_paid_status(ticket, action)
                found.save()
            else:
                comment = str(payload.get('guide_comment') or '')[:COMMENT_MAX]
                ev.participant_job(found, ticket, action, {'guide_comment': comment})
    except LockError:
        return fail(503, "Die Tour wird gerade bearbeitet. Bitte versuche es gleich noch einmal.")
    return participants(event_id)


# Huetten

def hut_summary(hut):
    """Was die Huettenliste braucht"""
    return {
        'id': str(hut.id),
        'name': hut.name,
        'region': hut.region,
        'total_places': hut.total_places(),
        'self_booking': bool(hut.allow_self_booking),
        'requires_approval': bool(hut.requires_approval),
        'can_manage': hut.can_manage(current_user),
    }


def hut_booking(booking, with_name=True):
    """Eine Huettenbuchung"""
    return {
        'id': str(booking.id),
        'hut_id': ref_id(booking._data.get('hut')),
        'name': booking.name if with_name else None,
        'from': iso(booking.from_date),
        'to': iso(booking.to_date),
        'places': booking.places or 0,
        'rooms': list(booking.rooms or []),
        'comment': booking.comment,
        'confirmed': bool(booking.confirmed),
        'blocked': bool(booking.blocked),
        'own': ref_id(booking._data.get('user')) == str(current_user.id),
    }


def find_hut(hut_id):
    """Huette der Sektion oder None"""
    try:
        return Hut.objects(id=hut_id).first()
    except ValidationError:
        return None


@API.route('/huts')
def huts():
    """Huetten der Sektion"""
    return jsonify({'huts': [hut_summary(x) for x in Hut.objects.order_by('name')]})


@API.route('/huts/<hut_id>')
def hut(hut_id):
    """
    Huette mit Zimmern, eigenen Buchungen und fuer Verwalter den offenen
    Anfragen. Mit from/to (YYYY-MM-DD) auch die freien Plaetze im Zeitraum.
    """
    found = find_hut(hut_id)
    if not found:
        return fail(404, "Diese Hütte gibt es nicht.")
    data = hut_summary(found)
    data.update({
        'managed': bool(found.managed),
        'contact': found.contact,
        'phone': found.phone,
        'link': found.link,
        'note': found.note,
        'rooms': [{'name': x.name, 'places': x.places or 0} for x in found.rooms if x.name],
        'my_bookings': [hut_booking(x) for x in
                        HutBooking.objects(hut=found, user=current_user).order_by('from_date')],
    })
    from_date, to_date = parse_day(request.args.get('from')), parse_day(request.args.get('to'))
    if from_date and to_date and from_date < to_date:
        data['range'] = {
            'free_places': found.free_places(from_date, to_date) if found.has_capacity() else None,
            'blocked': [x.comment or "Gesperrt" for x in found.blocks(from_date, to_date)],
        }
    if data['can_manage']:
        data['pending'] = [hut_booking(x) for x in
                           HutBooking.objects(hut=found, confirmed=False).order_by('from_date')]
    return jsonify(data)


@API.route('/huts/<hut_id>/calendar')
def hut_calendar(hut_id):
    """Belegung pro Tag eines Monats (year, month), Namen nur fuer Verwalter"""
    found = find_hut(hut_id)
    if not found:
        return fail(404, "Diese Hütte gibt es nicht.")
    today = datetime.now().date()
    try:
        year = int(request.args.get('year') or today.year)
        month = int(request.args.get('month') or today.month)
    except ValueError:
        year, month = today.year, today.month
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        year, month = today.year, today.month
    manage = found.can_manage(current_user)
    days = []
    for week in hv._occupancy_weeks(found, year, month, today):
        for day in week:
            if not day['in_month']:
                continue
            days.append({'date': day['iso'], 'used': day['used'], 'free': day['free'],
                         'level': day['level'], 'blocked': day['blocked'],
                         'block_reason': day['block_reason'],
                         'entries': day['entries'] if manage else []})
    return jsonify({'year': year, 'month': month, 'total_places': found.total_places(), 'days': days})


@API.route('/huts/<hut_id>/bookings', methods=['POST'])
@limiter.limit(ev.BOOKING_LIMIT, key_func=token_limit_key)
def book_hut(hut_id):
    """Plaetze buchen, Regeln wie auf der Webseite"""
    found = find_hut(hut_id)
    if not found:
        return fail(404, "Diese Hütte gibt es nicht.")
    payload = request.get_json(silent=True) or {}
    try:
        places = int(payload.get('places') or 0)
    except (TypeError, ValueError):
        places = 0
    rooms = [str(x) for x in payload.get('rooms') or [] if x]
    comment = str(payload.get('comment') or '') or None
    booking, error = hv.book_hut_for(found, current_user, parse_day(payload.get('from')),
                                     parse_day(payload.get('to')), places, rooms, comment)
    if error:
        return fail(400, error)
    return jsonify(hut_booking(booking)), 201


@API.route('/huts/<hut_id>/bookings/<booking_id>/<action>', methods=['POST'])
def change_hut_booking(hut_id, booking_id, action):
    """cancel: eigene stornieren (Verwalter jede), confirm: Verwalter gibt frei"""
    found = find_hut(hut_id)
    try:
        booking = HutBooking.objects(id=booking_id, hut=found).first() if found else None
    except ValidationError:
        booking = None
    if not booking or action not in ('cancel', 'confirm'):
        return fail(404, "Diese Buchung gibt es nicht (mehr).")
    is_own = booking._data.get('user') is not None \
        and ref_id(booking._data.get('user')) == str(current_user.id)
    manage = found.can_manage(current_user)
    if action == 'cancel' and (is_own or manage):
        booking.delete()
        return jsonify({'cancelled': True})
    if action == 'confirm' and manage:
        booking.update(set__confirmed=True)
        booking.reload()
        return jsonify(hut_booking(booking))
    return fail(403, "Das darfst du bei dieser Buchung nicht.")


@API.route('/huts/bookings')
def my_hut_bookings():
    """Eigene Huettenbuchungen ab heute"""
    today = datetime.now().date()
    found = HutBooking.objects(user=current_user, to_date__gte=today).order_by('from_date')
    names = {str(x.id): x.name for x in Hut.objects}
    return jsonify({'bookings': [{**hut_booking(x), 'hut': names.get(ref_id(x._data.get('hut')))}
                                 for x in found]})
