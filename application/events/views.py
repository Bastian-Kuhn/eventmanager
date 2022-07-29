"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member

from datetime import datetime, timedelta
from flask import request, render_template, \
     flash, redirect, Blueprint, url_for, abort
from flask_login import current_user, login_required


from application.models.event import Event, EventParticipation, categories
from application.auth.forms import LoginForm
from application.auth.views import do_login
from application.events.forms import EventForm, EventRegisterForm, EventSearchForm



EVENTS = Blueprint('EVENTS', __name__)

def save_event_form(event):
    """
    Helper to store Event Object
    """
    if current_user not in event.event_owners:
        event.event_owners.append(current_user)
    for field, value in dict(request.form).items():
        if field in ['start_date', 'end_date']:
            continue
        if field in ['waitlist',]:
            value = bool(value)
        setattr(event, field, value)
    start_datetime_str = f"{request.form['start_date']} {request.form['start_time']}"
    end_datetime_str = f"{request.form['end_date']} {request.form['end_time']}"
    event.start_date = start_datetime_str
    event.end_date = end_datetime_str
    event.save()
    return True


def change_confirmation(what):
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

    print(filters)
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
        filter_names.append(f"Zeitpunkt: Zukünftige")

    events = Event.objects(**filter_expr).order_by('start_date')
    result = []
    if filter_own := filters.get('filter_own'):
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
        # We store DateTime Fields,
        # but the Form useses two seperate ones:
        event.start_time = event.start_date
        event.end_time = event.end_date

        form = EventForm(obj=event)
        form.populate_obj(event)
    if form.validate_on_submit():
        save_event_form(event)
        flash("Event wurde aktualisiert", 'info')
        return redirect(url_for('EVENTS.page_details', event_id=event_id))

    if form.errors:
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

    context = {
        'form': form
    }

    context['event'] = event
    return render_template('event_form.html', **context)

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

@EVENTS.route('/event/details', methods=['GET', 'POST'])
def page_details():
    """
    Detail Page for Event
    """
    event_id = request.args.get('event_id')

    event = Event.objects.get(id=event_id)
    login_form = LoginForm(request.form)
    register_form = EventRegisterForm(request.form)


    numbers = event.get_numbers()

    detail_fields = [
      ("Kategorie", dict(categories)[event.event_category]),
      ("Schwierigkeit", event.difficulty),
      ("Plätze insgesammt", numbers['total_places']),
      ("Plätze bestätigt", numbers['confirmed']),
      ("Plätze unbestätigt", numbers['wait_for_confirm']),
      ("Auf Warteliste", numbers['waitlist']),
      ("Start",  event.start_date.strftime("%d.%m.%Y")),
      ("Zeit am Treffpunkt" , event.start_date.strftime("%H:%M")),
      ("Ende" , event.end_date.strftime("%d.%m.%Y %H:%M ")),
      ('Länge in km', event.length_km),
      ('Höhenmeter', event.altitude_difference),
      ('Dauer in Stunden', event.length_h),
    ]

    event_details = {}
    for title, data in detail_fields:
        if data:
            event_details[title] = data

    context = {
        'event' : event,
        'event_id': event_id,
        'event_details' : event_details.items(),
        'LoginForm': login_form,
        'EventRegisterForm': register_form,
    }

    if current_user.is_authenticated and register_form.validate_on_submit():
        data = request.form
        num_participants = len(event.participations)
        waitinglist = False
        register_possible = True
        if num_participants > event.places:
            if event.waitinglist:
                waitinglist = True
            else:
                flash("Das Event ist bereits voll", 'danger')
                register_possible = False

        if register_possible and not current_user.participate_event(event_id):
            current_user.add_event(event)
            new_participation = EventParticipation()
            new_participation.comment = data['comment']
            new_participation.user = current_user
            new_participation.waitinglist = waitinglist
            event.participations.append(new_participation)
            event.save()

    if not current_user.is_authenticated:
        if login_form.validate_on_submit():
            do_login(login_form, context)

    if register_form.errors:
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

    return render_template('event_details.html', **context)

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
        # We store DateTime Fields,
        # but the Form useses two seperate ones:
        event.start_time = event.start_date
        event.end_time = event.end_date
        form = EventForm(obj=event)
        form.populate_obj(event)
    else:
        form = EventForm(request.form)

    if form.validate_on_submit():
        new_event = Event()
        save_event_form(new_event)
        flash("Event wurde erzeug", 'info')
        return redirect(url_for('EVENTS.page_details', event_id=str(new_event.id)))

    if form.errors:
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

    context = {
        'form': form
    }

    return render_template('event_form.html', **context)
