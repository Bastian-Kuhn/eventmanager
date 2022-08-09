"""
Site Config
"""

from application import db

class Config(db.Document): #pylint: disable=too-few-public-methods
    """Style Settings"""

    nav_background_color = db.StringField()
    logo_image = db.ImageField(collection_name="logos")
    event_categories = db.ListField(db.StringField())
    enabled = db.BooleanField()


    meta = {"strict" : False}
