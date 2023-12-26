"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member, too-many-locals, too-many-branches, import-error, too-few-public-methods, too-many-statements

from datetime import datetime, timedelta
import uuid
import io, csv
from flask import request, render_template, \
     flash, redirect, Blueprint, url_for, abort, make_response
from flask_login import current_user, login_required
from wtforms import StringField, SelectField
from wtforms.validators import InputRequired


from application.events.models import Event, EventParticipation,\
                             CustomField, CustomFieldDefintion, Ticket, OwnedTicket, difficulties
from application.models.user import roles
from application.auth.forms import LoginForm
from application.auth.views import do_login
from application.events.forms import EventForm, EventRegisterForm, EventSearchForm
from application.models.config import Config

roles_dict = dict(roles)

EVENTS = Blueprint('EVENTS', __name__)

#   . Helpers
def difficult_to_icon(level, icon_type):
    """
    Format Intod Images
    """
    length = 5
    if icon_type == 1:
        ia = "■"
        ib = "□"
    else:
        ia = "●"
        ib = "○"
    if not level:
        level = 'sehr leicht'
    index = list([x[0] for x in difficulties]).index(level)+1

    output = ""
    for i in range(length):
        if i < index:
            output += ia
        else:
            output += ib

    return output

class DictObj:
    """
    Helper to conver dict to object
    """
    def __init__(self, in_dict:dict):
        assert isinstance(in_dict, dict)
        for key, val in in_dict.items():
            if isinstance(val, (list, tuple)):
                setattr(self, key, [DictObj(x) if isinstance(x, dict) else x for x in val])
            else:
                setattr(self, key, DictObj(val) if isinstance(val, dict) else val)
#.
#   . Populate Event Form
ticket_template = DictObj({
                    'name': None,
                    'description': None,
                    'maximum_tickets': 0,
                    'price': 0.0
                    })

custom_question_template = DictObj({'field_name': None})

def populate_event_form(form, event):
    """
    Since the Event Form needs some preperations,
    We do this in a central place

    Beware: Donst save event object after this function
    """
    # Add Template Field
    event.custom_fields.insert(0, custom_question_template)
    event.tickets.insert(0, ticket_template)

    form.populate_obj(event)
    return form
#.
#   . save_event_form
def save_event_form(event):
    """
    Helper to store Event Object
    """
    form_data = dict(request.form)
    for field, value in form_data.items():
        # Set all default field which not here excluded
        if field in ['start_date', 'end_date', 'booking_from', 'booking_until']:
            continue
        if field in ['waitlist',]:
            value = bool(value)
        setattr(event, field, value)

    if current_user not in event.event_owners and form_data.get('add_guide'):
        event.event_owners.append(current_user)


    start_datetime_str = f"{request.form['start_date']} {request.form.get('start_time', '00:00')}"
    end_datetime_str = f"{request.form['end_date']} {request.form.get('end_time', '00:00')}"
    event.start_date = start_datetime_str
    event.end_date = end_datetime_str

    if request.form.get('booking_from'):
        book_start_datetime_str = f"{request.form['booking_from']} {request.form.get('booking_from_time', '00:00')}"
        event.booking_from = book_start_datetime_str
    if request.form.get('booking_until'):
        book_end_datetime_str = f"{request.form['booking_until']} {request.form.get('booking_until_time', '00:00')}"
        event.booking_until = book_end_datetime_str


    event.custom_fields = []
    event.tickets = []
    ticket_collector = {}
    for key, value in request.form.items():
        if key.startswith('tickets-0') or key.startswith('custom_fields-0'):
            # skip the hidden templates
            continue
        if key.startswith('custom_fields-') and not key.endswith("csrf_token"):
            if value:
                new_field = CustomFieldDefintion()
                new_field.field_name = value
                event.custom_fields.append(new_field)
        if key.startswith('tickets-') and not key.endswith("csrf_token"):
            splited = key[8:].split('-')
            group = splited[0]
            name = "-".join(splited[1:])
            if value and name:
                ticket_collector.setdefault(group, {})
                ticket_collector[group][name] = value
    overwrite_max_tickets = False
    if len(ticket_collector) == 1:
        overwrite_max_tickets = request.form['places']

    ## Handle Tickets
    for t_data in ticket_collector.values():
        if not t_data.get('name'):
            continue
        ticket = Ticket()
        ticket.name = t_data['name']
        ticket.price = float(t_data.get('price', 0))
        ticket.description = t_data.get('description')
        if overwrite_max_tickets and int(t_data.get('maximum_tickets', 0)) == 0:
            ticket.maximum_tickets = overwrite_max_tickets
        else:
            ticket.maximum_tickets = t_data.get('maximum_tickets', 0)
        event.tickets.append(ticket)

    event.save()
    return True
