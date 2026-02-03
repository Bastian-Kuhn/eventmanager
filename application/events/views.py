"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member, too-many-locals, too-many-branches, import-error, too-few-public-methods, too-many-statements

from datetime import datetime, timedelta
from dateutil import relativedelta
import uuid
import io, csv
from flask import request, render_template, \
     flash, redirect, Blueprint, url_for, abort, make_response, jsonify
from flask_login import current_user, login_required
from wtforms import StringField, SelectField
from wtforms.validators import InputRequired
from markupsafe import Markup


from application.events.models import Event, EventParticipation,\
                             CustomField, CustomFieldDefintion, Ticket, OwnedTicket, difficulties
from application.models.user import roles
from application.auth.forms import LoginForm
from application.auth.views import do_login
from application.events.forms import EventForm, EventRegisterForm, EventSearchForm
from application.models.config import Config
from application.models.user import User
from application.events.models import OwnedTicket

from application.modules.email import send_email

roles_dict = dict(roles)


def age(date):
    now = datetime.now().date()
    return relativedelta.relativedelta(now, date).years

EVENTS = Blueprint('EVENTS', __name__)

#   . Helpers


def get_categories():
    """
    Return Dict with Categories
    """
    categrories_detailed = {}
    try:
        for cat in Config.objects.get(enabled=True).event_categories_full:
            categrories_detailed[cat.name.lower()] = {
                                                        'color': cat.color,
                                                        'name': cat.name,
                                                    }
    except:
        pass
    return categrories_detailed

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
        is_extra = True if t_data.get('is_extra_ticket') == 'y' else False
        ticket = Ticket()
        ticket.name = t_data['name']
        ticket.price = float(t_data.get('price', 0))
        ticket.description = t_data.get('description')
        ticket.is_extra_ticket = is_extra
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
    new_email = request.form['new_email']
    new_phone = request.form['new_phone']
    new_birthdate = request.form['new_birthdate']
    new_comment = request.form['new_comment']
    ticket_id = request.form['ticket_id']
    target_user_id = request.form.get('target_user_id')

    event = Event.objects.get(id=event_id)
    
    # Determine which user's tickets we should be editing
    if current_user.has_right('guide') and target_user_id:
        target_user = User.objects.get(id=target_user_id)
        ticket = event.get_booked_ticket(ticket_id, target_user)
    else:
        # Regular user can only edit their own tickets
        ticket = event.get_booked_ticket(ticket_id, current_user)
    
    if ticket:
        ticket.name_on_ticket = new_name
        ticket.phone_on_ticket = new_phone
        ticket.email_on_ticket = new_email
        ticket.birthdate_on_ticket = new_birthdate
        ticket.comment_on_ticket = new_comment
        event.save()
        return {'msg': 'success'}
    else:
        return {'msg': 'error', 'error': 'Ticket nicht gefunden oder keine Berechtigung'}, 403

