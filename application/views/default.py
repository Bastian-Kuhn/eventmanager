"""
Models for flask_admin
"""
#pylint: disable=no-member
from flask_admin import AdminIndexView
from flask_admin.contrib.mongoengine import ModelView

from flask_login import current_user
from flask import redirect, url_for, flash


class IndexView(AdminIndexView):
    """
    Index View Overwrite for auth
    """
    def is_visible(self):
        return False

    def is_accessible(self):
        return current_user.is_authenticated \
                and not current_user.force_password_change

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for('auth.login', next='/admin'))

class CustomModelView(ModelView):
    """ Custom Model View """

    def is_accessible(self):
        return current_user.is_authenticated

    def inaccessible_callback(self, name, **kwargs):
        flash("You don't have the rights for the Module")
        return redirect('/admin')
