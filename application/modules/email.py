# Version 1.1
import html
import re
import smtplib
from threading import Thread
from flask import current_app, render_template
from flask_mail import Connection, Mail, Message
from email.utils import formataddr
from application.models.section import section_or_none


def html_to_text(markup):
    """
    Textteil aus dem HTML der Mail. Ohne ihn werten Spamfilter die Mail ab
    (MPART_ALT_DIFF). Links bleiben als Adresse in Klammern erhalten.
    """
    text = re.sub(r'(?is)<(script|style|head)\b.*?</\1>', '', markup)
    text = re.sub(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                  lambda m: m.group(2) if m.group(1) in m.group(2) else f"{m.group(2)} ({m.group(1)})", text)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|h[1-6]|li|tr|table)>', '\n\n', text)
    text = re.sub(r'(?i)<li\b[^>]*>', '- ', text)
    text = html.unescape(re.sub(r'<[^>]+>', '', text))
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip() + '\n'


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
        msg.body = html_to_text(msg.html)
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


TEST_MAIL_TIMEOUT = 20
TEST_LOG_LINE_MAX = 400


def send_test_mail(section, to):
    """
    Testmail synchron auf demselben Weg wie send_email (eigener Mailserver der
    Sektion, sonst Plattform), damit Fehler sichtbar werden statt im Thread zu
    verschwinden. Gibt (ok, Protokollzeilen) zurueck. Die Anmeldung laeuft
    ohne Mitschnitt, Zugangsdaten stehen nicht im Protokoll.
    """
    app = current_app._get_current_object()  # pylint: disable=protected-access
    log = []
    if has_own_mail(section):
        mail = mail_state(app, section)
        sender = section.mail_sender or section.mail_username
        log.append(f"Eigener Mailserver der Sektion: {mail.server}:{mail.port}")
    else:
        platform = PlatformSender(app, section.name if section is not None else None)
        mail, sender = platform.state, platform.mail_sender
        log.append(f"Mailserver der Plattform: {mail.server or '(nicht eingerichtet)'}:{mail.port}")
    if not mail.server or not sender:
        log.append("Kein Mailserver oder kein Absender eingerichtet, es wird nichts verschickt.")
        return False, log
    log.append(f"SSL: {'ja' if mail.use_ssl else 'nein'}, zusätzlich STARTTLS: "
               f"{'ja' if mail.use_tls else 'nein'}, Benutzer: {mail.username or '(ohne Anmeldung)'}")
    log.append(f"Absender: {sender}, Empfänger: {to}")

    def capture(*args):
        line = ' '.join(str(arg) for arg in args)
        log.append(line if len(line) <= TEST_LOG_LINE_MAX else line[:TEST_LOG_LINE_MAX] + ' …')

    msg = Message("Testmail Tourenanmeldung", sender=sender, recipients=[to],
                  body="Diese Testmail kam über die Mail-Einstellungen in der Verwaltung. "
                       "Wenn du sie liest, funktioniert der Versand.\n")
    smtp_class = smtplib.SMTP_SSL if mail.use_ssl else smtplib.SMTP
    host = smtp_class(timeout=TEST_MAIL_TIMEOUT)
    host._print_debug = capture  # pylint: disable=protected-access
    host.set_debuglevel(1)
    try:
        host.connect(mail.server, mail.port)
        if mail.use_tls:
            host.starttls()
        if mail.username and mail.password:
            host.set_debuglevel(0)
            host.login(mail.username, mail.password)
            host.set_debuglevel(1)
            log.append(f"Anmeldung als {mail.username}: erfolgreich")
        host.sendmail(sender, [to], msg.as_bytes())
        host.quit()
    except Exception as error:  # pylint: disable=broad-except
        log.append(f"Fehler: {type(error).__name__}: {error}")
        if mail.use_tls and mail.use_ssl:
            log.append("Hinweis: Port 465 ist schon per SSL verschlüsselt, "
                       "„TLS am Mailserver aktivieren“ bitte ausschalten.")
        try:
            host.close()
        except Exception:  # pylint: disable=broad-except
            pass
        return False, log
    log.append("Der Mailserver hat die Mail angenommen.")
    return True, log
