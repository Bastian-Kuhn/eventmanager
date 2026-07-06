"""
Öffentliche Hütten-Seite: Liste aller Hütten inkl. Kapazität und – optional
für einen gewählten Zeitraum – freien Plätzen.
"""
#pylint: disable=no-member
from datetime import datetime
from flask import Blueprint, render_template, request

from application.huts.models import Hut

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
    """
    Öffentliche Übersicht aller Hütten.
    """
    from_date = _parse_date(request.args.get('from_date'))
    to_date = _parse_date(request.args.get('to_date'))
    # Nur wenn ein gültiger, positiver Zeitraum gewählt wurde, freie Plätze zeigen
    range_active = bool(from_date and to_date and from_date < to_date)

    huts = []
    for hut in Hut.objects.order_by('name'):
        info = {
            'hut': hut,
            'total_places': hut.total_places(),
            'free_places': hut.free_places(from_date, to_date) if range_active else None,
        }
        huts.append(info)

    context = {
        'huts': huts,
        'from_date': request.args.get('from_date', ''),
        'to_date': request.args.get('to_date', ''),
        'range_active': range_active,
    }
    return render_template('huts_list.html', **context)
