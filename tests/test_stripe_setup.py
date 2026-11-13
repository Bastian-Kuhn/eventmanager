"""
flask stripe_setup: Produkt, Preise, Webhook und Kundenportal des
Eventmanagers, gegen ein Stripe-Konto im Speicher, in dem auch Cairn liegt.
Ausfuehren: .venv/bin/python -m unittest tests.test_stripe_setup
"""
import itertools
import sys
import unittest
from unittest import mock

from application import app
from application.modules import stripe_setup

HOST = "portal.example.com"
CENTS = {"month": 1000, "year": 10800}


class _Obj(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _List:
    def __init__(self, items):
        self.items = list(items)

    def auto_paging_iter(self):
        return iter(self.items)


def _merge(obj, kw):
    for key, value in kw.items():
        obj[key] = dict(obj.get(key) or {}, **value) if isinstance(value, dict) else value


class FakeStripe:
    """Konto mit Cairns Produkt, Preis, Webhook und Portal schon darin"""

    def __init__(self):
        self.writes = []
        self.api_key = ""
        ids = itertools.count(1)
        fake = self

        def new_id(prefix):
            return f"{prefix}_{next(ids)}"

        self.products = [_Obj(id="prod_cairn", active=True, name="Cairn Premium",
                              tax_code="txcd_10103000", metadata={"cairn_role": "premium"})]
        self.prices = [_Obj(id="price_cairn", product="prod_cairn", unit_amount=300,
                            currency="eur", active=True, lookup_key="cairn_premium",
                            tax_behavior="inclusive",
                            recurring=_Obj(interval="month", interval_count=1,
                                           usage_type="licensed"))]
        self.hooks = [_Obj(id="we_cairn", url="https://cairn.example.com/billing/webhook",
                           enabled_events=["checkout.session.completed"])]
        self.portals = [_Obj(id="bpc_cairn", active=True, metadata={"cairn": "1"},
                             business_profile={"headline": "Cairn subscription"})]
        foreign = {"prod_cairn", "price_cairn", "we_cairn", "bpc_cairn"}
        self.foreign = foreign

        def find(items, obj_id):
            if obj_id in foreign:
                raise AssertionError(f"fremdes Objekt angefasst: {obj_id}")
            return next(i for i in items if i["id"] == obj_id)

        class Account:
            @staticmethod
            def retrieve():
                return _Obj(id="acct_1", settings={"dashboard": {"display_name": "Beispiel"}})

        class Product:
            @staticmethod
            def list(**kw):
                return _List(p for p in fake.products if p["active"] or not kw.get("active"))

            @staticmethod
            def create(**kw):
                fake.writes.append(("product.create", kw))
                p = _Obj(id=new_id("prod"), active=True, **kw)
                fake.products.append(p)
                return p

            @staticmethod
            def modify(obj_id, **kw):
                fake.writes.append(("product.modify", kw))
                _merge(find(fake.products, obj_id), kw)

        class Price:
            @staticmethod
            def list(lookup_keys=(), **kw):
                return _List(p for p in fake.prices if p["lookup_key"] in lookup_keys)

            @staticmethod
            def create(**kw):
                fake.writes.append(("price.create", kw))
                taken = [p for p in fake.prices if p["lookup_key"] == kw["lookup_key"]]
                if taken and not kw.get("transfer_lookup_key"):
                    raise ValueError("lookup key taken")
                for p in taken:
                    p["lookup_key"] = None
                p = _Obj(id=new_id("price"), active=True,
                         **{k: v for k, v in kw.items() if k != "transfer_lookup_key"})
                p["recurring"] = dict(p["recurring"], interval_count=1)
                fake.prices.append(p)
                return p

        class WebhookEndpoint:
            @staticmethod
            def list(**kw):
                return _List(fake.hooks)

            @staticmethod
            def create(**kw):
                fake.writes.append(("webhook.create", kw))
                h = _Obj(id=new_id("we"), secret="whsec_fresh", **kw)
                fake.hooks.append(h)
                return h

            @staticmethod
            def modify(obj_id, **kw):
                fake.writes.append(("webhook.modify", kw))
                _merge(find(fake.hooks, obj_id), kw)

        class Configuration:
            @staticmethod
            def list(**kw):
                return _List(c for c in fake.portals if c["active"] or not kw.get("active"))

            @staticmethod
            def create(**kw):
                fake.writes.append(("portal.create", kw))
                c = _Obj(id=new_id("bpc"), active=True, **kw)
                fake.portals.append(c)
                return c

            @staticmethod
            def modify(obj_id, **kw):
                fake.writes.append(("portal.modify", kw))
                _merge(find(fake.portals, obj_id), kw)

        self.Account, self.Product, self.Price = Account, Product, Price
        self.WebhookEndpoint = WebhookEndpoint
        self.billing_portal = _Obj(Configuration=Configuration)

    def own(self, items):
        return [i for i in items if i["id"] not in self.foreign]


def _run(fake, do_apply=True, host=HOST, cents=None):
    the_plan = stripe_setup.plan(fake, host, cents or CENTS)
    if do_apply:
        stripe_setup.apply(fake, the_plan)
    return the_plan


class SetupTest(unittest.TestCase):

    def test_dry_run_writes_nothing(self):
        fake = FakeStripe()
        the_plan = _run(fake, do_apply=False)
        self.assertEqual(fake.writes, [])
        self.assertEqual([(s.kind, s.action) for s in the_plan.steps],
                         [("product", "create"), ("price", "create"), ("price", "create"),
                          ("webhook", "create"), ("portal", "create")])

    def test_apply_creates_own_objects_only(self):
        fake = FakeStripe()
        the_plan = _run(fake)
        product = fake.own(fake.products)[0]
        self.assertEqual(product["name"], "Tourentool")
        self.assertEqual(product["metadata"], {"eventmanager": "section"})
        prices = {p["lookup_key"]: p for p in fake.own(fake.prices)}
        self.assertEqual(prices["eventmanager_section"]["unit_amount"], 1000)
        self.assertEqual(prices["eventmanager_section_yearly"]["unit_amount"], 10800)
        self.assertEqual(prices["eventmanager_section_yearly"]["recurring"]["interval"], "year")
        self.assertEqual({p["tax_behavior"] for p in prices.values()}, {"exclusive"})
        self.assertEqual({p["product"] for p in prices.values()}, {product["id"]})
        hook = fake.own(fake.hooks)[0]
        self.assertEqual(hook["url"], f"https://{HOST}/portal/stripe/webhook")
        portal = fake.own(fake.portals)[0]
        self.assertEqual(portal["business_profile"]["headline"], "Tourentool")
        self.assertFalse(portal["features"]["subscription_update"]["enabled"])
        self.assertEqual(portal["default_return_url"], f"https://{HOST}/portal/")
        self.assertEqual(stripe_setup.env_lines(the_plan), [
            f"STRIPE_PRICE_SECTION={prices['eventmanager_section']['id']}",
            f"STRIPE_PRICE_SECTION_YEARLY={prices['eventmanager_section_yearly']['id']}",
            "STRIPE_WEBHOOK_SECRET=whsec_fresh",
            f"STRIPE_PORTAL_CONFIGURATION={portal['id']}",
        ])
        # Cairn ist unveraendert
        self.assertEqual(fake.prices[0]["lookup_key"], "cairn_premium")
        self.assertEqual(fake.portals[0]["business_profile"]["headline"], "Cairn subscription")

    def test_second_run_changes_nothing(self):
        fake = FakeStripe()
        first = _run(fake)
        writes = len(fake.writes)
        second = _run(fake)
        self.assertEqual(second.changes, [])
        self.assertEqual(len(fake.writes), writes)
        self.assertEqual([l for l in stripe_setup.env_lines(first) if "SECRET" not in l],
                         stripe_setup.env_lines(second))

    def test_price_change_moves_the_lookup_key(self):
        fake = FakeStripe()
        _run(fake)
        old = next(p for p in fake.prices if p["lookup_key"] == "eventmanager_section")
        the_plan = _run(fake, cents={"month": 1200, "year": 10800})
        self.assertEqual([(s.kind, s.action) for s in the_plan.changes], [("price", "move")])
        new = next(p for p in fake.prices if p["lookup_key"] == "eventmanager_section")
        self.assertEqual(new["unit_amount"], 1200)
        self.assertTrue(old["active"])  # bleibt fuer laufende Abos
        self.assertIsNone(old["lookup_key"])

    def test_drift_is_set_back(self):
        fake = FakeStripe()
        _run(fake)
        portal = fake.own(fake.portals)[0]
        portal["features"]["subscription_update"] = {"enabled": True}
        fake.own(fake.products)[0]["name"] = "Umbenannt"
        fake.own(fake.hooks)[0]["enabled_events"] = ["checkout.session.completed"]
        the_plan = _run(fake)
        self.assertEqual([(s.kind, s.action) for s in the_plan.changes],
                         [("product", "update"), ("webhook", "update"), ("portal", "update")])
        self.assertEqual(_run(fake, do_apply=False).changes, [])

    def test_without_portal_hostname_no_webhook(self):
        fake = FakeStripe()
        the_plan = _run(fake, host="")
        self.assertIsNone(the_plan.find("webhook"))
        self.assertNotIn("default_return_url", fake.own(fake.portals)[0])

    def test_key_mode(self):
        self.assertEqual(stripe_setup.key_mode("sk_test_abc"), "test")
        self.assertEqual(stripe_setup.key_mode("rk_live_abc"), "live")
        self.assertEqual(stripe_setup.key_mode("pk_live_abc"), "")


class CommandTest(unittest.TestCase):

    def _invoke(self, key, *args):
        fake = FakeStripe()
        with mock.patch.dict(app.config, STRIPE_SECRET_KEY=key, PORTAL_HOSTNAME=HOST), \
                mock.patch.dict(sys.modules, {"stripe": fake}):
            result = app.test_cli_runner().invoke(args=["stripe_setup", *args])
        return result, fake

    def test_live_apply_needs_live(self):
        result, fake = self._invoke("sk_live_geheim", "--apply")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--live", result.output)
        self.assertEqual(fake.writes, [])
        self.assertNotIn("geheim", result.output)

    def test_live_dry_run_reads_only(self):
        result, fake = self._invoke("sk_live_geheim")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("LIVE-Modus", result.output)
        self.assertIn("acct_1 Beispiel", result.output)
        self.assertEqual(fake.writes, [])

    def test_apply_prints_env_lines(self):
        result, fake = self._invoke("sk_test_geheim", "--apply")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("STRIPE_PRICE_SECTION=price_", result.output)
        self.assertIn("STRIPE_WEBHOOK_SECRET=whsec_fresh", result.output)
        self.assertNotIn("geheim", result.output)
        self.assertEqual(len(fake.own(fake.prices)), 2)


if __name__ == "__main__":
    unittest.main()