#.
#   . Event My Booking Page


@EVENTS.route('/user/change_ticket', methods=['POST'])
def ajax_mybooking():
    """
    Save Ajax Data for Ticket Changes
    """
    event_id = request.form['event_id']
    new_name = request.form['new_name']
    ticket_id = request.form['ticket_id']

    event = Event.objects.get(id=event_id)
    ticket = event.get_booked_ticket(ticket_id, current_user)
    ticket.name_on_ticket = new_name
    event.save()
    return {'msg': 'success'}


@EVENTS.route('/user/booking')
def page_mybooking():
    """
    Detail Page for Event
    """
    event_id = request.args.get('event_id')
    event = Event.objects.get(id=event_id)
    participations = []
    for parti in event.participations:
        if parti.user == current_user:
            participations.append(parti)

    context = {
        'event': event,
        'participations': participations
    }


    return render_template('user_booking.html', **context)

#.
#   . Event List Page
@EVENTS.route('/', methods=['POST', 'GET'])
def page_list():
    """
    Public Page with Events
    """
    categories = [(None, "Kategorie")] + [(x.lower(), x) for x in sorted(Config.objects(enabled=True)[0].event_categories)]
    mode = 'page'
    context = {}
    search_form = EventSearchForm(request.form)
    search_form.filter_category.choices = categories 
    now = datetime.now()
    filters = {}
    filter_names = []
    filter_expr = {}
    search = False


    if search_form.validate_on_submit():
        filters = request.form
        search = True

    if request.args.get('filter_category'):
        filters['filter_future'] = 'y'
        filters['filter_category'] = request.args['filter_category']
        search = True

    if filters.get('export'):
        mode = 'export'

    if not search:
        filters['filter_future'] = 'y'
        filters['filter_category'] = 'None'
        search_form.filter_future.data = 'y'
        if request.args.get('filter') == 'my_events':
            filters['filter_own'] = 'y'
            search_form.filter_own.data = 'y'

    # We need to check this filte
    filter_date = filters.get('filter_date')
    filter_future = filters.get('filter_future')



    if filter_name := filters.get('filter_name'):
        filter_expr['event_name__icontains'] = filter_name
        filter_names.append(f"Name enthält: {filter_name}")

    filter_category = filters.get('filter_category')
    if filter_category != "None":
        filter_expr['event_category'] = filter_category
        filter_names.append(f"Kategorie ist: {dict(categories).get(filter_category, filter_category)}")


    if filter_date:
        # If Date Filteres,
        # Disable future Filter
        filter_future = False
        search_form.filter_future.data = False
        date_start = datetime.strptime(filter_date, "%Y-%m-%d")
        date_end = datetime.strptime(filter_date, "%Y-%m-%d") + timedelta(hours=24)
        filter_expr['start_date__gte'] = date_start
        filter_expr['start_date__lte'] = date_end
        filter_names.append(f"Zeitpunkt: {filter_date}")

    if filter_future == 'y':
        filter_expr['start_date__gte'] = now
        filter_names.append("Zeitpunkt: Zukünftige")

    events = Event.objects(**filter_expr).order_by('start_date')
    result = []
    if filters.get('filter_own') and current_user.is_authenticated:
        filter_names.append("Angemeldet")
        for event in events:
            if event in current_user.event_registrations or \
                current_user in event.event_owners:
                result.append(event)
    else:
        result = events
    context['events'] = result
    context['render_dificulty'] = difficult_to_icon

    if filter_names:
        context['header'] = f"Filter: {', '.join(filter_names)}"
    else:
        context['header'] = "Events"
    context['event_categories'] = dict(categories)
    context['search_form'] = search_form


    if mode == 'page':
        return render_template('event_list.html', **context)
    elif mode == 'export':
        return render_template('event_list_export.html', **context)
#.
#   . Event Admin Page

def event_populate(event):
    """
    Hepler to have always all needed fields to the event form
    """
    # We store DateTime Fields,
    # but the Form useses two seperate ones:
    event.start_time = event.start_date
    event.end_time = event.end_date
    event.booking_until_time = event.booking_until
    event.booking_from_time = event.booking_from

    event.custom_fields.insert(0, custom_question_template)
    event.tickets.insert(0, ticket_template)

    return event

