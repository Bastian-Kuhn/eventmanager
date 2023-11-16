"""
Site Config
"""

from application import db

class Config(db.Document): #pylint: disable=too-few-public-methods
    """Style Settings"""

    nav_background_color = db.StringField()
    logo_image = db.ImageField(field="logo_image", collection='logos')
    event_categories = db.ListField(field=db.StringField())

    mail_sender = db.StringField()
    mail_server = db.StringField()
    mail_use_tls = db.BooleanField()
    mail_username = db.StringField()
    mail_subject_prefix = db.StringField()
    mail_password = db.StringField()

    enabled = db.BooleanField()

    meta = {
        "strict" : False,
        "db_alias": "default",
    }
