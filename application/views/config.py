"""
Config Model View
"""
from application.views.default import CustomModelView
from flask_login import current_user


class ConfigModelView(CustomModelView): #pylint: disable=too-few-public-methods
    """
    Style Model
    """

    can_edit = True
    can_delete = True
    can_create = True


    def is_accessible(self): #pylint: disable=no-self-use
        """ Overwrite """
        return current_user.is_authenticated and current_user.is_admin()
