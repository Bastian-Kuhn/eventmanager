"""
Portal (PORTAL_HOSTNAME): Sektionen registrieren sich selbst, legen ihren
Admin an und verwalten Einstellungen und Abrechnung. Alles unter /portal,
damit es sich nicht mit den Routen der Sektionen ueberschneidet.
"""
import io
from datetime import datetime, timedelta

from bson import ObjectId
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from mongoengine.errors import NotUniqueError
from PIL import Image

from application import csrf, limiter, section_logo
from application.models.config import Category
from application.models.section import Section, ref_id
from application.models.user import Membership, User
from application.modules import billing as stripe_billing
from application.modules.email import has_own_mail, send_email
from application.auth.views import mail_allowed
from application.portal.forms import RegisterForm, RegisterLoggedInForm, SettingsForm
from application.portal.terms import label_forms, terms
from application.user.views import CONFIRM_TOKEN_SECONDS

PORTAL = Blueprint('PORTAL', __name__, url_prefix='/portal')

# Leere Zeilen fuer neue Kategorien unter den vorhandenen
EMPTY_CATEGORY_ROWS = 3
LOGO_MAX_BYTES = 2 * 1024 * 1024


def _admin_sections(user):
    """Sektionen, die das Konto im Portal verwalten darf"""
    if user.is_global_admin():
        return list(Section.objects.order_by('name'))
    ids = [entry.section.pk for entry in user.memberships if entry.admin]
    return list(Section.objects(pk__in=ids).order_by('name'))


def _managed_section(slug):
    """Sektion laden, die der angemeldete User verwalten darf, sonst 404"""
    section = Section.objects(slug=slug).first()
    if section is None:
        abort(404)
    entry = current_user.membership(section)
    if not (current_user.is_global_admin() or (entry and entry.admin)):
        abort(404)
    return section


def section_address(section):
    host = (section.hostnames or [''])[0]
    return f"https://{host}/" if host else ''


@PORTAL.route('/')
def index():
    """Start: was das Portal ist, und die eigenen Sektionen"""
    sections = _admin_sections(current_user) if current_user.is_authenticated else []
    requests = [(section, other) for section in sections for other in partner_requests(section)]
    return render_template('portal_index.html', sections=sections, address=section_address,
                           requests=requests, trial_days=current_app.config['TRIAL_DAYS'])


def partner_requests(section):
    """Sektionen, die mit section teilen wollen, die section selbst noch nicht eingetragen hat"""
    own = [ref_id(ref) for ref in section._data.get('partner_sections') or []]  # pylint: disable=protected-access
    return list(Section.objects(partner_sections=section.pk, pk__nin=own).order_by('name'))


@PORTAL.route('/sektion/<slug>/partner', methods=['POST'])
@login_required
def accept_partner(slug):
    """Anfrage einer anderen Sektion bestaetigen: ab jetzt teilen beide"""
    section = _managed_section(slug)
    other = Section.objects(pk=request.form.get('partner_id'), partner_sections=section.pk).first() \
        if ObjectId.is_valid(request.form.get('partner_id') or '') else None
    if other is None:
        abort(404)
    Section.objects(pk=section.pk).update_one(add_to_set__partner_sections=other.pk)
    flash(f"{section.name} und {other.name} teilen ab jetzt ihre Touren.", 'success')
    return redirect(url_for('PORTAL.index'))


def _new_host(slug):
    base = current_app.config.get('SECTION_BASE_DOMAIN')
    return f"{slug}.{base}" if base else ''


def _slug_taken(form):
    host = _new_host(form.slug.data)
    if Section.objects(slug=form.slug.data).first() or (host and Section.objects(hostnames=host).first()):
        form.slug.errors.append("Dieser Kurzname ist schon vergeben")
        return True
    return False


def _create_section(form, admin):
    host = _new_host(form.slug.data)
    section = Section(slug=form.slug.data, name=form.section_name.data.strip(),
                      hostnames=[host] if host else [], member_count=form.member_count.data,
                      nav_background_color='#5aaa28',
                      trial_until=datetime.now() + timedelta(days=current_app.config['TRIAL_DAYS']))
    try:
        section.save()
    except NotUniqueError:
        form.slug.errors.append("Dieser Kurzname ist schon vergeben")
        return None
    # Admin und Trainer: ohne Trainerrolle kaeme er nicht in die Tourverwaltung
    User.objects(id=admin.id).update_one(
        push__memberships=Membership(section=section, role='guide', admin=True))
    return section


