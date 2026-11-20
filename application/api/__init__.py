"""
JSON-API fuer die Tourentool-App (Mac, iPhone, iPad). Angemeldet wird mit
einem Schluessel aus /app/authorize, nie mit der Browser-Session: so greift
kein CSRF ueber das Cookie. Die Regeln (Plaetze, Warteliste, Rechte, Mails)
kommen aus denselben Funktionen wie die Webseiten.
"""
