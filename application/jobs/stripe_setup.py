#!/usr/bin/env python3
"""
Stripe fuer die Abrechnung der Sektionen einrichten, siehe
application/modules/stripe_setup.py
"""
import click

from application import app
from application.modules import stripe_setup as setup


def _row(step):
    return (f"  {step.action:<7} {step.kind:<8} {step.name:<46} {step.obj_id or ''}"
            + (f"  {step.detail}" if step.detail else ""))


@app.cli.command('stripe_setup')
@click.option('--apply', 'do_apply', is_flag=True,
              help="Schreibt, was der Plan sagt (sonst Probelauf, nichts wird geschrieben).")
@click.option('--live', is_flag=True, help="Zusaetzlich zu --apply noetig bei einem Live-Key.")
def stripe_setup_cmd(do_apply, live):
    """Produkt, Preise, Webhook und Kundenportal des Eventmanagers in Stripe.

    Beliebig oft ausfuehrbar, fasst nur eigene Objekte an (Lookup-Keys und
    Metadata "eventmanager"), nie die von Cairn im selben Konto. Probelauf
    ohne --apply, ein Live-Key braucht zusaetzlich --live. Am Ende stehen die
    Zeilen fuer die .env, nie der API-Key.
    """
    cfg = app.config
    key = cfg.get('STRIPE_SECRET_KEY') or ''
    if not key:
        raise click.ClickException("STRIPE_SECRET_KEY ist leer.")
    mode = setup.key_mode(key)
    banner = {'test': "TEST-Modus (Sandbox, kein echtes Geld)",
              'live': "LIVE-Modus (echte Kunden, echtes Geld)"}.get(
                  mode, "UNBEKANNTER Modus (Key ist weder sk_test_ noch sk_live_)")
    import stripe  # pylint: disable=import-outside-toplevel
    stripe.api_key = key
    click.echo("=" * 72)
    click.echo(f"  Stripe: {banner}")
    click.echo(f"  Konto:  {setup.account_label(stripe)}")
    click.echo("=" * 72)
    if do_apply and not mode:
        raise click.ClickException("Mit einem Key unbekannten Modus wird nichts geschrieben.")
    if do_apply and mode == 'live' and not live:
        raise click.ClickException("Das ist ein LIVE-Key: --apply --live, um live zu schreiben.")

    hostname = cfg.get('PORTAL_HOSTNAME') or ''
    cents = {'month': round(cfg['PRICE_PER_UNIT_EUR'] * 100),
             'year': round(cfg['PRICE_PER_UNIT_YEARLY_EUR'] * 100)}
    the_plan = setup.plan(stripe, hostname, cents)
    if not hostname:
        click.echo("PORTAL_HOSTNAME ist leer: kein Webhook, Portal ohne Ruecksprung-URL.")
    if not do_apply:
        click.echo("Probelauf: nichts wird geschrieben. --apply macht die Aenderungen unten.")
    click.echo("")
    for step in the_plan.steps:
        click.echo(_row(step))
    click.echo("")
    changes = the_plan.changes
    if not changes:
        click.echo("Nichts zu tun: alles ist da und passt.")
    elif do_apply:
        setup.apply(stripe, the_plan)
        click.echo(f"Fertig: {len(changes)} Aenderung(en).")
        for step in changes:
            click.echo(_row(step))
    else:
        click.echo(f"{len(changes)} Aenderung(en) wuerden gemacht.")

    click.echo("")
    click.echo("Zeilen fuer die .env der Instanz:")
    for line in setup.env_lines(the_plan):
        click.echo(line)
    if the_plan.webhook_secret:
        click.echo("")
        click.echo("Das Webhook-Secret oben zeigt Stripe nur EINMAL, nur hier. Es gehoert "
                   "in die .env und nirgends sonst hin.")
    elif any(s.kind == 'webhook' for s in the_plan.steps):
        click.echo("(Webhook gab es schon: STRIPE_WEBHOOK_SECRET bleibt wie es ist. Fehlt es, "
                   "im Stripe-Dashboard beim Endpoint das Secret neu erzeugen.)")
    if mode == 'live':
        click.echo("SUBSCRIPTIONS_ENABLED=1 und STRIPE_SECRET_KEY (live) muessen ebenfalls in der .env stehen.")
    click.echo("Danach die Instanz neu erstellen, damit sie die .env neu liest.")
