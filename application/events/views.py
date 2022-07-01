"""
Login Routes and Handling for Frontend
"""
# pylint: disable=no-member
from application.models.event import Event
from flask import request, render_template, current_app, \
     flash, redirect, session, Blueprint, url_for
from flask_login import current_user, login_required
from .forms import EventForm


EVENTS = Blueprint('EVENTS', __name__)


@EVENTS.route('/')
def page_events():
    """
    Public Page with Events
    """

    context = {}

    return render_template('events_list.html', **context)

@EVENTS.route('/create', methods=['GET', 'POST'])
@login_required
def page_create():
    """
    Create New Event Page
    """

    form = EventForm(request.form)
    if form.validate_on_submit():
        pass
    context = {
        'form': form
    }

    return render_template('event_create.html', **context)
