# -*- coding: utf-8 -*-
""" LOGGING Module"""
from flask import request
import re
import traceback

class Logging():
    """
    General Logging Module
    """

    def __init__(self, log_func=None):
        """
        Init and Set Config
        """
        self.log_func = log_func

    def log(self, message, user_id=False, log_type="debug", url=None, raw=False):
        """ LOG Messages with Level DEBUG TO SYSLOG"""


        request_id = ""
        if request:
            request_id = request.environ.get("HTTP_X_REQUEST_ID")

        self.log_func({'message' : message,
                       'user_id': user_id,
                       'type': log_type,
                       'url': url,
                       'request_id': request_id,
                       'traceback': traceback.format_exc(),
                       'raw': raw})

    def debug(self, message):
        """Just print it out"""
        print(message)
