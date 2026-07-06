"""
Frontend User
"""
# pylint: disable=no-member
from datetime import datetime
from application import limiter
from application.models.user import User

from flask import request, render_template, current_app, \
     flash, redirect, session, Blueprint, url_for
from flask_login import current_user, login_required
from .forms import NewUserForm, ConsentForm, ChangePasswordForm


USER = Blueprint('USER', __name__)


@USER.route('/user/profile')
@login_required
def page_user_profil():
    """
    User profile: Passwort aendern, vergangene Touren, Medien-Einwilligung
    (global + pro Event ueberschreibbar).
    """
    now = datetime.now()

    consent_form = ConsentForm(
        media_optin=current_user.media_optin,
        data_optin=current_user.data_optin,
    )
    password_form = ChangePasswordForm()

    is_guide = current_user.has_right('guide')
    past_events = []
    for event in current_user.event_registrations:
        if not event.is_over(now):
            continue
        participation = event.get_participation(current_user)
        past_events.append({
            'event': event,
            # None = globalen Profilwert nutzen, sonst True/False als Ueberschreibung
            'override': participation.media_optin if participation else None,
            # Nach Tourbeginn darf nur noch ein Guide die Einwilligung aendern
            'editable': is_guide or not event.is_started(now),
        })
    past_events.sort(key=lambda item: item['event'].start_date or datetime.min, reverse=True)

    # Eigene Hüttenbuchungen und -anfragen
    from application.huts.models import HutBooking
    my_hut_bookings = list(
        HutBooking.objects(user=current_user).order_by('-from_date'))

    context = {
        'consent_form': consent_form,
        'password_form': password_form,
        'past_events': past_events,
        'hut_bookings': my_hut_bookings,
    }
    return render_template('user_profile.html', **context)


@USER.route('/user/profile/consent', methods=['POST'])
@login_required
def page_user_consent():
    """
    Globale Einwilligungen speichern
    """
    form = ConsentForm()
    if form.validate_on_submit():
        # Zeitpunkt der Einwilligung nur bei Zustimmung als Nachweis festhalten
        if form.media_optin.data and not current_user.media_optin:
            current_user.media_optin_date = datetime.now()
        current_user.media_optin = form.media_optin.data
        current_user.data_optin = form.data_optin.data
        current_user.save()
        flash("Einwilligungen gespeichert", 'success')
    else:
        flash("Einwilligungen konnten nicht gespeichert werden", 'danger')
    return redirect(url_for('USER.page_user_profil'))


@USER.route('/user/profile/password', methods=['POST'])
@login_required
def page_user_password():
    """
    Passwort aendern (aktuelles Passwort erforderlich)
    """
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.old_password.data):
            flash("Aktuelles Passwort ist falsch", 'danger')
            return redirect(url_for('USER.page_user_profil'))
        current_user.set_password(form.password.data)
        current_user.force_password_change = False
        current_user.save()
        flash("Passwort geändert", 'success')
    else:
        for _, messages in form.errors.items():
            flash(messages[0], 'danger')
    return redirect(url_for('USER.page_user_profil'))


@USER.route('/user/profile/event_optin', methods=['POST'])
@login_required
def page_user_event_optin():
    """
    Medien-Einwilligung fuer ein einzelnes Event ueberschreiben
    """
    from application.events.models import Event

    event_id = request.form.get('event_id')
    choice = request.form.get('media_optin')  # 'default' | 'yes' | 'no'
    value = {'yes': True, 'no': False}.get(choice)  # 'default' -> None

    event = Event.objects(id=event_id).first()
    if not event:
        flash("Event nicht gefunden", 'danger')
        return redirect(url_for('USER.page_user_profil'))

    # Nach Tourbeginn darf nur noch ein Guide die Einwilligung aendern
    if event.is_started(datetime.now()) and not current_user.has_right('guide'):
        flash("Nach Tourbeginn kann die Einwilligung nur noch von einem Guide geändert werden",
              'danger')
        return redirect(url_for('USER.page_user_profil'))

    participation = event.get_participation(current_user)
    if not participation:
        flash("Für diese Tour liegt keine Anmeldung vor", 'danger')
        return redirect(url_for('USER.page_user_profil'))

    participation.media_optin = value
    event.save()
    flash("Einwilligung für diese Tour gespeichert", 'success')
    return redirect(url_for('USER.page_user_profil'))

@USER.route('/user/create', methods=['POST', 'GET'])
@limiter.limit("4/min 1/sec")
def page_user_create():
    """
    Create New User
    """

    form = NewUserForm(request.form)
    if form.validate_on_submit():
        new_user = User()
        for field, value in dict(request.form).items():
            if field in ['password', 'password_repeat']:
                continue
            if field in ['media_optin', 'data_optin']:
                value = bool(value)
            if field == 'email':
                value = value.lower()
            setattr(new_user, field, value)
        if new_user.media_optin:
            new_user.media_optin_date = datetime.now()
        new_user.set_password(request.form['password'])
        new_user.save()
        flash("Anmeldung erfolgreich", 'info')
        return redirect(url_for('auth.login'))

    if form.errors:
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

    context = {
        'form': form
    }

    return render_template('user_create.html', **context)
