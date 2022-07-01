"""
Events
"""
from application import db

class Event(db.Document):
    event_name = db.StringField()
    event_description = db.StringField()
