"""
Websites im Admin-Panel: Admins der Sektion legen Websites mit dem
WordPress-Plugin an, bekommen den Schluessel (nur einmal sichtbar), waehlen
den SSO-Modus und geben offene Verknuepfungen frei.
"""
# pylint: disable=no-member, protected-access
from urllib.parse import urlparse

from bson import ObjectId
from flask import abort, flash, redirect, request, url_for
from flask_admin import BaseView, expose
from flask_login import current_user

from application.models.api_token import ApiToken
from application.models.section import ref_id, section_or_none
from application.models.user import User
from application.models.website import SSO_MODE_KEYS, Website, WebsiteCode, WebsiteLink, sso_modes


def valid_return_url(url):
    """https, nur fuer lokale Tests auch http"""
    parsed = urlparse(url)
    if not parsed.netloc or parsed.fragment:
        return False
    return parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1'))


def form_values():
    """(Name, Ruecksprung, Modus) aus dem Formular oder None mit Hinweis per flash"""
    name = (request.form.get('name') or '').strip()[:100]
    return_url = (request.form.get('return_url') or '').strip()[:500]
    mode = request.form.get('sso_mode') or 'off'
    if not name or mode not in SSO_MODE_KEYS:
        flash("Bitte gib einen Namen an.", 'danger')
        return None
    if not valid_return_url(return_url):
        flash("Die Rücksprungadresse muss mit https:// beginnen. Das Plugin zeigt sie in seinen Einstellungen an.",
              'danger')
        return None
    return name, return_url, mode


class WebsiteView(BaseView):
    """Websites der Sektion des Requests"""

    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_admin() and section_or_none() is not None

    def inaccessible_callback(self, name, **kwargs):
        abort(404)

    def website(self, website_id):
        """Website dieser Sektion oder 404"""
        found = Website.objects(id=website_id, section=section_or_none().pk).first() \
            if ObjectId.is_valid(website_id) else None
        if found is None:
            abort(404)
        return found

    def render_index(self, new_key=None, new_key_site=None):
        section = section_or_none()
        websites = list(Website.objects(section=section.pk).order_by('name'))
        links = WebsiteLink.objects(website__in=[x.pk for x in websites]).order_by('-created')
        users = {str(x.pk): x for x in User.objects(id__in=[ref_id(x._data.get('user'))
                                                            for x in links if x._data.get('user')])}
        rows = [(link, users.get(ref_id(link._data.get('user')))) for link in links]
        return self.render('admin/websites.html', section=section, websites=websites, modes=sso_modes,
                           mode_names=dict(sso_modes), new_key=new_key, new_key_site=new_key_site,
                           pending=[x for x in rows if not x[0].active],
                           active=[x for x in rows if x[0].active])

    @expose('/')
    def index(self):
        return self.render_index()

    @expose('/neu', methods=['POST'])
    def create(self):
        values = form_values()
        if values is None:
            return redirect(url_for('.index'))
        name, return_url, mode = values
        website = Website(section=section_or_none(), name=name, return_url=return_url, sso_mode=mode)
        raw = website.new_key()
        website.save()
        return self.render_index(new_key=raw, new_key_site=website)

    @expose('/<website_id>/speichern', methods=['POST'])
    def update(self, website_id):
        website = self.website(website_id)
        values = form_values()
        if values is not None:
            website.name, website.return_url, website.sso_mode = values
            website.save()
            flash(f"{website.name} ist gespeichert.", 'success')
        return redirect(url_for('.index'))

    @expose('/<website_id>/schluessel', methods=['POST'])
    def rekey(self, website_id):
        website = self.website(website_id)
        raw = website.new_key()
        website.save()
        return self.render_index(new_key=raw, new_key_site=website)

    @expose('/<website_id>/loeschen', methods=['POST'])
    def delete(self, website_id):
        website = self.website(website_id)
        ApiToken.objects(website=website.pk).delete()
        WebsiteLink.objects(website=website.pk).delete()
        WebsiteCode.objects(website=website.pk).delete()
        website.delete()
        flash(f"{website.name} ist entfernt, angemeldete Bucher sind dort abgemeldet.", 'info')
        return redirect(url_for('.index'))

    @expose('/verknuepfung', methods=['POST'])
    def link(self):
        """Offene Verknuepfung freigeben oder ablehnen, aktive trennen"""
        section = section_or_none()
        link_id = request.form.get('link_id') or ''
        link = WebsiteLink.objects(id=link_id).first() if ObjectId.is_valid(link_id) else None
        website = Website.objects(id=ref_id(link._data.get('website')), section=section.pk).first() \
            if link else None
        if website is None:
            abort(404)
        if request.form.get('action') == 'accept' and not link.active:
            # Sonst Kreisimport: site.py braucht das Admin-Panel nicht
            from application.api.site import create_booker, may_link  # pylint: disable=import-outside-toplevel
            user = User.objects(id=ref_id(link._data.get('user'))).first() if link._data.get('user') \
                else User.objects(email=link.email).first()
            if user is None:
                user = create_booker(link.email, link.first_name, link.last_name)
            if not may_link(user):
                flash(f"{link.email} lässt sich nicht mit einer Website verbinden.", 'danger')
                return redirect(url_for('.index'))
            link.update(set__user=user, set__active=True)
            flash(f"{link.email} kann jetzt über {website.name} buchen.", 'success')
        else:
            if link._data.get('user'):
                ApiToken.objects(user=ref_id(link._data.get('user')), website=website.pk).delete()
            link.delete()
            flash(f"Verknüpfung von {link.email} mit {website.name} ist entfernt.", 'info')
        return redirect(url_for('.index'))
