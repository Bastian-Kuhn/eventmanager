# Version 1.1
from threading import Thread
from flask import current_app, render_template
from flask_mail import Connection, Mail, Message
from email.utils import formataddr
from application.models.section import section_or_none


def send_async_email(app, msg, mail):
    with app.app_context():
        with Connection(mail) as connection:
            msg.send(connection)


def has_own_mail(section):
    """
    Eigener Mailserver nur, wenn einer eingetragen ist und ein Benutzer auch
    sein Passwort hat (ohne Benutzer: Relay ohne Login). Sonst Plattform.
    """
    return bool(section is not None and (section.mail_server or '').strip()
                and (section.mail_password or not section.mail_username))


def mail_state(app, section):
    """
    SMTP-Einstellungen der Sektion als eigener Flask-Mail-State. Nicht ueber
    app.config: das teilen sich alle Threads, parallele Mails verschiedener
    Sektionen wuerden sich sonst die Zugangsdaten ueberschreiben.
    """
    return Mail().init_mail({
        'MAIL_SERVER': section.mail_server,
        'MAIL_USERNAME': section.mail_username,
        'MAIL_PASSWORD': section.mail_password,
        'MAIL_PORT': 465,
        'MAIL_USE_TLS': bool(section.mail_use_tls),
        'MAIL_USE_SSL': True,
        'MAIL_DEFAULT_SENDER': section.mail_sender,
    }, app.debug, app.testing)


class PlatformSender:  # pylint: disable=too-few-public-methods
    """
    Versand ueber den Mailserver der Plattform, fuer Sektionen ohne eigenen
    und fuers Portal. Absender ist die Plattform-Adresse mit dem Namen der
    Sektion, damit die Mail nicht als gefaelscht im Spam landet.
    """

    def __init__(self, app, name):
        config = app.config
        self.mail_server = config.get('PLATFORM_MAIL_SERVER')
        self.mail_sender = formataddr((name or 'Tourenanmeldung', config.get('PLATFORM_MAIL_SENDER') or ''))
        self.mail_subject_prefix = ''
        self.state = Mail().init_mail({
            'MAIL_SERVER': self.mail_server,
            'MAIL_USERNAME': config.get('PLATFORM_MAIL_USERNAME'),
            'MAIL_PASSWORD': config.get('PLATFORM_MAIL_PASSWORD'),
            'MAIL_PORT': config.get('PLATFORM_MAIL_PORT') or 465,
            'MAIL_USE_TLS': False,
            'MAIL_USE_SSL': True,
            'MAIL_DEFAULT_SENDER': self.mail_sender,
        }, app.debug, app.testing)


def send_email(to, subject, template, **kwargs):
    """
    Mail ueber den Mailserver der Sektion des Requests. Ohne eigenen (und im
    Portal) ueber den der Plattform, ist auch der nicht eingerichtet: keine.
    """
    app = current_app._get_current_object()  # pylint: disable=protected-access
    section = section_or_none()
    if has_own_mail(section):
        if not section.mail_sender and section.mail_username:
            kwargs.setdefault('SENDER', section.mail_username)
        return send_email_inner(to, subject, template, app, mail_state(app, section),
                                section=section, **kwargs)
    sender = PlatformSender(app, section.name if section is not None else None)
    if not sender.mail_server or not app.config.get('PLATFORM_MAIL_SENDER'):
        return None
    return send_email_inner(to, subject, template, app, sender.state, section=sender, **kwargs)

def send_email_inner(to, subject, template, app, mail, section, **kwargs):
    with app.app_context():

        if 'SENDER' in kwargs:
            sender = kwargs['SENDER']
        else:
            sender = section.mail_sender

        msg = Message(
            f"{section.mail_subject_prefix or ''} {subject}".strip(),
            sender=sender,
            recipients=[to]
        )
        # msg.body = render_template(template + '.txt', **kwargs)
        msg.html = render_template(template + '.html', **kwargs)
        if 'attachment_file' in kwargs:
            msg.attach(
                kwargs['attachment_name'],
                kwargs['attachment_mime'],
                kwargs['attachment_file']
            )

        if 'attachment_path' in kwargs:
            with app.open_resource(kwargs['attachment_path']) as attachment:
                msg.attach(
                    kwargs['attachment_name'],
                    kwargs['attachment_mime'],
                    attachment.read()
                )

        thr = Thread(target=send_async_email, args=[app, msg, mail])
        thr.start()
        return thr