@EVENTS.route('/user/add_ticket', methods=['POST'])
def ajax_add_ticket():
    """
    Add new ticket for a user (Guide only)
    """
    if not current_user.has_right('guide'):
        return {'success': False, 'message': 'Keine Berechtigung'}, 403
        
    event_id = request.form['event_id']
    user_id = request.form['user_id']
    ticket_type = request.form['ticket_type']
    new_name = request.form['new_name']
    new_email = request.form['new_email']
    new_phone = request.form['new_phone']
    new_birthdate = request.form['new_birthdate']
    new_comment = request.form['new_comment']
    
    
    try:
        event = Event.objects.get(id=event_id)
        target_user = User.objects.get(id=user_id)
        
        # Check if ticket type exists
        ticket_definition = None
        for ticket_def in event.tickets:
            if ticket_def.name == ticket_type:
                ticket_definition = ticket_def
                break
                
        if not ticket_definition:
            return {'success': False, 'message': 'Ticket-Typ nicht gefunden'}, 400
        
        # Find or create participation for the target user
        participation = None
        for parti in event.participations:
            if parti.user == target_user:
                participation = parti
                break
                
        if not participation:
            # Create new participation
            participation = EventParticipation()
            participation.user = target_user
            participation.booking_date = datetime.now()
            participation.custom_fields = []
            participation.tickets = []
            event.participations.append(participation)
        
        # Create new ticket
        new_ticket = OwnedTicket()
        new_ticket.ticket_id = str(uuid.uuid4())
        new_ticket.ticket_name = ticket_type
        new_ticket.name_on_ticket = new_name if new_name else f"{target_user.first_name} {target_user.last_name}"
        new_ticket.email_on_ticket = new_email if new_email else target_user.email
        new_ticket.phone_on_ticket = new_phone if new_phone else target_user.phone
        new_ticket.birthdate_on_ticket = datetime.strptime(new_birthdate, '%Y-%m-%d').date() if new_birthdate else target_user.birthdate
        new_ticket.comment_on_ticket = new_comment
        new_ticket.is_extra_ticket = ticket_definition.is_extra_ticket
        new_ticket.confirmed = False  # Guide needs to confirm
        new_ticket.waitinglist = False
        new_ticket.is_paid = False
        
        participation.tickets.append(new_ticket)
        event.save()
        
        return {'success': True, 'message': 'Ticket erfolgreich hinzugefügt'}
        
    except Exception as e:
        return {'success': False, 'message': str(e)}, 500

@EVENTS.route('/user/get_data/<event_id>', methods=['GET'])
def ajax_ticketdata(event_id):
    """
    Get user data for autocomplete suggestions
    """
    
    query = request.args.get('q', '').lower()
    target_user_id = request.args.get('target_user_id')
    
    event = Event.objects.get(id=event_id)
    
    # Determine which user's tickets to search through
    if current_user.has_right('guide') and target_user_id:
        target_user = User.objects.get(id=target_user_id)
        tickets = event.get_tickets_of_user(target_user)
    else:
        tickets = event.get_tickets_of_user(current_user)
        
    merged_data = {
        'name': query,
        'email': '',
        'phone': '',
        'birthdate': ''
    }
    
    for ticket in tickets:
        if query in ticket.name_on_ticket.lower():
            merged_data['name'] = ticket.name_on_ticket
            if not merged_data['email'] and ticket.email_on_ticket:
                merged_data['email'] = ticket.email_on_ticket
            if not merged_data['phone'] and ticket.phone_on_ticket:
                merged_data['phone'] = ticket.phone_on_ticket
            if not merged_data['birthdate'] and ticket.birthdate_on_ticket:
                merged_data['birthdate'] = ticket.birthdate_on_ticket.strftime('%Y-%m-%d')

    return jsonify([merged_data])

@EVENTS.route('/user/booking')
def page_mybooking():
    """
    Detail Page for Event
    """
    event_id = request.args.get('event_id')
    user_id = request.args.get('user_id')  # Optional parameter for guides to view other users' bookings
    event = Event.objects.get(id=event_id)
    tickets_by_name = {}
    total_cost = 0
    open_cost = 0  # Track unpaid costs
    # Create a mapping of event tickets to get price information
    event_tickets_by_name = {}
    for event_ticket in event.tickets:
        event_tickets_by_name[event_ticket.name] = event_ticket
    
    # Determine which user's booking to show
    target_user = current_user
    if user_id and current_user.has_right('guide'):
        target_user = User.objects.get(id=user_id)
    
    for parti in event.participations:
        if parti.user == target_user:
            for ticket in parti.tickets:
                ticket.booking_date = parti.booking_date
                # Add price information to the ticket
                if ticket.custom_price:
                    # Use custom price set by guide
                    ticket.price = ticket.custom_price
                elif ticket.ticket_name in event_tickets_by_name:
                    ticket.price = event_tickets_by_name[ticket.ticket_name].price
                else:
                    ticket.price = 0
                
                # Always add to total cost
                total_cost += ticket.price
                
                # Add to open costs if not paid
                if not ticket.is_paid:
                    open_cost += ticket.price
                    
                tickets_by_name.setdefault(ticket.ticket_name, [])
                tickets_by_name[ticket.ticket_name].append(ticket)

    context = {
        'event': event,
        'tickets_by_name': tickets_by_name,
        'total_cost': total_cost,
        'open_cost': open_cost,
        'target_user': target_user,
        'is_viewing_other_user': target_user != current_user,
    }


    return render_template('user_booking.html', **context)

