"""
``flask --app app stripe_setup``: die Stripe-Seite der Abrechnung, gegen den
konfigurierten Key (Test oder Live). Das Konto darf mit Cairn geteilt sein:
gefunden und geaendert wird nur, was dem Eventmanager gehoert.

Was dazugehoert:
- ein Produkt (``metadata.eventmanager=section``)
- ein Monats- und ein Jahrespreis je Einheit (Lookup-Keys
  ``eventmanager_section`` / ``eventmanager_section_yearly``)
- der Webhook ``https://<PORTAL_HOSTNAME>/portal/stripe/webhook``
- eine eigene Kundenportal-Konfiguration (``metadata.eventmanager=1``)

Beliebig oft ausfuehrbar: erst wird gesucht, dann nur Fehlendes angelegt oder
Abweichendes angeglichen. Preise sind bei Stripe unveraenderlich: passt einer
nicht mehr (Betrag, Intervall, archiviert), bekommt ein neuer Preis den
Lookup-Key, der alte bleibt wie er ist (laufende Abos behalten ihn). Nichts
wird archiviert oder geloescht. :func:`plan` liest nur, :func:`apply` schreibt,
was der Plan sagt. Stripe wird hereingereicht, die Tests geben eine Attrappe.
"""
from dataclasses import dataclass, field

CURRENCY = "eur"
# Nettopreise, die Umsatzsteuer kommt dazu (Stripe Tax, STRIPE_AUTOMATIC_TAX)
TAX_BEHAVIOR = "exclusive"
TAX_CODE = "txcd_10103001"  # SaaS, geschaeftliche Nutzung
PRODUCT_NAME = "Tourentool"
PRODUCT_META = {"eventmanager": "section"}
PORTAL_META = {"eventmanager": "1"}
PORTAL_HEADLINE = "Tourentool"
LOOKUP_KEYS = {"month": "eventmanager_section", "year": "eventmanager_section_yearly"}
ENV_NAMES = {"month": "STRIPE_PRICE_SECTION", "year": "STRIPE_PRICE_SECTION_YEARLY"}
WEBHOOK_PATH = "/portal/stripe/webhook"
WEBHOOK_EVENTS = ("checkout.session.completed", "customer.subscription.created",
                  "customer.subscription.updated", "customer.subscription.deleted")


def key_mode(key):
    """``test`` / ``live`` aus dem Praefix des Keys, sonst ''"""
    for prefix in ("sk_", "rk_"):
        if key.startswith(prefix + "test_"):
            return "test"
        if key.startswith(prefix + "live_"):
            return "live"
    return ""


@dataclass
class Step:
    kind: str          # product / price / webhook / portal
    action: str        # reuse / create / move / update
    name: str
    obj_id: str = ""
    detail: str = ""
    params: dict = field(default_factory=dict)
    interval: str = ""


@dataclass
class Plan:
    steps: list
    webhook_secret: str = ""   # nur direkt nach dem Anlegen des Webhooks

    @property
    def changes(self):
        return [s for s in self.steps if s.action != "reuse"]

    def find(self, kind, interval=""):
        return next((s for s in self.steps if s.kind == kind and s.interval == interval), None)


def _get(obj, key, default=None):
    try:
        value = obj[key]
    except (KeyError, TypeError):
        value = getattr(obj, key, default)
    return default if value is None else value


def account_label(stripe):
    """Konto-ID und Anzeigename, damit man sieht, wohin geschrieben wird"""
    try:
        account = stripe.Account.retrieve()
    except Exception as exc:  # pylint: disable=broad-except
        return f"nicht lesbar ({exc.__class__.__name__})"
    name = _get(_get(_get(account, "settings", {}), "dashboard", {}), "display_name", "")
    return f"{_get(account, 'id', '?')} {name}".strip()


def _id(ref):
    return ref if isinstance(ref, str) else _get(ref, "id", "")


def _money(cents, interval):
    return f"{cents / 100:.2f} {CURRENCY.upper()} / {interval}"


