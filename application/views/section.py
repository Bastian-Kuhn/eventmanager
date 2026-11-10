"""
Sektionen im Admin: Adresse, Design und Mailversand je Verein
"""
from wtforms import TextAreaField
from flask_login import current_user
from application.views.default import CustomModelView


class SectionView(CustomModelView):  # pylint: disable=too-few-public-methods
    """Sektionen verwalten, nur globale Admins"""

    can_edit = True
    can_delete = False
    can_create = True

    column_list = ('name', 'slug', 'hostnames', 'is_default')
    form_overrides = {'media_consent_text': TextAreaField}
    form_widget_args = {'media_consent_text': {'rows': 15}}
    column_labels = {
        'name': 'Name',
        'slug': 'Kurzname',
        'hostnames': 'Adressen',
        'is_default': 'Standard',
        'nav_background_color': 'Farbe der Navigation',
        'homepage_link': 'Link hinter dem Logo',
        'logo_image': 'Logo',
        'event_categories_full': 'Kategorien',
        'mail_sender': 'Absender',
        'mail_server': 'Mailserver',
        'mail_use_tls': 'TLS',
        'mail_username': 'Benutzer Mailserver',
        'mail_password': 'Passwort Mailserver',
        'mail_subject_prefix': 'Betreff-Präfix',
        'media_consent_text': 'Einwilligungstext Foto/Video (HTML)',
    }
    column_descriptions = {
        'slug': 'Kleinbuchstaben, Ziffern und Bindestrich, z.B. dav-beispiel.',
        'hostnames': 'Hostnamen ohne https:// und Port, z.B. touren.example.org.',
        'is_default': 'Gilt für alle Adressen, die keiner Sektion gehören. Nur eine Sektion.',
        'media_consent_text': 'Wird bei Registrierung, Profil und Buchung angezeigt. '
                              'Leer lassen für den Standardtext (DAV Sektion Kampenwand).',
    }

    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_global_admin()
