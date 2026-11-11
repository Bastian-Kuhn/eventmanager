"""
Impressum und Datenschutzerklaerung des Betreibers, auf jedem Host gleich
(Portal und alle Sektionen)
"""
from flask import Blueprint, render_template

from application.modules.billing import billing_enabled

LEGAL = Blueprint('LEGAL', __name__)


@LEGAL.route('/impressum')
def imprint():
    return render_template('legal_impressum.html')


@LEGAL.route('/datenschutz')
def privacy():
    return render_template('legal_datenschutz.html', billing=billing_enabled())
