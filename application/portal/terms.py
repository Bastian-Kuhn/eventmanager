"""
Wortlaut des Portals: dieselben Seiten sagen auf sektion.* "Sektion" und auf
verein.* "Verein". Die Formen stehen hier ausgeschrieben, weil Sektion und
Verein verschiedene Geschlechter haben.
"""
from application.models.section import portal_kind

TERMS = {
    'sektion': {
        'one': 'Sektion', 'many': 'Sektionen', 'portal': 'Sektionsportal',
        'the': 'die Sektion', 'The': 'Die Sektion', 'this': 'Diese Sektion',
        'gen': 'der Sektion', 'gen_new': 'der neuen Sektion',
        'new': 'Neue Sektion', 'more': 'Weitere Sektion', 'no_dat': 'keiner Sektion', 'other_dat': 'einer anderen Sektion',
        'it': 'sie',
    },
    'verein': {
        'one': 'Verein', 'many': 'Vereine', 'portal': 'Vereinsportal',
        'the': 'der Verein', 'The': 'Der Verein', 'this': 'Dieser Verein',
        'gen': 'des Vereins', 'gen_new': 'des neuen Vereins',
        'new': 'Neuen Verein', 'more': 'Weiteren Verein', 'no_dat': 'keinem Verein', 'other_dat': 'einem anderen Verein',
        'it': 'er',
    },
}


def terms():
    """Formen fuer den laufenden Request, ausserhalb des Portals die der Sektion"""
    return TERMS.get(portal_kind() or 'sektion', TERMS['sektion'])


def label_forms(form, t):
    """Feldbeschriftungen der Portal-Formulare an den Wortlaut anpassen"""
    for name, text in (('section_name', f"Name {t['gen']}"), ('name', f"Name {t['gen']}"),
                       ('homepage_link', f"Link hinter dem Logo (Homepage {t['gen']})"),
                       ('submit', f"{t['one']} registrieren")):
        field = getattr(form, name, None)
        if field is not None and not (name == 'submit' and field.label.text == 'Speichern'):
            field.label.text = text
    return form