@PORTAL.route('/registrieren', methods=['GET', 'POST'])
@limiter.limit("10/hour", methods=['POST'])
def register():
    """Neue Sektion mit Testphase anlegen"""
    logged_in = current_user.is_authenticated
    form = label_forms(RegisterLoggedInForm() if logged_in else RegisterForm(), terms())
    if form.validate_on_submit() and not _slug_taken(form):
        if logged_in:
            admin = current_user
        else:
            email = form.email.data.lower()
            if User.objects(email=email).first():
                form.email.errors.append("Für diese Adresse gibt es schon ein Konto. "
                                         "Bitte melde dich zuerst an.")
                return render_template('portal_register.html', form=form, logged_in=logged_in), 200
            admin = User(email=email, first_name=form.first_name.data, last_name=form.last_name.data,
                         date_added=datetime.now(), email_unconfirmed=True)
            admin.set_password(form.password.data)
            try:
                admin.save()
            except NotUniqueError:
                form.email.errors.append("Für diese Adresse gibt es schon ein Konto.")
                return render_template('portal_register.html', form=form, logged_in=logged_in), 200
        section = _create_section(form, admin)
        if section is None:
            return render_template('portal_register.html', form=form, logged_in=logged_in), 200
        if logged_in:
            flash(f"{terms()['The']} {section.name} ist angelegt. Testphase bis "
                  f"{section.trial_until:%d.%m.%Y}.", 'success')
            return redirect(url_for('PORTAL.settings', slug=section.slug))
        if mail_allowed(admin.email):
            send_email(admin.email, "E-Mail-Adresse bestätigen", 'email/confirm_email',
                       user=admin, token=admin.generate_token(CONFIRM_TOKEN_SECONDS))
        flash(f"{terms()['The']} {section.name} ist angelegt. Wir haben dir einen Link zur "
              "Bestätigung deiner E-Mail-Adresse geschickt, danach kannst du dich hier anmelden.",
              'success')
        return redirect(url_for('PORTAL.index'))
    return render_template('portal_register.html', form=form, logged_in=logged_in)


def _own_mail_complete(form, section):
    """Mit eigenem Mailserver: Server noetig, zu einem Benutzer auch das Passwort"""
    if not form.own_mail.data:
        return True
    ok = True
    if not (form.mail_server.data or '').strip():
        form.mail_server.errors.append("Für den eigenen Mailserver bitte ausfüllen")
        ok = False
    if (form.mail_username.data or '').strip() and not (form.mail_password.data or section.mail_password):
        form.mail_password.errors.append("Zum Benutzer bitte das Passwort eintragen")
        ok = False
    return ok


def _logo_bytes(upload):
    """Hochgeladenes Logo pruefen, verkleinert als PNG zurueck, sonst None"""
    data = upload.read(LOGO_MAX_BYTES + 1)
    if len(data) > LOGO_MAX_BYTES:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:  # pylint: disable=broad-except
        return None
    image.thumbnail((600, 240))
    out = io.BytesIO()
    image.save(out, 'PNG')
    out.seek(0)
    return out


def _settings_page(form, section):
    # Wer diese Sektion selbst eingetragen hat: dort ist das Teilen beidseitig
    incoming = {str(other.pk) for other in Section.objects(partner_sections=section.pk)}
    return render_template('portal_settings.html', form=form, section=section, incoming=incoming,
                           address=section_address(section), logo=section_logo(section))


