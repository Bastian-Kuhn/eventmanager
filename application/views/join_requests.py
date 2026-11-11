"""
Beitrittsanfragen im Admin-Panel: Konten, die sich hier anmelden wollten,
ohne Mitglied zu sein. Annehmen gibt eine Mitgliedschaft (Gast, wenn das
Konto zu einer anderen Sektion gehoert).
"""
from bson import ObjectId
from flask import abort, flash, redirect, request, url_for
from flask_admin import BaseView, expose
from flask_login import current_user

from application.models.section import section_or_none
from application.models.user import User


def join_requesters(section):
    """Konten mit offener Anfrage bei section"""
    # Unbestaetigte Adressen zaehlen nicht, sonst fuellt jeder die Liste mit Fremdadressen
    return User.objects(join_requests__section=section.pk, disabled__ne=True,
                        email_unconfirmed__ne=True).order_by('last_name', 'first_name')


class JoinRequestView(BaseView):
    """Offene Anfragen der Sektion des Requests"""

    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_admin() and section_or_none() is not None

    def inaccessible_callback(self, name, **kwargs):
        abort(404)

    @expose('/')
    def index(self):
        section = section_or_none()
        return self.render('admin/join_requests.html', section=section,
                           requesters=[(user, user.home_section_names(), user.join_request(section))
                                       for user in join_requesters(section)])

    @expose('/antwort', methods=['POST'])
    def answer(self):
        section = section_or_none()
        user_id = request.form.get('user_id') or ''
        user = join_requesters(section).filter(id=user_id).first() if ObjectId.is_valid(user_id) else None
        if user is None:
            abort(404)
        accept = request.form.get('action') == 'accept'
        user.answer_join(section, accept)
        if accept:
            flash(f"{user.email} ist aufgenommen und kann sich jetzt anmelden.", 'success')
        else:
            flash(f"Anfrage von {user.email} abgelehnt.", 'info')
        return redirect(url_for('.index'))
