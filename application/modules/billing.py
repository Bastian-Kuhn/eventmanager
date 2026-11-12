"""
Abrechnung der Sektionen ueber Stripe, angelehnt an Cairn.

Eine Sektion hat hoechstens ein Abo, monatlich (STRIPE_PRICE_SECTION,
10 Euro) oder jaehrlich (STRIPE_PRICE_SECTION_YEARLY, 108 Euro). Menge = angefangene MEMBERS_PER_UNIT Mitglieder laut
Angabe der Sektion. Den Status schreibt nur der Webhook (apply_subscription),
Checkout und Kundenportal laufen bei Stripe.

Alles ist aus, solange SUBSCRIPTIONS_ENABLED nicht gesetzt ist, und braucht
dann zusaetzlich STRIPE_SECRET_KEY.
"""
import logging
import math
from datetime import datetime, timezone

from flask import current_app, url_for

from application.models.section import Section

log = logging.getLogger(__name__)


class BillingError(RuntimeError):
    """Abrechnung geht gerade nicht (nicht eingerichtet oder Stripe lehnt ab)"""


def billing_enabled():
    config = current_app.config
    return bool(config.get('SUBSCRIPTIONS_ENABLED') and config.get('STRIPE_SECRET_KEY'))


def _stripe():
    if not billing_enabled():
        raise BillingError("Die Abrechnung ist hier nicht eingerichtet.")
    import stripe  # pylint: disable=import-outside-toplevel
    stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
    return stripe


def units(section):
    """Abgerechnete Einheiten: angefangene 250 Mitglieder, mindestens eine"""
    per_unit = current_app.config.get('MEMBERS_PER_UNIT', 250)
    return max(1, math.ceil((section.member_count or 1) / per_unit))


def monthly_price(section):
    return units(section) * current_app.config.get('PRICE_PER_UNIT_EUR', 10)


def yearly_price(section):
    return units(section) * current_app.config.get('PRICE_PER_UNIT_YEARLY_EUR', 108)


def yearly_available():
    return bool(current_app.config.get('STRIPE_PRICE_SECTION_YEARLY'))


def _metadata(section):
    return {'section_id': str(section.pk), 'section_slug': section.slug}


def create_checkout(section, admin, interval='month'):
    """Stripe Checkout fuer das Abo der Sektion, gibt die URL zurueck"""
    stripe = _stripe()
    key = 'STRIPE_PRICE_SECTION_YEARLY' if interval == 'year' else 'STRIPE_PRICE_SECTION'
    price = current_app.config.get(key)
    if not price:
        raise BillingError("Kein Preis fuer Sektionen eingerichtet.")
    kwargs = {'customer': section.stripe_customer_id} if section.stripe_customer_id \
        else {'customer_email': admin.email}
    kwargs.update(_checkout_tax_params(bool(section.stripe_customer_id)))
    session = stripe.checkout.Session.create(
        mode='subscription',
        client_reference_id=str(section.pk),
        line_items=[{'price': price, 'quantity': units(section)}],
        success_url=url_for('PORTAL.billing', slug=section.slug, _external=True),
        cancel_url=url_for('PORTAL.billing', slug=section.slug, _external=True),
        metadata=_metadata(section),
        subscription_data={'metadata': _metadata(section)},
        **kwargs,
    )
    return session['url']


def _checkout_tax_params(existing_customer):
    """
    Was Checkout fuer die Rechnung abfragt: Rechnungsadresse und optional die
    USt-IdNr. Bei einem schon vorhandenen Kunden schreibt customer_update beides
    dorthin (verlangt Stripe dann). Stripe Tax nur mit STRIPE_AUTOMATIC_TAX:
    Stripe lehnt es ab, solange im Konto Steuersitz und Registrierungen fehlen.
    Nur hier: das Abo behaelt automatic_tax, sync_quantity braucht nichts.
    """
    params = {
        'billing_address_collection': 'required',
        'tax_id_collection': {'enabled': True},
    }
    if existing_customer:
        params['customer_update'] = {'address': 'auto', 'name': 'auto'}
    if current_app.config.get('STRIPE_AUTOMATIC_TAX'):
        params['automatic_tax'] = {'enabled': True}
    return params


def list_invoices(section, limit=12):
    """
    Letzte Rechnungen der Sektion bei Stripe, neueste zuerst. [] ohne
    Abrechnung oder Kunde, None wenn Stripe nicht antwortet (die Seite sagt
    das dann und laeuft weiter).
    """
    if not billing_enabled() or not section.stripe_customer_id:
        return []
    try:
        rows = list(_stripe().Invoice.list(customer=section.stripe_customer_id, limit=limit)['data'])
    except Exception as exc:  # pylint: disable=broad-except
        log.warning("billing: Rechnungen fuer %s nicht geladen: %s", section.slug, exc)
        return None
    invoices = []
    for invoice in rows:
        if invoice.get('status') == 'draft':
            continue  # noch nicht ausgestellt: keine Nummer, kein PDF
        invoices.append({
            'number': invoice.get('number') or '',
            'date': _to_datetime(invoice.get('created')),
            'amount': (invoice.get('total') or 0) / 100,
            'currency': (invoice.get('currency') or 'eur').upper(),
            'status': INVOICE_STATUS.get(invoice.get('status'), invoice.get('status') or ''),
            'url': invoice.get('hosted_invoice_url') or '',
            'pdf': invoice.get('invoice_pdf') or '',
        })
    return invoices