@PORTAL.route('/sektion/<slug>', methods=['GET', 'POST'])
@login_required
def settings(slug):
    """Einstellungen einer Sektion"""
    section = _managed_section(slug)
    form = label_forms(SettingsForm(obj=section), terms())
    form.partner_sections.choices = [(str(other.pk), other.name) for other in
                                     Section.objects(pk__ne=section.pk).order_by('name')]
    if request.method == 'GET':
        form.own_mail.data = has_own_mail(section)
        form.partner_sections.data = [ref_id(ref) for ref in section._data.get('partner_sections') or []]  # pylint: disable=protected-access
    if request.method == 'GET':
        for category in section.event_categories_full:
            form.categories.append_entry({'name': category.name, 'color': category.color})
        for _ in range(EMPTY_CATEGORY_ROWS):
            form.categories.append_entry({'name': '', 'color': '#5aaa28'})
    if form.validate_on_submit() and _own_mail_complete(form, section):
        if form.logo.data:
            logo = _logo_bytes(form.logo.data)
            if logo is None:
                form.logo.errors.append("Das Bild ließ sich nicht lesen oder ist größer als 2 MB")
                return _settings_page(form, section)
            section.logo_image.replace(logo, content_type='image/png', filename='logo.png')
        old_members = section.member_count
        for field in ('name', 'member_count', 'nav_background_color', 'homepage_link',
                      'media_consent_text'):
            setattr(section, field, getattr(form, field).data or None)
        if form.own_mail.data:
            for field in ('mail_server', 'mail_username', 'mail_sender'):
                setattr(section, field, (getattr(form, field).data or '').strip() or None)
            section.mail_use_tls = bool(form.mail_use_tls.data)
            if form.mail_password.data:
                section.mail_password = form.mail_password.data
        else:
            # Versand ueber die Plattform: alte Zugangsdaten nicht liegen lassen
            section.mail_server = section.mail_username = section.mail_password = None
            section.mail_sender = None
            section.mail_use_tls = False
        section.event_categories_full = [
            Category(name=entry.form.name.data.strip(), color=entry.form.color.data or '#5aaa28')
            for entry in form.categories if (entry.form.name.data or '').strip()]
        section.partner_sections = list(Section.objects(pk__in=form.partner_sections.data or []))
        section.save()
        if section.member_count != old_members:
            try:
                stripe_billing.sync_quantity(section)
            except Exception:  # pylint: disable=broad-except
                current_app.logger.exception("Stripe-Menge fuer %s nicht angepasst", section.slug)
                flash("Gespeichert, aber die Menge im Abo ließ sich nicht anpassen. "
                      "Bitte melde dich bei uns.", 'warning')
                return redirect(url_for('PORTAL.settings', slug=section.slug))
        flash("Einstellungen gespeichert", 'success')
        return redirect(url_for('PORTAL.settings', slug=section.slug))
    return _settings_page(form, section)


@PORTAL.route('/sektion/<slug>/abrechnung')
@login_required
def billing(slug):
    """Stand der Abrechnung"""
    section = _managed_section(slug)
    return render_template('portal_billing.html', section=section,
                           billing_enabled=stripe_billing.billing_enabled(),
                           units=stripe_billing.units(section), price=stripe_billing.monthly_price(section),
                           yearly_price=stripe_billing.yearly_price(section),
                           yearly=stripe_billing.yearly_available(),
                           per_unit=current_app.config['MEMBERS_PER_UNIT'],
                           unit_price=current_app.config['PRICE_PER_UNIT_EUR'],
                           unit_price_yearly=current_app.config['PRICE_PER_UNIT_YEARLY_EUR'],
                           now=datetime.now())


@PORTAL.route('/sektion/<slug>/abrechnung/abo', methods=['POST'])
@login_required
def checkout(slug):
    """Weiter zu Stripe Checkout"""
    section = _managed_section(slug)
    if section.is_paid:
        return redirect(url_for('PORTAL.billing', slug=slug))
    try:
        interval = 'year' if request.form.get('interval') == 'year' else 'month'
        return redirect(stripe_billing.create_checkout(section, current_user, interval), code=303)
    except stripe_billing.BillingError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('PORTAL.billing', slug=slug))


@PORTAL.route('/sektion/<slug>/abrechnung/verwalten', methods=['POST'])
@login_required
def customer_portal(slug):
    """Weiter zum Stripe-Kundenportal"""
    section = _managed_section(slug)
    try:
        return redirect(stripe_billing.create_portal(section), code=303)
    except stripe_billing.BillingError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('PORTAL.billing', slug=slug))


@PORTAL.route('/stripe/webhook', methods=['POST'])
@csrf.exempt
@limiter.exempt
def webhook():
    """Stripe meldet Abo-Aenderungen, nur mit gueltiger Signatur"""
    try:
        event = stripe_billing.verify_webhook(request.get_data(), request.headers.get('Stripe-Signature', ''))
    except stripe_billing.BillingError:
        return {'ok': False}, 400
    stripe_billing.handle_event(event)
    return {'ok': True}

