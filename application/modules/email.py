# Version 1.1
from threading import Thread
from flask import current_app, render_template
from flask_mail import Connection, Mail, Message
from application.models.section import current_section


def send_async_email(app, msg, mail):
    with app.app_context():
        with Connection(mail) as connection:
            msg.send(connection)


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


def send_email(to, subject, template, **kwargs):
    """Mail ueber den Mailserver der Sektion des Requests, ohne Sektion keine"""
    app = current_app._get_current_object()  # pylint: disable=protected-access
    section = current_section()
    if section is None or not section.mail_server:
        return None
    return send_email_inner(to, subject, template, app, mail_state(app, section),
                            section=section, **kwargs)

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
