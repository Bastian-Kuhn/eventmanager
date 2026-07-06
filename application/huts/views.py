"""
Öffentliche Hütten-Seiten: Übersicht, Detailseite mit Selbst-Buchung durch
Mitglieder (sofort bestätigt oder – je Hütte – Freigabe durch Hütten-Admin/Guide).
"""
#pylint: disable=no-member
import uuid
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user, login_required

from application.huts.models import Hut, HutBooking

HUTS = Blueprint('HUTS', __name__)


def _parse_date(value):
    """'YYYY-MM-DD' -> date oder None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@HUTS.route('/huetten')
def page_huts():
    """Öffentliche Übersicht aller Hütten."""
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
        my_bookings = [b for b in hut.bookings
                       if b.user and str(b.user.id) == str(current_user.id)]
    if can_manage:
        pending = [b for b in hut.bookings if not b.confirmed]

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
    room = request.form.get('room') or None
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
    hut.bookings.append(HutBooking(
        booking_id=uuid.uuid4().hex,
        from_date=from_date,
        to_date=to_date,
        places=places,
        room=room,
        comment=comment,
        name=f"{current_user.first_name} {current_user.last_name}",
        user=current_user,
        confirmed=confirmed,
    ))
    hut.save()

    if confirmed:
        flash("Buchung bestätigt.", 'success')
    else:
        flash("Buchungsanfrage gesendet – sie wartet auf Freigabe durch die Hütten-Verwaltung.",
              'info')
    return redirect(url_for('HUTS.page_hut_detail', hut_id=hut_id))


@HUTS.route('/huetten/<hut_id>/booking/<booking_id>/cancel', methods=['POST'])
@login_required
def cancel_booking(hut_id, booking_id):
    """Eigene Buchung stornieren (oder durch Hütten-Admin/Guide entfernen/ablehnen)."""
    hut = Hut.objects(id=hut_id).first()
    if not hut:
        abort(404)
    booking = hut.get_booking(booking_id)
    if not booking:
        abort(404)

    is_own = booking.user and str(booking.user.id) == str(current_user.id)
    if not (is_own or hut.can_manage(current_user)):
        abort(403)

    hut.bookings = [b for b in hut.bookings if b.booking_id != booking_id]
    hut.save()
    flash("Buchung storniert.", 'success')
    return redirect(url_for('HUTS.page_hut_detail', hut_id=hut_id))


@HUTS.route('/huetten/<hut_id>/booking/<booking_id>/confirm', methods=['POST'])
@login_required
def confirm_booking(hut_id, booking_id):
    """Hütten-Admin/Guide gibt eine wartende Buchung frei."""
    hut = Hut.objects(id=hut_id).first()
    if not hut:
        abort(404)
    if not hut.can_manage(current_user):
        abort(403)
    booking = hut.get_booking(booking_id)
    if not booking:
        abort(404)

    booking.confirmed = True
    hut.save()
    flash("Buchung freigegeben.", 'success')
    return redirect(url_for('HUTS.page_hut_detail', hut_id=hut_id))
