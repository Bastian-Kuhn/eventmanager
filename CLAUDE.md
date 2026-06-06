# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Grundsätze

1. Don't assume. Don't hide confusion. Surface tradeoffs.
2. Minimum code that solves the problem. Nothing speculative.
3. Touch only what you must. Clean up only your own mess.
4. Define success criteria. Loop until verified.
5. Simple is better than Complex, Readability counts.
6. Jede GUI-Seite (gebaut, bearbeitet oder nur angesehen) muss barrierefrei & mobiltauglich sein: Semantik, ARIA-Labels für Icon-Buttons, Label-Verknüpfung, Tastatur/Fokus, Kontraste, responsive.

## Projekt-Notizen

- Stack wie `../cmdbsyncer`: Flask + MongoEngine + Flask-Admin, uWSGI in prod, `./helper` wrapt docker compose.
- UI-Sprache ist Deutsch — neue Strings auf Deutsch halten.
- Erster Admin: `./helper create_user <email>` (druckt das generierte Passwort).
- Config-Auswahl per `config` env var (`compose` setzen die docker-compose Files). Ohne uWSGI läuft die App standalone mit `BaseConfig`.
- Laufzeit-SMTP/Logo/Nav-Farbe kommen aus dem `Config`-Singleton-Doc (`enabled=True`); `@app.before_request` kopiert das in `app.config`. Ohne so ein Doc sendet `send_email()` stillschweigend nichts.
- Event-Daten sind stark denormalisiert: `Event.tickets` sind Ticket-*Definitionen*, gebuchte Tickets liegen als `OwnedTicket` eingebettet in `EventParticipation` in `Event.participations`. `User.event_registrations` ist eine manuell gepflegte Rückreferenz — beim Hinzufügen/Entfernen von Tickets beide Seiten konsistent halten. Helper auf `Event` (`get_booked_ticket`, `delete_ticket`, `get_ticket_stats`, …) statt eigener Traversal nutzen.
- Rechte immer mit `current_user.has_right('guide')` prüfen (deckt `attendant` und global admins mit ab), nicht direkt `role ==`.
- `meta = {'strict': False}` auf den meisten Documents — Tippfehler im Attributnamen werden beim Save still geschluckt.
