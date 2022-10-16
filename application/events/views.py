"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member, too-many-locals, too-many-branches,

from datetime import datetime, timedelta
import uuid
from flask import request, render_template, \
     flash, redirect, Blueprint, url_for, abort
from flask_login import current_user, login_required
from wtforms import StringField, SelectField
from wtforms.validators import InputRequired


from application.events.models import Event, EventParticipation,\
                            categories, CustomField, CustomFieldDefintion, Ticket, OwnedTicket
from application.auth.forms import LoginForm
from application.auth.views import do_login
from application.events.forms import EventForm, EventRegisterForm, EventSearchForm 

EVENTS = Blueprint('EVENTS', __name__)

#   . Helpers
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
    if current_user not in event.event_owners:
        event.event_owners.append(current_user)
    for field, value in dict(request.form).items():
        if field in ['start_date', 'end_date', 'booking_from', 'booking_until']:
            continue
        if field in ['waitlist',]:
            value = bool(value)
        setattr(event, field, value)
    start_datetime_str = f"{request.form['start_date']} {request.form['start_time']}"
    end_datetime_str = f"{request.form['end_date']} {request.form['end_time']}"
    book_start_datetime_str = f"{request.form['booking_from']} {request.form['booking_from_time']}"
    book_end_datetime_str = f"{request.form['booking_until']} {request.form['booking_until_time']}"
    event.booking_from = book_start_datetime_str
    event.booking_until = book_end_datetime_str
    event.start_date = start_datetime_str
    event.end_date = end_datetime_str
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
    ## Handle Tickets
    for t_data in ticket_collector.values():
        ticket = Ticket()
        ticket.id = str(uuid.uuid1())
        ticket.name = t_data['name']
        ticket.price = float(t_data['price'])
        ticket.description = t_data['description']
        ticket.maximum_tickets = t_data['maximum_tickets']
        event.tickets.append(ticket)

    event.save()
    return True
#.
#   . Ajax Helper for Participation Table
def change_confirmation(what):
    """
    Helper
    """
    event_id = request.form['event_id']
    user_id = request.form['user_id']
    event = Event.objects.get(id=event_id)
    return event.change_user_status(user_id, what)

@EVENTS.route('/confirm_toggle', methods=['GET', 'POST'])
def endpoint_userconfirm():
    """
    Confirm given User id for given events
    """
    if not current_user.has_right('guide'):
        abort(403)
    status = request.form['status']
    if status == "True":
        return change_confirmation('confirmed')
    return change_confirmation('unconfirmed')

@EVENTS.route('/waitinglist_toggle', methods=['GET', 'POST'])
def endpoint_waitinglist():
    """
    Confirm given User id for given events
    """
    if not current_user.has_right('guide'):
        abort(403)
    status = request.form['status']
    if status == "True":
        return change_confirmation('waitinglist_on')
    return change_confirmation('waitinglist_off')