#.
#   . Event List Page
@EVENTS.route('/', methods=['POST', 'GET'])
def page_list():
    """
    Public Page with Events
    """
    categrories_detailed = get_categories()

    categories = [(None, "Alle")] + [(x, y['name']) \
            for x,y in categrories_detailed.items()]
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
        filter_names.append(f"Kategorie: {dict(categories).get(filter_category, filter_category)}")


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
        #filter_names.append("Zeitpunkt: Zukünftige")

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
        context['header'] = f"{', '.join(filter_names)}"
    else:
        context['header'] = "Events"
    context['event_categories'] = dict(categories)
    context['event_categories_detailed'] = categrories_detailed
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

    try:
        extra_categories = [(x.name.lower(), x.name) for x in Config.objects(enabled=True)[0].event_categories_full]
    except:
        extra_categories = []
    categories = [(None, "Keine")] + extra_categories

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
    elif job == 'update_guide_comment':
        guide_comment = request.form.get('guide_comment', '')
        ticket.guide_comment = guide_comment
        response = {'success': True, 'ticket_id': ticket_id, 'guide_comment': guide_comment}
    elif job == 'update_guide':
        guide = request.form.get('guide', '')
        ticket.guide = guide
        response = {'success': True, 'ticket_id': ticket_id, 'guide': guide}
    elif job == 'delete':
        response = event.delete_ticket(ticket_id)

    event.save()

    return response


@EVENTS.route('/event/manage_hidden_categories', methods=['POST'])
def manage_hidden_categories():
    """
    Manage global hidden categories
    """
    if not current_user.has_right('guide'):
        return jsonify({'success': False, 'error': 'Not authorized'})

    action = request.form.get('action')
    event_id = request.form.get('event_id')
    category = request.form.get('category')

    try:
        event = Event.objects.get(id=event_id)
        
        # Initialize hidden_categories if it doesn't exist
        if not event.hidden_categories:
            event.hidden_categories = []
        
        if action == 'hide_category':
            if category and category not in event.hidden_categories:
                event.hidden_categories.append(category)
        elif action == 'show_category':
            if category and category in event.hidden_categories:
                event.hidden_categories.remove(category)
        elif action == 'hide_all_categories':
            # Get all available categories from extra tickets
            from collections import defaultdict
            extra_tickets = defaultdict(list)
            for parti in event.participations:
                for ticket in parti.tickets:
                    if ticket.is_extra_ticket:
                        extra_tickets[ticket.ticket_name].append(ticket)
            event.hidden_categories = list(extra_tickets.keys())
        elif action == 'show_all_categories':
            event.hidden_categories = []
        
        event.save()
        
        return jsonify({'success': True, 'hidden_categories': event.hidden_categories})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@EVENTS.route('/event/get_hidden_categories', methods=['GET'])
def get_hidden_categories():
    """
    Get global hidden categories
    """
    if not current_user.is_authenticated:
        return jsonify({'success': False, 'error': 'Not authenticated'})

    event_id = request.args.get('event_id')
    
    try:
        event = Event.objects.get(id=event_id)
        
        # Get global hidden categories
        hidden_categories = event.hidden_categories if event.hidden_categories else []
        
        return jsonify({'success': True, 'hidden_categories': hidden_categories})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