@EVENTS.route('/event/admin', methods=['GET', 'POST'])
def page_admin():
    """
    Admin Page
    """
    event_id = request.args.get('event_id')
    event = Event.objects.get(id=event_id)
    if not current_user.has_right('guide'):
        abort(403)

    categories = [(None, "Kategorie")] + [(x.lower(), x) for x in Config.objects(enabled=True)[0].event_categories]

    if request.form:
        form = EventForm(request.form)
        form.event_category.choices = categories
    else:
        event = event_populate(event)

        form = EventForm(obj=event)
        form.event_category.choices = categories
        form = populate_event_form(form, event)
    if form.validate_on_submit():
        save_event_form(event)
        flash("Event wurde aktualisiert", 'info')
        return redirect(url_for('EVENTS.page_details', event_id=event_id))

    if form.errors:
        print(form.errors, flush=True)
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

    context = {
        'form': form
    }

    context['event'] = event
    return render_template('event_form.html', **context)
#.
#   . Event Participation list  Page


@EVENTS.route('/event/change_participants', methods=['POST'])
def change_participation():
    """
    Helper
    """
    if not current_user.has_right('guide'):
        abort(403)

    job = request.form['job']
    ticket_id = request.form['ticket_id']
    event_id = request.form['event_id']

    event = Event.objects.get(id=event_id)
    ticket = event.get_booked_ticket(ticket_id)

    response = {}

    if job == 'confirm':
        ticket.confirmed = True
        ticket.waitinglist = False
    elif job == 'waitlist':
        ticket.waitinglist = True
        ticket.confirmed = False
    elif job == 'delete':
        response = event.delete_ticket(ticket_id)

    event.save()

    return response


def get_participants(event):
    """
    Get List of Participants
    """
    bookings = {
        'confirmed' : [],
        'waitinglist' : [],
        'unconfirmed' : [],
    }
    for parti in event.participations:
        for ticket in parti.tickets:
            what = 'unconfirmed'
            if ticket.confirmed:
                what = 'confirmed'
            elif ticket.waitinglist:
                what = 'waitinglist'

            extra_questions = []
            for field in event.custom_fields:
                extra_questions.append((field.field_name, parti.get_field(field.field_name)))

            bookings[what].append({
                'id': ticket.ticket_id,
                'ticket_owner': ticket.name_on_ticket,
                'booking_date': parti.booking_date,
                'ticket_info': {
                    'name': ticket.ticket_name,
                    'bucher': f"{parti.user.first_name} {parti.user.last_name}",
                    'telefon': parti.user.phone,
                    'email': parti.user.email,
                    'media_optin': parti.user.media_optin,
                    'data_optin': parti.user.data_optin,
                    'role': roles_dict[parti.user.role],
                    'comment': parti.comment,
                },
                'extra_questions': extra_questions

            })
    return bookings


@EVENTS.route('/event/participants/export')
def page_participants_export():
    """
    Export Participants
    """
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))

    if not current_user.has_right('guide'):
        abort(403)

    event_id = request.args.get('event_id')
    event = Event.objects.get(id=event_id)
    participants = get_participants(event)['confirmed']
    io_output = io.StringIO()
    writer = csv.writer(io_output, delimiter=';' )

    extra_question_headers = \
            [ x[0] for x in participants[0]['extra_questions']]

    writer.writerow([
      'Bucher',
      'Buchungs Datum',
      'Teilnehmer',
      'Telefon',
      'E-Mail',
      'Medien Optin',
      'Daten Optin',
      'Vereinsrolle',
      'Kommentar',

    ]+ extra_question_headers)
    for line in participants:
        writer.writerow([
            line['ticket_info']['bucher'],
            line['booking_date'],
            line['ticket_owner'],
            line['ticket_info']['telefon'],
            line['ticket_info']['email'],
            line['ticket_info']['media_optin'],
            line['ticket_info']['data_optin'],
            line['ticket_info']['role'],
            line['ticket_info']['comment'],
        ]+ [x[1] for x in line['extra_questions']])

    output = make_response(io_output.getvalue())
    filename = event.event_name.replace(' ', '_')
    output.headers["Content-Disposition"] = f"attachment; filename={filename}.csv"
    output.headers["Content-type"] = "text/csv"
    return output


