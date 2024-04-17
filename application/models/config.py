"""
Site Config
"""

from application import db


class Category(db.EmbeddedDocument):
    """
    Category
    """
    name = db.StringField()
    color = db.StringField()

class Config(db.Document): #pylint: disable=too-few-public-methods
    """Style Settings"""

    nav_background_color = db.StringField()
    homepage_link = db.StringField()
    logo_image = db.ImageField(field="logo_image", collection='logos')
    event_categories = db.ListField(field=db.StringField())
    event_categories_full = db.ListField(field=db.EmbeddedDocumentField(document_type="Category"))

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
