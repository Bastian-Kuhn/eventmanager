"""
Umzug im Admin-Panel (nur globale Admins): Sektion des Requests als Datei
exportieren, Exportdatei einer anderen Installation als neue Sektion
importieren. Siehe modules/section_transfer.py.
"""
import io
import re
from datetime import datetime

from flask import abort, current_app, flash, redirect, request, send_file, url_for
from flask_admin import BaseView, expose
from flask_login import current_user

from application.models.section import SLUG_PATTERN, normalize_hostname, section_or_none
from application.modules import section_transfer


class TransferView(BaseView):
    """Export und Import ganzer Sektionen"""

    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_global_admin()

    def inaccessible_callback(self, name, **kwargs):
        abort(404)

    @expose('/')
    def index(self):
        return self.render('admin/transfer.html', section=section_or_none())

    @expose('/export', methods=['POST'])
    def export(self):
        section = section_or_none()
        if section is None:
            abort(404)
        data, counts = section_transfer.export_section(section)
        current_app.logger.warning("Sektion %s exportiert von %s: %s",
                                   section.slug, current_user.email, counts)
        name = section.slug if section.pk is not None else normalize_hostname(request.host) or 'export'
        filename = f"{name}-{datetime.now():%Y-%m-%d}.json.gz"
        return send_file(io.BytesIO(data), mimetype='application/gzip',
                         as_attachment=True, download_name=filename)

    @expose('/import', methods=['POST'])
    def import_(self):
        slug = (request.form.get('slug') or '').strip().lower()
        name = (request.form.get('name') or '').strip()
        host = normalize_hostname(request.form.get('hostname'))
        upload = request.files.get('file')
        if not re.fullmatch(SLUG_PATTERN, slug):
            flash("Kurzname: Kleinbuchstaben, Ziffern und Bindestrich.", 'danger')
            return redirect(url_for('.index'))
        if not name or upload is None or not upload.filename:
            flash("Bitte Datei und Name angeben.", 'danger')
            return redirect(url_for('.index'))
        try:
            payload = section_transfer.read_export(upload.read())
            section, counts = section_transfer.import_section(payload, slug, name, [host] if host else [])
        except section_transfer.TransferError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('.index'))
        current_app.logger.warning("Sektion %s importiert von %s: %s",
                                   section.slug, current_user.email, counts)
        flash(f"{section.name} ist angelegt: {counts['events']} Touren, {counts['huts']} Hütten, "
              f"{counts['hut_bookings']} Hüttenbuchungen, {counts['users_created']} neue Konten, "
              f"{counts['users_merged']} vorhandene Konten ergänzt.", 'success')
        return redirect(url_for('.index'))
