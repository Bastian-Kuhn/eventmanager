"""
Bereinigung von Ausgaben, die sonst als HTML, Link oder Excel-Formel wirken wuerden.
"""
import re
from urllib.parse import urlsplit

import nh3
from markupsafe import Markup

# Was CKEditor erzeugt, reicht fuer die Eventbeschreibung; Skripte, Event-Handler
# und javascript:-Links entfernt nh3 unabhaengig von dieser Liste.
_DESCRIPTION_ATTRIBUTES = {
    **{tag: set(attrs) for tag, attrs in nh3.ALLOWED_ATTRIBUTES.items()},
    '*': {'style', 'class'},
    'a': nh3.ALLOWED_ATTRIBUTES['a'] | {'target'},
    'img': nh3.ALLOWED_ATTRIBUTES['img'] | {'width', 'height'},
    'table': {'border', 'cellpadding', 'cellspacing', 'summary'},
    'td': nh3.ALLOWED_ATTRIBUTES['td'] | {'colspan', 'rowspan'},
    'th': nh3.ALLOWED_ATTRIBUTES['th'] | {'colspan', 'rowspan'},
}
_DESCRIPTION_STYLES = {
    'background-color', 'border', 'border-collapse', 'color', 'float', 'font-family',
    'font-size', 'font-style', 'font-weight', 'height', 'margin', 'margin-left',
    'margin-right', 'text-align', 'text-decoration', 'width',
}

_FORMULA_START = ('=', '+', '-', '@', '\t', '\r')
_PLAIN_NUMBER = re.compile(r'^[+-]?[\d\s/().-]+$')


def sanitize_html(value):
    """Von Guides gepflegtes HTML fuer die Ausgabe bereinigen"""
    return Markup(nh3.clean(value or '', attributes=_DESCRIPTION_ATTRIBUTES,
                            filter_style_properties=_DESCRIPTION_STYLES))


def safe_url(value):
    """Nur http(s)-Links durchlassen, sonst leer (kein javascript: im href)"""
    url = (value or '').strip()
    if re.search(r'[\x00-\x1f\\]', url):
        return ''
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return ''
    return url if scheme in ('http', 'https') else ''


def is_local_path(value):
    """True fuer Pfade dieser Seite, auch Browser-Tricks wie //host oder /\\host fallen raus"""
    return bool(re.fullmatch(r'/(?![/\\])[^\\\x00-\x1f]*', value or ''))


def csv_safe(value):
    """Texte so ausgeben, dass Excel sie nicht als Formel ausfuehrt. Telefonnummern bleiben."""
    if isinstance(value, str) and value.startswith(_FORMULA_START) \
            and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value