def get_participants(event):
    """
    Get List of Participants
    """
    bookings = {
        'confirmed' : [],
        'waitinglist' : [],
        'unconfirmed' : [],
    }
    tickets_by_name = {}
    for event_ticket in event.tickets:
        tickets_by_name[event_ticket.name] = event_ticket
    for parti in event.participations:
        for ticket in parti.tickets:
            what = 'unconfirmed'
            if ticket.confirmed:
                what = 'confirmed'
            elif ticket.waitinglist:
                what = 'waitinglist'

            if not ticket.is_paid and tickets_by_name[ticket.ticket_name].price == 0:
                ticket.is_paid = True

            extra_questions = []
            for field in event.custom_fields:
                extra_questions.append((field.field_name, parti.get_field(field.field_name)))


            bucher = f"{parti.user.first_name} {parti.user.last_name}"
            if ticket.name_on_ticket != bucher:
                role = 'Unbekannt'
            elif parti.user.role:
                role = roles_dict[parti.user.role]
            else:
                role = "Nicht überprüft"

            ticket_by_name_price = 0
            if ticket.ticket_name in tickets_by_name:
                ticket_by_name_price = tickets_by_name[ticket.ticket_name].price

            bookings[what].append({
                'id': ticket.ticket_id,
                'ticket_owner': ticket.name_on_ticket,
                'is_extra_ticket': ticket.is_extra_ticket,
                'booking_date': parti.booking_date,
                'is_paid': ticket.is_paid,
                'price': ticket.custom_price if (hasattr(ticket, 'custom_price') and ticket.custom_price is not None) else ticket_by_name_price,
                'ticket_info': {
                    'name': ticket.ticket_name,
                    'bucher': bucher,
                    'bucher_user_id': str(parti.user.id),
                    'telefon': ticket.phone_on_ticket if ticket.phone_on_ticket else parti.user.phone,
                    'email': ticket.email_on_ticket if ticket.email_on_ticket else parti.user.email,
                    'media_optin': parti.user.media_optin,
                    'data_optin': parti.user.data_optin,
                    'birthdate': ticket.birthdate_on_ticket if ticket.birthdate_on_ticket else parti.user.birthdate,
                    'role': role,
                    'comment': ticket.comment_on_ticket if ticket.comment_on_ticket else "",
                    'guide_comment': ticket.guide_comment if ticket.guide_comment else "",
                    'guide': ticket.guide if ticket.guide else "",
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
      'Alter',
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
            age(line['ticket_info']['birthdate']),
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
        if email and email not in emails:
            emails.append(email)

    context['event'] = event
    context['emails'] = emails
    context['bookings'] = participants
    extra_tickets_grouped = {}
    
    # Collect all extra tickets from all states
    for state in ['confirmed', 'unconfirmed', 'waitinglist']:
        for ticket in participants[state]:
            if not ticket['is_extra_ticket']:
                continue
            ticket_info = ticket['ticket_info']
            extra_tickets_grouped.setdefault(ticket_info['name'], [])
            extra_tickets_grouped[ticket_info['name']].append((
                ticket['id'], ticket['ticket_owner'], ticket_info['comment'], 
                ticket_info['guide_comment'], ticket_info['birthdate'], 
                ticket['is_paid'], ticket_info['bucher'], ticket_info['bucher_user_id']
            ))
    
    context['extra_tickets'] = extra_tickets_grouped

    return render_template('event_participants.html', **context)
#.
#   . Billing

@EVENTS.route('/event/change_paidstatus', methods=['POST'])
def change_paidstatus():
    """
    Helper to mark Tickets paid
    """
    if not current_user.has_right('guide'):
        abort(403)

    job = request.form['job']
    ticket_id = request.form['ticket_id']
    event_id = request.form['event_id']

    event = Event.objects.get(id=event_id)
    ticket = event.get_booked_ticket(ticket_id)

    response = {}

    if job == 'paid':
        ticket.is_paid = True
    elif job == 'unpaid':
        ticket.is_paid = False
    event.save()

    return response

@EVENTS.route('/event/change_price', methods=['POST'])
@login_required
def change_price():
    """
    Helper to change ticket prices for guides
    """
    if not current_user.has_right('guide'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    event_id = request.form.get('event_id')
    ticket_id = request.form.get('ticket_id')
    new_price = request.form.get('new_price')
    
    try:
        new_price = float(new_price)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid price format'}), 400
    
    event = Event.objects.get(id=event_id)
    
    # Find the ticket and update its custom price
    for parti in event.participations:
        for ticket in parti.tickets:
            if str(ticket.ticket_id) == str(ticket_id):
                ticket.custom_price = new_price
                event.save()
                return jsonify({'success': True, 'new_price': new_price})
    
    return jsonify({'success': False, 'message': 'Ticket not found'}), 404

@EVENTS.route('/event/extra_tickets/<category>')
def page_extra_tickets(category):
    """
    Extra Tickets Category Page for Guides
    """
    event_id = request.args.get('event_id')

    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))

    if not current_user.has_right('guide'):
        abort(403)

    event = Event.objects.get(id=event_id)
    context = {}

    participants = get_participants(event)
    
    # Filter for the specific category from all states
    category_tickets = []
    for state in ['confirmed', 'unconfirmed', 'waitinglist']:
        for ticket in participants[state]:
            if not ticket['is_extra_ticket']:
                continue
            ticket_info = ticket['ticket_info']
            if ticket_info['name'] == category:
                category_tickets.append((
                    ticket['id'], ticket['ticket_owner'], ticket_info['comment'], 
                    ticket_info['guide_comment'], ticket_info['guide'], ticket_info['birthdate'], 
                    ticket['is_paid'], ticket_info['bucher'], ticket_info['bucher_user_id']
                ))

    context['event'] = event
    context['category'] = category
    context['tickets'] = category_tickets

    return render_template('event_extra_tickets.html', **context)

@EVENTS.route('/event/extra_tickets_export/<category>')
def page_extra_tickets_export(category):
    """
    Export Extra Tickets for specific category
    """
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))

    if not current_user.has_right('guide'):
        abort(403)

    event_id = request.args.get('event_id')
    event = Event.objects.get(id=event_id)
    
    participants = get_participants(event)
    
    # Filter for the specific category from all states
    category_tickets = []
    for state in ['confirmed', 'unconfirmed', 'waitinglist']:
        for ticket in participants[state]:
            if not ticket['is_extra_ticket']:
                continue
            ticket_info = ticket['ticket_info']
            if ticket_info['name'] == category:
                category_tickets.append({
                    'id': ticket['id'], 
                    'ticket_owner': ticket['ticket_owner'], 
                    'comment': ticket_info['comment'], 
                    'guide': ticket_info['guide'], 
                    'guide_comment': ticket_info['guide_comment'], 
                    'birthdate': ticket_info['birthdate'], 
                    'is_paid': ticket['is_paid'], 
                    'bucher': ticket_info['bucher'], 
                    'bucher_user_id': ticket_info['bucher_user_id'],
                    'booking_date': ticket['booking_date']
                })
    
    io_output = io.StringIO()
    writer = csv.writer(io_output, delimiter=';')

    writer.writerow([
        'Teilnehmer',
        'Teilnehmer Kommentar',
        'Guide Kommentar',
        'Guide',
        'Jahrgang',
        'Bezahlt'
    ])
    
    # Sort tickets by guide if guide is set, otherwise keep original order
    sorted_tickets = sorted(category_tickets, key=lambda x: x['guide'] if x['guide'] else ticket['ticket_owner'])
    
    for ticket in sorted_tickets:
        writer.writerow([
            ticket['ticket_owner'],
            ticket['comment'],
            ticket['guide_comment'],
            ticket['guide'],
            ticket['birthdate'].strftime('%m/%Y') if ticket['birthdate'] else '',
            'Ja' if ticket['is_paid'] else 'Nein'
        ])

    output = make_response(io_output.getvalue())
    filename = f"{event.event_name}_{category}".replace(' ', '_')
    output.headers["Content-Disposition"] = f"attachment; filename={filename}.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@EVENTS.route('/event/billing')
