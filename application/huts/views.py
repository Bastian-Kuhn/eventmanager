"""
Öffentliche Hütten-Seiten: Übersicht, Detailseite mit Selbst-Buchung durch
Mitglieder (sofort bestätigt oder – je Hütte – Freigabe durch Hütten-Admin/Guide)
sowie eine Freigabe-Übersicht für Hütten-Admins.
"""
#pylint: disable=no-member
import calendar as _calendar
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user, login_required

from application.huts.models import Hut, HutBooking

HUTS = Blueprint('HUTS', __name__)

MONTH_NAMES_DE = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                  "August", "September", "Oktober", "November", "Dezember"]
WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _parse_date(value):
    """'YYYY-MM-DD' -> date oder None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _manageable_huts(user):
    """Hütten, deren Buchungen der User freigeben darf."""
    if not (user and user.is_authenticated):
        return []
    if user.has_right('guide'):
        return list(Hut.objects.order_by('name'))
    return list(Hut.objects(admins=user).order_by('name'))


@HUTS.route('/huetten')
def page_huts():
    """Öffentliche Übersicht aller Hütten. Bei nur einer Hütte direkt zur Detailseite."""
    if Hut.objects.count() == 1:
        only = Hut.objects.first()
        return redirect(url_for('HUTS.page_hut_detail', hut_id=only.id))

    from_date = _parse_date(request.args.get('from_date'))
    to_date = _parse_date(request.args.get('to_date'))
    range_active = bool(from_date and to_date and from_date < to_date)

    huts = []
    for hut in Hut.objects.order_by('name'):
        huts.append({
            'hut': hut,
            'total_places': hut.total_places(),
            'free_places': hut.free_places(from_date, to_date) if range_active else None,
        })

    return render_template(
        'huts_list.html',
        huts=huts,
        from_date=request.args.get('from_date', ''),
        to_date=request.args.get('to_date', ''),
        range_active=range_active,
    )


@HUTS.route('/huetten/verwaltung')
@login_required
def page_hut_admin():
    """Freigabe-Übersicht für Hütten-Admins/Guides über alle verwalteten Hütten."""
    huts = _manageable_huts(current_user)
    if not huts:
        abort(403)
    groups = []
    for hut in huts:
        pending = list(HutBooking.objects(hut=hut, confirmed=False).order_by('from_date'))
        upcoming = list(HutBooking.objects(hut=hut, confirmed=True).order_by('from_date'))
        groups.append({'hut': hut, 'pending': pending, 'confirmed': upcoming})
    return render_template('hut_admin.html', groups=groups)


@HUTS.route('/huetten/<hut_id>')
def page_hut_detail(hut_id):
    """Detailseite einer Hütte inkl. Buchungsformular und Buchungsstatus."""
    hut = Hut.objects(id=hut_id).first()
    if not hut:
        abort(404)

    from_date = _parse_date(request.args.get('from_date'))
    to_date = _parse_date(request.args.get('to_date'))
    range_active = bool(from_date and to_date and from_date < to_date)

    can_manage = hut.can_manage(current_user)
    my_bookings, pending = [], []
    if current_user.is_authenticated:
        my_bookings = list(HutBooking.objects(hut=hut, user=current_user).order_by('from_date'))
    if can_manage:
        pending = list(HutBooking.objects(hut=hut, confirmed=False).order_by('from_date'))

    context = {
        'hut': hut,
        'total_places': hut.total_places(),
        'free_places': hut.free_places(from_date, to_date) if range_active else None,
        'from_date': request.args.get('from_date', ''),
        'to_date': request.args.get('to_date', ''),
        'range_active': range_active,
        'can_manage': can_manage,
        'my_bookings': my_bookings,
        'pending': pending,
    }
    return render_template('hut_detail.html', **context)


@HUTS.route('/huetten/<hut_id>/kalender')
def page_hut_calendar(hut_id):
    """Belegungskalender einer Hütte (Auslastung pro Tag)."""
    hut = Hut.objects(id=hut_id).first()
    if not hut:
        abort(404)

    today = datetime.now().date()
    try:
        year = int(request.args.get('year') or today.year)
        month = int(request.args.get('month') or today.month)
    except ValueError:
        year, month = today.year, today.month
    if not 1 <= month <= 12:
        year, month = today.year, today.month

    total = hut.total_places()
    first = date(year, month, 1)
    next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    # Belegte Plätze pro Tag (halb-offen: Abreisetag zählt nicht mehr)
    occ = {}
    for booking in HutBooking.objects(hut=hut, from_date__lt=next_first, to_date__gt=first):
        if not (booking.from_date and booking.to_date):
            continue
        day = max(booking.from_date, first)
        end = min(booking.to_date, next_first)
        while day < end:
            occ[day] = occ.get(day, 0) + (booking.places or 0)
            day += timedelta(days=1)

    weeks = []
    for week in _calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row = []
        for day in week:
            used = occ.get(day, 0)
            ratio = (used / total) if total else 0
            if used == 0:
                level = 'free'
            elif ratio >= 1:
                level = 'full'
            elif ratio >= 0.75:
                level = 'high'
            else:
                level = 'low'
            row.append({
                'date': day,
                'in_month': day.month == month,
                'is_today': day == today,
                'used': used,
                'free': total - used,
                'level': level,
            })
        weeks.append(row)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    context = {
        'hut': hut,
        'total_places': total,
        'weeks': weeks,
        'weekdays': WEEKDAYS_DE,
        'year': year,
        'month': month,
        'month_name': MONTH_NAMES_DE[month],
        'prev_year': prev_year, 'prev_month': prev_month,
        'next_year': next_year, 'next_month': next_month,
    }
    return render_template('hut_calendar.html', **context)


@HUTS.route('/huetten/<hut_id>/book', methods=['POST'])
@login_required
def book_hut(hut_id):
    """Mitglied bucht Plätze für einen Zeitraum."""
    hut = Hut.objects(id=hut_id).first()
    if not hut:
        abort(404)

    from_date = _parse_date(request.form.get('from_date'))
    to_date = _parse_date(request.form.get('to_date'))
    try:
        places = int(request.form.get('places') or 0)
    except ValueError:
        places = 0
    rooms = [r for r in request.form.getlist('rooms') if r]
    comment = request.form.get('comment') or None

    back = url_for('HUTS.page_hut_detail', hut_id=hut_id,
                   from_date=request.form.get('from_date', ''),
                   to_date=request.form.get('to_date', ''))

    if not (from_date and to_date and from_date < to_date):
        flash("Bitte einen gültigen Zeitraum wählen (Abreise nach Anreise).", 'danger')
        return redirect(back)
    if places < 1:
        flash("Bitte die Anzahl der Plätze angeben.", 'danger')
        return redirect(back)

    free = hut.free_places(from_date, to_date)
    if places > free:
        flash(f"Nicht genügend freie Plätze: im Zeitraum sind nur noch {free} frei.", 'danger')
        return redirect(back)

    confirmed = not hut.requires_approval
    HutBooking(
        hut=hut,
        from_date=from_date,
        to_date=to_date,
        places=places,
        rooms=rooms,
        comment=comment,
        name=f"{current_user.first_name} {current_user.last_name}",
        user=current_user,
        confirmed=confirmed,
    ).save()

    if confirmed:
        flash("Buchung bestätigt.", 'success')
    else:
        flash("Buchungsanfrage gesendet – sie wartet auf Freigabe durch die Hütten-Verwaltung.",
              'info')
    return redirect(url_for('HUTS.page_hut_detail', hut_id=hut_id))


def _load_booking_or_404(hut_id, booking_id):
    hut = Hut.objects(id=hut_id).first()
    if not hut:
        abort(404)
    booking = HutBooking.objects(id=booking_id, hut=hut).first()
    if not booking:
        abort(404)
    return hut, booking


@HUTS.route('/huetten/<hut_id>/booking/<booking_id>/cancel', methods=['POST'])
@login_required
def cancel_booking(hut_id, booking_id):
    """Eigene Buchung stornieren (oder durch Hütten-Admin/Guide entfernen/ablehnen)."""
    hut, booking = _load_booking_or_404(hut_id, booking_id)
    is_own = booking.user and str(booking.user.id) == str(current_user.id)
    if not (is_own or hut.can_manage(current_user)):
        abort(403)
    booking.delete()
    flash("Buchung storniert.", 'success')
    return redirect(request.form.get('next') or url_for('HUTS.page_hut_detail', hut_id=hut_id))


@HUTS.route('/huetten/<hut_id>/booking/<booking_id>/confirm', methods=['POST'])
@login_required
def confirm_booking(hut_id, booking_id):
    """Hütten-Admin/Guide gibt eine wartende Buchung frei."""
    hut, booking = _load_booking_or_404(hut_id, booking_id)
    if not hut.can_manage(current_user):
        abort(403)
    booking.confirmed = True
    booking.save()
    flash("Buchung freigegeben.", 'success')
    return redirect(request.form.get('next') or url_for('HUTS.page_hut_detail', hut_id=hut_id))