def _price_fits(price, cents, interval):
    rec = _get(price, "recurring", {}) or {}
    return (bool(_get(price, "active", False))
            and _get(price, "currency", "") == CURRENCY
            and _get(price, "unit_amount", None) == cents
            and _get(rec, "interval", "") == interval
            and _get(rec, "interval_count", 1) == 1
            and _get(rec, "usage_type", "licensed") == "licensed"
            and _get(price, "tax_behavior", "unspecified") == TAX_BEHAVIOR)


def _describe(price):
    rec = _get(price, "recurring", {}) or {}
    parts = [_money(_get(price, "unit_amount", 0) or 0, _get(rec, "interval", "?"))]
    if _get(price, "currency", CURRENCY) != CURRENCY:
        parts.append(f"Waehrung {_get(price, 'currency')}")
    if _get(price, "tax_behavior", TAX_BEHAVIOR) != TAX_BEHAVIOR:
        parts.append(f"Steuer {_get(price, 'tax_behavior')}")
    if not _get(price, "active", False):
        parts.append("archiviert")
    return ", ".join(parts)


def _differs(want, have, prefix=""):
    """Pfade, an denen ``have`` von ``want`` abweicht (nur die Schluessel aus want)"""
    out = []
    for key, value in want.items():
        path = f"{prefix}{key}"
        current = _get(have, key, None) if have is not None else None
        if isinstance(value, dict):
            out += _differs(value, current, path + ".")
        elif isinstance(value, list):
            if sorted(current or []) != sorted(value):
                out.append(path)
        elif current != value:
            out.append(path)
    return out


def portal_params(portal_url):
    params = {
        "business_profile": {"headline": PORTAL_HEADLINE},
        "features": {
            "customer_update": {"enabled": True,
                                "allowed_updates": ["email", "address", "tax_id"]},
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
            # Die Menge setzt die App, ein Planwechsel koennte auf Cairn-Produkte fuehren
            "subscription_update": {"enabled": False},
        },
    }
    if portal_url:
        params["default_return_url"] = f"{portal_url}/portal/"
    return params


def plan(stripe, portal_hostname, cents):
    """Was angelegt, angeglichen oder wiederverwendet wuerde. Liest nur.
    ``cents``: {'month': 1000, 'year': 10800}"""
    steps = []
    keys = list(LOOKUP_KEYS.values())
    found = {_get(p, "lookup_key", ""): p for p in
             stripe.Price.list(lookup_keys=keys, limit=10).auto_paging_iter()}
    products = [p for p in stripe.Product.list(limit=100, active=True).auto_paging_iter()]
    product = next((p for p in products
                    if _get(_get(p, "metadata", {}), "eventmanager", "") == "section"), None)
    if product is None:
        # Produkt eines eigenen Preises, falls die Metadata fehlt
        own = {_id(_get(p, "product", "")) for p in found.values()}
        product = next((p for p in products if _get(p, "id") in own), None)

    want = {"name": PRODUCT_NAME, "tax_code": TAX_CODE, "metadata": dict(PRODUCT_META)}
    if product is None:
        steps.append(Step("product", "create", PRODUCT_NAME, detail=f"Steuercode {TAX_CODE}",
                          params=want))
    else:
        have = {"name": _get(product, "name", ""), "tax_code": _id(_get(product, "tax_code", "")),
                "metadata": _get(product, "metadata", {}) or {}}
        diff = _differs(want, have)
        if diff:
            steps.append(Step("product", "update", PRODUCT_NAME, _get(product, "id"),
                              "angleichen: " + ", ".join(diff), params=want))
        else:
            steps.append(Step("product", "reuse", PRODUCT_NAME, _get(product, "id")))

    for interval, key in LOOKUP_KEYS.items():
        amount = cents[interval]
        params = {"currency": CURRENCY, "unit_amount": amount,
                  "recurring": {"interval": interval, "usage_type": "licensed"},
                  "tax_behavior": TAX_BEHAVIOR, "lookup_key": key,
                  "nickname": key, "metadata": {"eventmanager": key}}
        have = found.get(key)
        if have is not None and _price_fits(have, amount, interval):
            steps.append(Step("price", "reuse", key, _get(have, "id"),
                              _money(amount, interval), interval=interval))
        elif have is not None:
            params["transfer_lookup_key"] = True
            steps.append(Step("price", "move", key,
                              detail=f"{_money(amount, interval)}, Key liegt auf "
                                     f"{_get(have, 'id')} ({_describe(have)}), der bleibt",
                              params=params, interval=interval))
        else:
            steps.append(Step("price", "create", key, detail=_money(amount, interval),
                              params=params, interval=interval))

    portal_url = f"https://{portal_hostname}" if portal_hostname else ""
    if portal_url:
        webhook_url = portal_url + WEBHOOK_PATH
        hook = next((w for w in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter()
                     if _get(w, "url", "") == webhook_url), None)
        if hook is None:
            steps.append(Step("webhook", "create", webhook_url, params={
                "url": webhook_url, "enabled_events": list(WEBHOOK_EVENTS),
                "description": f"Eventmanager ({portal_hostname})",
                "metadata": {"eventmanager": "1"}}))
        else:
            have = set(_get(hook, "enabled_events", []) or [])
            missing = [e for e in WEBHOOK_EVENTS if e not in have and "*" not in have]
            if missing:
                steps.append(Step("webhook", "update", webhook_url, _get(hook, "id"),
                                  "Events dazu: " + ", ".join(missing),
                                  params={"enabled_events": sorted(have | set(missing))}))
            else:
                steps.append(Step("webhook", "reuse", webhook_url, _get(hook, "id")))

    want = portal_params(portal_url)
    portal = next((c for c in stripe.billing_portal.Configuration.list(limit=100, active=True)
                   .auto_paging_iter()
                   if _get(_get(c, "metadata", {}), "eventmanager", "") == "1"), None)
    if portal is None:
        steps.append(Step("portal", "create", "Kundenportal", detail=PORTAL_HEADLINE,
                          params={**want, "metadata": dict(PORTAL_META)}))
    else:
        diff = _differs(want, portal)
        if diff:
            steps.append(Step("portal", "update", "Kundenportal", _get(portal, "id"),
                              "angleichen: " + ", ".join(diff), params=want))
        else:
            steps.append(Step("portal", "reuse", "Kundenportal", _get(portal, "id")))
    return Plan(steps)