def page_billing():
    """
    Participants Page
    """

    event_id = request.args.get('event_id')

    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))

    if not current_user.has_right('guide'):
        abort(403)

    event = Event.objects.get(id=event_id)
    context = {}


    participants = get_participants(event)

    tickets = {
    }

    for ticket in participants['confirmed']:
        bucher = ticket['ticket_info']['bucher']
        tickets.setdefault(bucher, {'paid': [], 'unpaid': []})
        if ticket['is_paid']:
            tickets[bucher]['paid'].append(ticket)
        else:
            tickets[bucher]['unpaid'].append(ticket)

    for ticket in participants['unconfirmed']:
        bucher = ticket['ticket_info']['bucher']
        tickets.setdefault(bucher, {'paid': [], 'unpaid': []})
        if not ticket['is_extra_ticket']:
            continue
        if ticket['is_paid']:
            tickets[bucher]['paid'].append(ticket)
        else:
            tickets[bucher]['unpaid'].append(ticket)

    for bucher in tickets.copy():
        total_pay = 0
        for ticket in tickets[bucher]['unpaid']:
            total_pay += ticket['price']
        if total_pay == 0:
            del tickets[bucher]
        else:
            tickets[bucher]['total'] = total_pay



    context['event'] = event
    context['bookings'] = tickets


    return render_template('event_billing.html', **context)

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

    try:
        extra_categories = [(x.name.lower(), x.name) for x in Config.objects(enabled=True)[0].event_categories_full]
    except:
        extra_categories = []
    categories = [(None, "Alle")] + extra_categories
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
        choices = [ (str(x), f'{x} mal') for x in range(ticket.maximum_tickets+1) ]
        places = ticket_stats['max'][ticket.name] - ticket_stats.get(ticket.name, 0)
        preisinfo = ""
        if ticket.price > 0:
            preisinfo = f"(je Anmeldung: {ticket.price}  €)"
        description = f": {ticket.description}" if ticket.description else ""
        setattr(EventRegForm, f"ticket_{ticket.name}",
            SelectField(Markup(f"<b>{ticket.name}</b> {description} {preisinfo}"), choices=choices))


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
      ("Start",  f'{event.start_date.strftime("%d.%m.%Y")} {event.start_date.strftime("%H:%M")}', 'string'),
      ("Ende" , event.end_date.strftime("%d.%m.%Y %H:%M "), 'string'),
      ('Tickets', format_ticket(event.tickets), 'table'),
    ]

    if numbers['total_places']:
        detail_fields += [
            ("Freie Plätze insgesammt", numbers['total_places'], 'string'),
        ]


    if event.min_places:
        detail_fields += [
            ('Mindestteilnehmerzahl', event.min_places, 'string'),
        ]

    detail_fields += [
      ("Anmeldungen bestätigt", numbers['confirmed'], 'string'),
      ("Anmeldungen unbestätigt", numbers['wait_for_confirm'], 'string'),
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

    if event.event_type != 'notour':
        detail_fields += [
          ("Schwierigkeit", difficult_to_icon(event.difficulty, 1), 'string'),
          ("Kondition", difficult_to_icon(event.shape, 2), 'string'),
          ('Länge in km', event.length_km, 'string'),
          ('Höhenmeter', event.altitude_difference, 'string'),
          ('Dauer in Stunden', event.length_h, 'string'),
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
        elif higest_level != 'guide' and guide.role == 'youthguide':
            higest_level = 'youthguide'
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
            is_extra = ticket.is_extra_ticket
            ticket_data[ticket.name] = {'name': ticket.name, 'desc': ticket.description, 'is_extra': is_extra}

        if not wanted_seats:
            flash("Du hast keine Tickets gewählt.", 'danger')
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
                    ticket.email_on_ticket = current_user.email
                    ticket.phone_on_ticket = current_user.phone
                    ticket.birthdate_on_ticket = current_user.birthdate
                    ticket.comment_on_ticket = data['comment']
                    ticket.is_extra_ticket = ticket_data[ticket_name]['is_extra']
                    ticket.waitinglist = waitinglist
                    new_participation.tickets.append(ticket)


            new_participation.comment = data['comment']
            new_participation.user = current_user
            new_participation.waitinglist = waitinglist
            Event.objects(id=event_id).update_one(push__participations=new_participation)
            event.reload()
            event.save()

            for guide in event.event_owners:
                send_email(guide.email, f"Neue Anmeldung: {event.event_name}", 'email/newparticipant',
                       event=event, user=current_user, tickets=new_participation.tickets, fields=new_participation.custom_fields, form_data=data)

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
    try:
        extra_categories = [(x.name.lower(), x.name) for x in Config.objects(enabled=True)[0].event_categories_full]
    except:
        extra_categories = []
    categories = [(None, "Keine")] + extra_categories

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