#.
#   . Event My Booking Page
@EVENTS.route('/user/booking', methods=['GET', 'POST'])
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
    context = {}
    search_form = EventSearchForm(request.form)
    now = datetime.now()
    filters = {}
    filter_names = []
    filter_expr = {}
    search = False

    if search_form.validate_on_submit():
        filters = request.form
        search = True

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
        filter_names.append(f"Kategorie ist: {dict(categories)[filter_category]}")


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

    if filter_names:
        context['header'] = f"Filter: {', '.join(filter_names)}"
    else:
        context['header'] = "Events"
    context['event_categories'] = dict(categories)
    context['search_form'] = search_form


    return render_template('event_list.html', **context)
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

    if request.form:
        form = EventForm(request.form)
    else:
        event = event_populate(event)

        form = EventForm(obj=event)
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
@EVENTS.route('/event/participants', methods=['GET', 'POST'])
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

    context['event'] = event
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

    # Add Custom Fields to Registration Form
    custom_fields = event.custom_fields
    for idx, field in enumerate(custom_fields):
        setattr(EventRegisterForm, f"custom_{idx}",
                    StringField(field.field_name, validators=[InputRequired()]))


    ticket_stats = event.get_ticket_stats()

    # Handle Ticket function
    event_tickets = event.tickets
    for ticket in event_tickets:
        choices = [ (str(x), f'{x} Plätze') for x in range(ticket.maximum_tickets+1) ]
        places = ticket_stats['max'][ticket.id] - ticket_stats.get(ticket.id, 0)
        setattr(EventRegisterForm, f"ticket_{ticket.id}",
                    SelectField(f"{ticket.name} (je Platz: {ticket.price}  €) aktuell noch {places}/{ticket_stats['max'][ticket.id]}", choices=choices))


    register_form = EventRegisterForm(request.form)

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
      ("Kategorie", dict(categories)[event.event_category], 'string'),
      ("Schwierigkeit", event.difficulty, 'string'),
      ("Plätze insgesammt", numbers['total_places'], 'string'),
      ("Plätze bestätigt", numbers['confirmed'], 'string'),
      ("Plätze unbestätigt", numbers['wait_for_confirm'], 'string'),
      ("Auf Warteliste", numbers['waitlist'], 'string'),
      ("Buchbar ab" , event.booking_from.strftime("%d.%m.%Y %H:%M "), 'string'),
      ("Buchbar bis" , event.booking_until.strftime("%d.%m.%Y %H:%M "), 'string'),
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

    now = datetime.now()
    context = {
        'event' : event,
        'registraion_enabled': event.booking_until >= now >= event.booking_from,
        'event_custom_fields': [(str(x), y) for x, y in enumerate(event.custom_fields)],
        'event_ticket_ids': [x.id for x in event.tickets],
        'event_id': event_id,
        'event_details' : [(x, y[0], y[1]) for x, y in event_details.items()],
        'LoginForm': login_form,
        'regform': register_form,
    }

    if register_form.errors:
        print(register_form.errors, flush=True)
    if current_user.is_authenticated and register_form.validate_on_submit():
        data = request.form

        register_possible = True

        # Count
        num_participants = ticket_stats['total']
        if num_participants > event.places and not event.waitlist:
            flash("Das Event ist bereits voll", 'danger')
            register_possible = False

        if event.start_date < now:
            flash("Das Event hat bereits stattgefunden", 'danger')
            register_possible = False

        if not event.booking_until >= now >= event.booking_from:
            flash("Die Anmeldung auf das Event ist noch nicht freigeschaltet", 'danger')
            register_possible = False


        free_seats = {}
        ticket_data = {}
        wanted_seats = {}
        ticket_stats = event.get_ticket_stats() # Update data
        for ticket in event_tickets:
            wanted = int(data[f'ticket_{ticket.id}'])
            if wanted == 0:
                continue
            places = ticket_stats['max'][ticket.id] - ticket_stats.get(ticket.id, 0)
            free_seats[ticket.id] = places - wanted
            wanted_seats[ticket.id] = wanted
            ticket_data[ticket.id] = {'name': ticket.name, 'desc': ticket.description}


        if register_possible:
            current_user.add_event(event)

            new_participation = EventParticipation()
            for idx, custom_field_def in enumerate(custom_fields):
                field_name = custom_field_def.field_name
                field_id = f"custom_{idx}"
                custom_field = CustomField()
                custom_field.name = field_name
                custom_field.value = data[field_id]
                new_participation.custom_fields.append(custom_field)
                new_participation.booking_date = now

            for ticket_id, num in wanted_seats.items():
                waitinglist = True
                for _ in range(num):
                    if free_seats[ticket_id] >= 0:
                        waitinglist = False
                    free_seats[ticket_id] -= 1

                    ticket = OwnedTicket()
                    ticket.ticket_id = ticket_id
                    ticket.tickets_name = ticket_data[ticket_id]['name']
                    ticket.tickets_comment = ticket_data[ticket_id]['desc']
                    ticket.confirmed = False
                    ticket.name_on_ticket = f"{current_user.first_name} {current_user.last_name}"
                    ticket.waitinglist = waitinglist
                    new_participation.tickets.append(ticket)


            new_participation.comment = data['comment']
            new_participation.user = current_user
            new_participation.waitinglist = waitinglist
            event.reload()
            event.participations.append(new_participation)
            event.save()
            return redirect(url_for('EVENTS.page_mybooking', event_id=str(event.id)))

    if not current_user.is_authenticated:
        if login_form.validate_on_submit():
            do_login(login_form, context)

    if register_form.errors:
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

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

    if event_id and not request.form:
        # Make it possible to clone a event
        # This populates the form which can be saved as new
        event = Event.objects.get(id=event_id)
        event = event_populate(event)

        form = EventForm(obj=event)
        form = populate_event_form(form, event)
    else:
        form = EventForm(request.form)

    if form.validate_on_submit():
        new_event = Event()
        save_event_form(new_event)
        flash("Event wurde erzeug", 'info')
        return redirect(url_for('EVENTS.page_details', event_id=str(new_event.id)))

    if form.errors:
        flash(f"Bitte behebe die angezeigten Fehler in den Feldern({form.errors})", 'danger')

    context = {
        'form': form
    }

    return render_template('event_form.html', **context)
#.