@EVENTS.route('/event/participants')
def page_participants():
    """
    Participants Page
    """

    event_id = request.args.get('event_id')

    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))

    if not current_user.has_right('guide') and \
        not current_user.participate_event(event_id):
        abort(403)

    event = Event.objects.get(id=event_id)
    context = {}


    emails = []
    participants = get_participants(event)
    for part in participants['confirmed']:
        email = part['ticket_info']['email']
        if email not in emails:
            emails.append(email)

    context['event'] = event
    context['emails'] = emails
    context['bookings'] = get_participants(event)
    return render_template('event_participants.html', **context)
#.
#   . Event Details Page

@EVENTS.route('/event/details', methods=['GET', 'POST'])
def page_details():
    """
    Detail Page for Event
    """
    event_id = request.args.get('event_id')

    event = Event.objects.get(id=event_id)
    login_form = LoginForm(request.form)

    class EventRegForm(EventRegisterForm):
        """
        Event Reg Form
        """

    categories = [(None, "Kategorie")] + [(x.lower(), x) for x in sorted(Config.objects(enabled=True)[0].event_categories)]
    # Add Custom Fields to Registration Form
    custom_fields = event.custom_fields
    for idx, field in enumerate(custom_fields):
        print(idx, flush=True)
        setattr(EventRegForm, f"custom_{idx}",
                    StringField(field.field_name, validators=[InputRequired()]))


    ticket_stats = event.get_ticket_stats()

    # Handle Ticket function
    event_tickets = event.tickets
    for ticket in event_tickets:
        choices = [ (str(x), f'{x} Plätze') for x in range(ticket.maximum_tickets+1) ]
        places = ticket_stats['max'][ticket.name] - ticket_stats.get(ticket.name, 0)
        preisinfo = ""
        if ticket.price > 0:
            preisinfo = f"(je Platz: {ticket.price}  €)"
        setattr(EventRegForm, f"ticket_{ticket.name}",
                    SelectField(f"'{ticket.name}' {preisinfo} aktuell noch {places}/{ticket_stats['max'][ticket.name]}", choices=choices))


    register_form = EventRegForm(request.form)

    numbers = event.get_numbers()

    def format_ticket(lines):
        """ Format Helper for EmbeddedDocument
        """
        table = []
        for line in lines:
            table.append([
              ('Name', line.name),
              ('Beschreibung', line.description),
              ('Preis Euro', line.price),
              ('Anzahl', line.maximum_tickets),
            ])
        return table

    detail_fields = [
      ("Kategorie", dict(categories).get(event.event_category), 'string'),
      ("Schwierigkeit", difficult_to_icon(event.difficulty, 1), 'string'),
      ("Kondition", difficult_to_icon(event.shape, 2), 'string'),
      ("Plätze insgesammt", numbers['total_places'], 'string'),
    ]

    if event.min_places:
        detail_fields += [
            ('Mindestteilnehmerzahl', event.min_places, 'string'),
        ]


    detail_fields += [
      ("Plätze bestätigt", numbers['confirmed'], 'string'),
      ("Plätze unbestätigt", numbers['wait_for_confirm'], 'string'),
      ("Auf Warteliste", numbers['waitlist'], 'string'),
    ]
    if event.booking_from:
        detail_fields += [
          ("Buchbar ab" , event.booking_from.strftime("%d.%m.%Y %H:%M "), 'string'),
        ]
    if event.booking_until:
        detail_fields += [
          ("Buchbar bis" , event.booking_until.strftime("%d.%m.%Y %H:%M "), 'string'),
        ]
    detail_fields += [
      ("Start",  event.start_date.strftime("%d.%m.%Y"), 'string'),
      ("Zeit am Treffpunkt" , event.start_date.strftime("%H:%M"), 'string'),
      ("Ende" , event.end_date.strftime("%d.%m.%Y %H:%M "), 'string'),
      ('Länge in km', event.length_km, 'string'),
      ('Höhenmeter', event.altitude_difference, 'string'),
      ('Dauer in Stunden', event.length_h, 'string'),
      ('Tickets', format_ticket(event.tickets), 'table'),
    ]

    event_details = {}
    for title, data, mode in detail_fields:
        if data:
            event_details[title] = (data, mode)


    event_details_list = []
    higest_level = 'attendant'
    for guide in event.event_owners:
        if guide.role == 'guide':
            higest_level = 'guide'
        event_details_list.append(
            (f'Verantwortlicher {roles_dict[guide.role]}', f"{guide.first_name} {guide.last_name}, {guide.email}", "string")
        )


    event_details_list += [(x, y[0], y[1]) for x, y in event_details.items()]

    now = datetime.now()
    registration_enabled = True
    if event.booking_until and event.booking_from:
        registration_enabled = event.booking_until >= now >= event.booking_from

    context = {
        'event' : event,
        'higest_guide_level' : higest_level,
        'registration_enabled': registration_enabled,
        'event_custom_fields': [(str(x), y) for x, y in enumerate(event.custom_fields)],
        'event_ticket_ids': [x.name for x in event.tickets],
        'event_id': event_id,
        'event_details' : event_details_list,
        'LoginForm': login_form,
        'regform': register_form,
    }

    if current_user.is_authenticated and register_form.validate_on_submit():
        data = request.form

        register_possible = True

        # Count
        if event.start_date < now:
            flash("Das Event hat bereits stattgefunden", 'danger')
            register_possible = False

        if not registration_enabled:
            flash("Die Anmeldung auf das Event ist noch nicht freigeschaltet", 'danger')
            register_possible = False


        free_seats = {}
        ticket_data = {}
        wanted_seats = {}
        ticket_stats = event.get_ticket_stats() # Update data
        for ticket in event_tickets:
            wanted = int(data[f'ticket_{ticket.name}'])
            if wanted == 0:
                continue
            places = ticket_stats['max'][ticket.name] - ticket_stats.get(ticket.name, 0)
            free_seats[ticket.name] = places - wanted
            wanted_seats[ticket.name] = wanted
            ticket_data[ticket.name] = {'name': ticket.name, 'desc': ticket.description}

        if not wanted_seats:
            flash("Du hast keine Ticket Plätze gewählt.", 'danger')
            register_possible = False


        if register_possible:
            current_user.add_event(event)

            new_participation = EventParticipation()
            new_participation.booking_date = now
            for idx, custom_field_def in enumerate(custom_fields):
                field_name = custom_field_def.field_name
                field_id = f"custom_{idx}"
                custom_field = CustomField()
                custom_field.name = field_name
                custom_field.value = data[field_id]
                new_participation.custom_fields.append(custom_field)

            for ticket_name, num in wanted_seats.items():
                waitinglist = True
                for _ in range(num):
                    if free_seats[ticket_name] >= 0:
                        waitinglist = False
                    free_seats[ticket_name] -= 1

                    ticket_id = uuid.uuid4().hex

                    ticket = OwnedTicket()
                    ticket.ticket_id = ticket_id
                    ticket.ticket_name = ticket_name
                    ticket.ticket_comment = ticket_data[ticket_name]['desc']
                    ticket.confirmed = False
                    ticket.name_on_ticket = f"{current_user.first_name} {current_user.last_name}"
                    ticket.waitinglist = waitinglist
                    new_participation.tickets.append(ticket)


            new_participation.comment = data['comment']
            new_participation.user = current_user
            new_participation.waitinglist = waitinglist
            Event.objects(id=event_id).update_one(push__participations=new_participation)
            event.reload()
            event.save()
            return redirect(url_for('EVENTS.page_mybooking', event_id=str(event.id)))

    if not current_user.is_authenticated:
        if login_form.validate_on_submit():
            do_login(login_form, context)

    if register_form.errors:
        flash(f"Bitte behebe die angezeigten Fehler in den Feldern {register_form.errors}", 'danger')

    return render_template('event_details.html', **context)
#.
#   . Event Create Page
@EVENTS.route('/create', methods=['GET', 'POST'])
@login_required
def page_create():
    """
    Create New Event Page
    """
    if not current_user.has_right('guide'):
        abort(403)

    event_id = request.args.get('event_id')
    categories = [(None, "Kategorie")] + [(x.lower(), x) for x in sorted(Config.objects(enabled=True)[0].event_categories)]

    if event_id and not request.form:
        # Make it possible to clone a event
        # This populates the form which can be saved as new
        event = Event.objects.get(id=event_id)
        event = event_populate(event)

        form = EventForm(obj=event)
        form.event_category.choices = categories
        form = populate_event_form(form, event)
    else:
        form = EventForm(request.form)
        form.event_category.choices = categories

    if form.validate_on_submit():
        new_event = Event()
        save_event_form(new_event)
        flash("Event wurde erzeugt", 'info')
        return redirect(url_for('EVENTS.page_details', event_id=str(new_event.id)))

    if form.errors:
        flash(f"Bitte behebe die angezeigten Fehler in den Feldern({form.errors})", 'danger')

    context = {
        'form': form
    }

    return render_template('event_form.html', **context)
#.
