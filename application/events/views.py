"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member
from flask import request, render_template, \
     flash, redirect, Blueprint, url_for, abort
from flask_login import current_user, login_required
from datetime import datetime


from application.models.event import Event, EventParticipation
from application.auth.forms import LoginForm
from application.auth.views import do_login

from .forms import EventForm, EventRegisterForm


EVENTS = Blueprint('EVENTS', __name__)


@EVENTS.route('/')
def page_list():
    """
    Public Page with Events
    """
    event_filter = request.args.get('filter')
    context = {}
    if event_filter == 'my_events':
        context['header'] = "Meine Events"
        context['events'] = current_user.event_registrations
    else:
        now = datetime.now()
        context['events'] = Event.objects(start_date__gte=now)
        context['header'] = "Alle Events"


    return render_template('event_list.html', **context)

@EVENTS.route('/event/admin', methods=['GET', 'POST'])
def page_admin():
    """
    Admin Page
    """
    if not current_user.has_right('guide'):
        abort(403)

    context = {}
    event_id = request.args.get('event_id')
    event = Event.objects.get(id=event_id)

    context['event'] = event
    return render_template('event_admin.html', **context)

@EVENTS.route('/event/details', methods=['GET', 'POST'])
def page_details():
    """
    Detail Page for Event
    """
    event_id = request.args.get('event_id')

    event = Event.objects.get(id=event_id)
    login_form = LoginForm(request.form)
    register_form = EventRegisterForm(request.form)

    context = {
        'event' : event,
        'event_id': event_id,
        'LoginForm': login_form,
        'EventRegisterForm': register_form,
    }

    if register_form.validate_on_submit():
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

    form = EventForm(request.form)
    if form.validate_on_submit():
        new_event = Event()
        for field, value in dict(request.form).items():
            if field in ['start_date', 'start_time', 'end_date']:
                continue
            if field in ['waitlist',]:
                value = bool(value)
            setattr(new_event, field, value)
        start_datetime_str = f"{request.form['start_date']} {request.form['start_time']}"
        end_datetime_str = f"{request.form['end_date']} 00:00"
        new_event.start_date = start_datetime_str
        new_event.end_date = end_datetime_str

        new_event.event_owner = current_user

        new_event.save()
        flash("Event wurde erzeug", 'info')
        return redirect(url_for('EVENTS.page_details', event_id=str(new_event.id)))

    if form.errors:
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

    context = {
        'form': form
    }

    return render_template('event_create.html', **context)