INVOICE_STATUS = {'paid': 'Bezahlt', 'open': 'Offen', 'void': 'Storniert',
                  'uncollectible': 'Uneinbringlich'}


def create_portal(section):
    """Stripe-Kundenportal (Zahlungsdaten, Rechnungen, Kuendigung)"""
    stripe = _stripe()
    if not section.stripe_customer_id:
        raise BillingError("Es gibt noch kein Abo.")
    kwargs = {}
    if current_app.config.get('STRIPE_PORTAL_CONFIGURATION'):
        kwargs['configuration'] = current_app.config['STRIPE_PORTAL_CONFIGURATION']
    session = stripe.billing_portal.Session.create(
        customer=section.stripe_customer_id,
        return_url=url_for('PORTAL.billing', slug=section.slug, _external=True),
        **kwargs,
    )
    return session['url']


def sync_quantity(section):
    """Menge im Abo an die Mitgliederzahl anpassen, ohne Abo nichts"""
    if not (section.subscription_id and section.is_paid and billing_enabled()):
        return
    stripe = _stripe()
    subscription = stripe.Subscription.retrieve(section.subscription_id)
    item = subscription['items']['data'][0]
    if int(item.get('quantity') or 0) != units(section):
        stripe.Subscription.modify(section.subscription_id,
                                   items=[{'id': item['id'], 'quantity': units(section)}])


def refresh_subscription(section):
    """
    Abo-Stand direkt bei Stripe nachfragen, etwa vor dem Loeschen. Kennt Stripe
    das Abo nicht (z.B. aus dem Testmodus, jetzt mit Live-Key), gilt es als
    beendet. Antwortet Stripe nicht, bleibt alles wie es ist.
    """
    if not (section.subscription_id and billing_enabled()):
        return
    stripe = _stripe()
    try:
        subscription = stripe.Subscription.retrieve(section.subscription_id)
    except stripe.InvalidRequestError as exc:
        if getattr(exc, 'code', None) == 'resource_missing':
            log.warning("billing: Abo %s von %s unbekannt bei Stripe, gilt als beendet",
                        section.subscription_id, section.slug)
            Section.objects(pk=section.pk).update_one(set__subscription_status='canceled')
        return
    except Exception:  # pylint: disable=broad-except
        log.exception("billing: Abo %s nicht abrufbar", section.subscription_id)
        return
    apply_subscription(subscription, deleted=subscription.get('status') == 'canceled')


def verify_webhook(payload, signature):
    stripe = _stripe()
    secret = current_app.config.get('STRIPE_WEBHOOK_SECRET')
    if not secret:
        raise BillingError("STRIPE_WEBHOOK_SECRET fehlt.")
    try:
        return stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:  # pylint: disable=broad-except
        raise BillingError(f"Ungueltiger Webhook: {exc}") from exc


def _section_for(obj):
    section_id = (obj.get('metadata') or {}).get('section_id') or obj.get('client_reference_id')
    section = Section.objects(pk=section_id).first() if section_id else None
    if section is None and obj.get('customer'):
        section = Section.objects(stripe_customer_id=obj['customer']).first()
    return section


def _to_datetime(epoch):
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(tzinfo=None)


def apply_subscription(subscription, deleted=False):
    """Stand eines Abos aus Stripe an die Sektion schreiben"""
    section = _section_for(subscription)
    if section is None:
        log.warning("billing: Abo %s ohne Sektion", subscription.get('id'))
        return None
    current, incoming = section.subscription_id or '', subscription.get('id') or ''
    # Stripe garantiert keine Reihenfolge: eine spaete Meldung zu einem
    # ersetzten Abo darf das aktuelle nicht ueberschreiben
    if current and incoming and current != incoming \
            and (deleted or subscription.get('status') not in ('active', 'trialing')):
        return section
    section.subscription_id = incoming
    section.subscription_status = 'canceled' if deleted else (subscription.get('status') or '')
    section.current_period_end = _to_datetime(subscription.get('current_period_end'))
    section.cancel_at_period_end = bool(subscription.get('cancel_at_period_end'))
    items = (subscription.get('items') or {}).get('data') or []
    recurring = ((items[0].get('price') or {}).get('recurring') or {}) if items else {}
    if recurring.get('interval'):
        section.billing_interval = recurring['interval']
    if subscription.get('customer'):
        section.stripe_customer_id = subscription['customer']
    section.save()
    return section


def handle_event(event):
    kind = event['type']
    obj = event['data']['object']
    if kind in ('customer.subscription.created', 'customer.subscription.updated',
                'customer.subscription.deleted'):
        apply_subscription(obj, deleted=kind.endswith('deleted'))
    elif kind == 'checkout.session.completed':
        section = _section_for(obj)
        if section and obj.get('customer') and not section.stripe_customer_id:
            section.stripe_customer_id = obj['customer']
            section.save()
        if section and obj.get('subscription'):
            apply_subscription(_stripe().Subscription.retrieve(obj['subscription']))
