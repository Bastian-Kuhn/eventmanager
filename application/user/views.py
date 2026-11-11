"""
Frontend User
"""
# pylint: disable=no-member
from datetime import datetime
from application import limiter
from application.models.user import User
from application.events.models import Event, with_event_lock
from application.auth.forms import RequestPasswordForm
from application.auth.views import PASSWORD_LIMIT, refresh_login, read_reset_token, mail_allowed
from application.modules.email import send_email

from flask import request, render_template, current_app, \
     flash, redirect, session, Blueprint, url_for
from flask_login import current_user, login_required
from mongoengine.errors import NotUniqueError
from mongoengine.queryset.visitor import Q
from .forms import NewUserForm, ConsentForm, ChangePasswordForm, ConfirmEmailForm


USER = Blueprint('USER', __name__)

# Gueltigkeit des Bestaetigungslinks nach der Registrierung
CONFIRM_TOKEN_SECONDS = 3 * 24 * 3600


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

    # Nur die vergangenen Touren der Sektion laden, nicht alle je gebuchten.
    # Verweise auf geloeschte Events fallen dabei von selbst heraus.
    past_events = []
    booked_ids = list(current_user._reference_ids('event_registrations'))  # pylint: disable=protected-access
    over = Q(end_date__lt=now) | Q(end_date=None, start_date__lt=now)
    for event in Event.visible(Q(id__in=booked_ids) & over):
        participation = event.get_participation(current_user)
        past_events.append({
            'event': event,
            # None = globalen Profilwert nutzen, sonst True/False als Ueberschreibung
            'override': participation.media_optin if participation else None,
            # Nach Tourbeginn darf nur noch ein Verantwortlicher der Tour die Einwilligung aendern
            'editable': current_user.may_manage_event(event) or not event.is_started(now),
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
@limiter.limit(PASSWORD_LIMIT)
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
        refresh_login(current_user._get_current_object())  # pylint: disable=protected-access
        flash("Passwort geändert", 'success')
    else:
        for _, messages in form.errors.items():
            flash(messages[0], 'danger')
    return redirect(url_for('USER.page_user_profil'))


@USER.route('/user/profile/event_optin', methods=['POST'])
@login_required
@with_event_lock
def page_user_event_optin():
    """
    Medien-Einwilligung fuer ein einzelnes Event ueberschreiben
    """
    event_id = request.form.get('event_id')
    choice = request.form.get('media_optin')  # 'default' | 'yes' | 'no'
    value = {'yes': True, 'no': False}.get(choice)  # 'default' -> None

    event = Event.visible(id=event_id).first()
    if not event:
        flash("Event nicht gefunden", 'danger')
        return redirect(url_for('USER.page_user_profil'))

    # Nach Tourbeginn darf nur noch ein Verantwortlicher der Tour die Einwilligung aendern
    if event.is_started(datetime.now()) and not current_user.may_manage_event(event):
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
@limiter.limit("4/minute;1/second", methods=['POST'])
def page_user_create():
    """
    Create New User
    """

    form = NewUserForm(request.form)
    if form.validate_on_submit():
        email = form.email.data.lower()
        user = User.objects(email=email).first()
        # Ein noch unbestaetigtes Konto gehoert nicht sicher dem Absender der
        # ersten Registrierung: eine neue Registrierung ersetzt seine Daten
        if not user or user.email_unconfirmed:
            if not user:
                user = User(email=email, date_added=datetime.now())
            # Nur diese Felder: Rolle, Rechte oder die id darf niemand selbst setzen
            user.first_name = form.first_name.data
            user.last_name = form.last_name.data
            user.phone = form.phone.data
            user.birthdate = form.birthdate.data
            user.club_id = form.club_id.data
            user.media_optin = form.media_optin.data
            user.media_optin_date = datetime.now() if user.media_optin else None
            user.data_optin = form.data_optin.data
            user.email_unconfirmed = True
            user.set_password(form.password.data)
            try:
                user.save()
            except NotUniqueError:
                user = None
            if user and mail_allowed(user.email):
                send_email(user.email, "E-Mail-Adresse bestätigen", 'email/confirm_email',
                           user=user, token=user.generate_token(CONFIRM_TOKEN_SECONDS))
        # Gleiche Antwort auch bei vergebener Adresse, sonst laesst sich
        # abfragen, wer ein Konto hat
        flash("Wir haben dir einen Link zur Bestätigung deiner E-Mail-Adresse geschickt. "
              "Falls du schon ein Konto hast, setze dein Passwort über "
              "„Passwort vergessen“ zurück.", 'info')
        return redirect(url_for('auth.login'))

    if form.errors:
        flash("Bitte behebe die angezeigten Fehler in den Feldern", 'danger')

    context = {
        'form': form
    }

    return render_template('user_create.html', **context)


@USER.route('/user/confirm-resend', methods=['POST', 'GET'])
@limiter.limit("5/minute;20/hour", methods=['POST'])
def page_user_confirm_resend():
    """
    Bestaetigungsmail erneut anfordern, falls die erste nicht ankam
    """
    form = RequestPasswordForm(request.form)
    if form.validate_on_submit():
        email = form.request_email.data.lower()
        user = User.objects(email=email, email_unconfirmed=True, disabled__ne=True).first()
        # Gleiche Antwort fuer jede Adresse, sonst verraet es, wer ein Konto hat
        if user and mail_allowed(email):
            send_email(user.email, "E-Mail-Adresse bestätigen", 'email/confirm_email',
                       user=user, token=user.generate_token(CONFIRM_TOKEN_SECONDS))
        flash("Falls zu dieser Adresse ein unbestätigtes Konto gehört, haben wir den "
              "Bestätigungslink erneut geschickt. Schau auch im Spam-Ordner nach.", 'info')
        return redirect(url_for('auth.login'))
    return render_template('confirm_resend.html', form=form)


@USER.route('/user/confirm/<token>', methods=['POST', 'GET'])
@limiter.limit(PASSWORD_LIMIT, methods=['POST'])
def page_user_confirm(token):
    """
    E-Mail-Adresse bestaetigen. Neben dem Link aus der Mail braucht es das
    Passwort aus der Registrierung: wer nur das Postfach hat, nutzt
    "Passwort vergessen". Ein Link-Scanner im Postfach bestaetigt so nichts.
    """
    invalid_link = "Der Link ist ungültig oder abgelaufen"
    try:
        claims = read_reset_token(token)
    except ValueError:
        flash(invalid_link, 'danger')
        return redirect(url_for('auth.login'))
    user = User.objects(id=claims.get('userid'), disabled__ne=True).first()
    if not user or claims.get('pwd') != user.password_fingerprint():
        flash(invalid_link, 'danger')
        return redirect(url_for('auth.login'))
    if not user.email_unconfirmed:
        flash("Deine E-Mail-Adresse ist schon bestätigt", 'info')
        return redirect(url_for('auth.login'))

    form = ConfirmEmailForm(request.form)
    if form.validate_on_submit():
        if user.check_password(form.password.data):
            user.email_unconfirmed = False
            user.save()
            flash("E-Mail-Adresse bestätigt. Du kannst dich jetzt anmelden.", 'success')
            return redirect(url_for('auth.login'))
        flash("Passwort ist falsch", 'danger')
    return render_template('formular.html', form=form, title="E-Mail-Adresse bestätigen")
