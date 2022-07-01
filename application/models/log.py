"""
Log Entry
"""

from application import db

class LogEntry(db.Document): #pylint: disable=too-few-public-methods
    """Log Entry"""

    datetime = db.DateTimeField()
    type = db.StringField()
    url = db.StringField()
    user_id = db.StringField()
    request_id = db.StringField()
    message = db.StringField()
    raw = db.StringField()
    source = db.StringField()
    traceback = db.StringField()


    meta = {"strict" : False,
            "indexes": [
                {'fields': ['datetime'],
                 'expireAfterSeconds': 2592000
                }
            ]
           }