def apply(stripe, the_plan):
    """Schreibt, was der Plan sagt, Produkt vor den Preisen. Traegt die IDs
    angelegter Objekte in die Schritte ein."""
    product_id = ""
    for s in the_plan.steps:
        if s.kind == "product" and s.action == "reuse":
            product_id = s.obj_id
        elif s.kind == "product" and s.action == "update":
            stripe.Product.modify(s.obj_id, **s.params)
            product_id = s.obj_id
        elif s.kind == "product":
            s.obj_id = product_id = _get(stripe.Product.create(**s.params), "id")
        elif s.action == "reuse":
            continue
        elif s.kind == "price":
            s.obj_id = _get(stripe.Price.create(product=product_id, **s.params), "id")
        elif s.kind == "webhook" and s.action == "create":
            hook = stripe.WebhookEndpoint.create(**s.params)
            s.obj_id = _get(hook, "id")
            the_plan.webhook_secret = _get(hook, "secret", "") or ""
        elif s.kind == "webhook":
            stripe.WebhookEndpoint.modify(s.obj_id, **s.params)
        elif s.kind == "portal" and s.action == "update":
            stripe.billing_portal.Configuration.modify(s.obj_id, **s.params)
        elif s.kind == "portal":
            s.obj_id = _get(stripe.billing_portal.Configuration.create(**s.params), "id")
    return the_plan


def env_lines(the_plan):
    """Zeilen fuer die .env der Instanz, nie der API-Key"""
    lines = []
    for interval, name in ENV_NAMES.items():
        step = the_plan.find("price", interval)
        lines.append(f"{name}={step.obj_id or '<legt --apply an>'}")
    if the_plan.webhook_secret:
        lines.append(f"STRIPE_WEBHOOK_SECRET={the_plan.webhook_secret}")
    portal = the_plan.find("portal")
    lines.append(f"STRIPE_PORTAL_CONFIGURATION={portal.obj_id or '<legt --apply an>'}")
    return lines
