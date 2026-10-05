from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qsl

from freezegun import freeze_time
from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

from .. import const

LOGGER = "odoo.addons.payment_pagos360.models.payment_transaction"


@tagged("post_install", "-at_install")
class TestCollectionControl(TransactionCase):
    """The daily control resolves, the day after, what Pagos360 resolved and the webhook did
    not tell Odoo, checking one by one only what Odoo has not resolved yet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Midday in Argentina, so the control day is plainly yesterday.
        cls.now = fields.Datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
        cls.startClassPatcher(freeze_time(cls.now))
        cls.provider = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.provider.write({"state": "test"})
        cls.day = cls.provider._pagos360_get_control_date()
        cls.partner = cls.env["res.partner"].create({"name": "Test Buyer"})
        cls.Transaction = cls.env["payment.transaction"]
        cls.token = cls.env.ref("payment_pagos360.pagos360_tests_token")

    def _make_tx(self, operation="online_redirect", state="pending", created_on=None, **values):
        tx = self.Transaction.create(
            {
                "provider_id": self.provider.id,
                "payment_method_id": self.env.ref("payment_pagos360.payment_method_pagos360").id,
                "operation": operation,
                "amount": 0.0 if operation == "validation" else 1000.0,
                "currency_id": self.env.company.currency_id.id,
                "partner_id": self.partner.id,
                **values,
            }
        )
        {"pending": tx._set_pending, "done": tx._set_done, "cancel": tx._set_canceled, "draft": lambda: None}[state]()
        if created_on:
            # create_date comes from the database clock, which freeze_time does not reach. Midday in Argentina.
            tx.flush_recordset()
            created = fields.Datetime.to_datetime(created_on) + timedelta(hours=15)
            self.env.cr.execute("UPDATE payment_transaction SET create_date = %s WHERE id = %s", (created, tx.id))
            tx.invalidate_recordset(["create_date"])
        return tx

    def _run_control(self, reports=None, adhesions=(), entities=None, fail_checks=False):
        """Run the control against a fake Pagos360 API.

        :param dict entities: ``{api path: [entity, ...]}`` answered to the individual checks.
        :return: The summary and the transactions checked one by one.
        """
        reports, entities, checked = reports or {}, entities or {}, []

        def make_request(provider, endpoint, data=None, method="POST"):
            path, __, query = endpoint.lstrip("/").partition("?")
            params = dict(parse_qsl(query))
            if path.startswith("report/"):
                return {"data": reports.get(path.split("/")[1], [])}
            if params.get("state") == "signed":
                rows = [row for kind, row in adhesions if kind == path]
                return {"data": rows, "items_per_page": 20, "total_count": len(rows)}
            if method == "POST":  # the child charge of a signed adhesion
                return {"id": 990001, "state": "pending"}
            if fail_checks:
                raise ValidationError("Pagos360: Could not establish the connection to the API.")
            kind, __, entity_id = path.partition("/")
            found = [
                entity
                for entity in entities.get(kind, [])
                if str(entity["id"]) in (entity_id, params.get("id"))
                or entity["external_reference"] == params.get("external_reference")
            ]
            if entity_id:
                return found[0] if found else {}
            return {"data": found}

        fetch = type(self.Transaction)._pagos360_fetch_and_process_transaction
        with (
            patch.object(type(self.provider), "_pagos360_make_request", make_request),
            patch.object(
                type(self.Transaction),
                "_pagos360_fetch_and_process_transaction",
                lambda tx: checked.append(tx) or fetch(tx),
            ),
            # The control commits per transaction, which a test cursor refuses.
            patch.object(self.env.cr, "commit", lambda: None),
            patch.object(self.env.cr, "rollback", lambda: None),
        ):
            summary = self.Transaction._pagos360_collection_control(self.provider)
        return summary, checked

    def _paid(self, entity_id, reference):
        paid_at = "%sT10:00:00-03:00" % self.day
        return {
            "id": entity_id,
            "state": "paid",
            "external_reference": reference,
            "request_result": [{"amount": 1000.0, "paid_at": paid_at}],
        }

    @mute_logger(LOGGER)
    def test_control_bulk(self):
        """Step 1. Red if the match ignores the state (`already_done` gets checked) or skips the debit id."""
        with patch.object(type(self.provider), "_pagos360_make_request") as make_request:
            self.Transaction._cron_pagos360_collection_control()
        make_request.assert_not_called()  # the provider is in test mode

        pending = self._make_tx(provider_reference="1001")
        adhesion = self._make_tx("validation", state="done", token_id=self.token.id)
        debit = self._make_tx("offline", token_id=self.token.id, provider_reference="1002")
        other_debit = self._make_tx("offline", token_id=self.token.id, provider_reference="1003")
        reverted = self._make_tx(state="done", provider_reference="1004")
        reverted._post_process()
        signed = self._make_tx("validation", state="draft", tokenize=True, pagos360_child_amount=750.0)
        signed_row = {
            "id": 3001,
            "state": "signed",
            "external_reference": signed.reference,
            "created_at": str(self.day),
        }
        invoice = self.env["account.move"].create({"move_type": "out_invoice", "partner_id": self.partner.id})
        canceled = self._make_tx(state="cancel", provider_reference="1005", invoice_ids=[Command.set(invoice.ids)])
        already_done = self._make_tx(state="done", provider_reference="1006")

        txs_before = self.Transaction.search([])
        summary, checked = self._run_control(
            reports={
                "collection": [
                    {"request_id": 1001, "external_reference": pending.reference},
                    # Debits carry the reference of their adhesion, which all its debits share.
                    {"request_id": 1002, "external_reference": adhesion.reference},
                    {"request_id": 1005, "external_reference": canceled.reference},
                    {"request_id": 1006, "external_reference": already_done.reference},
                    {"request_id": 1999, "external_reference": "NOT-IN-ODOO"},
                ],
                "chargeback": [{"request_id": 1004, "external_reference": reverted.reference}],
            },
            adhesions=[("card-adhesion", signed_row)],
            entities={
                "payment-request": [
                    self._paid(1001, pending.reference),
                    {"id": 1004, "state": "reverted", "external_reference": reverted.reference},
                ],
                "debit-request": [self._paid(1002, adhesion.reference)],
                "card-adhesion": [signed_row],
            },
        )

        with self.subTest("cobro sin aviso: queda confirmado, con su pago a la fecha de Pagos360"):
            pending._post_process()
            self.assertEqual((pending.state, pending.payment_id.date), ("done", self.day))
        with self.subTest("débito: se cruza por su id, no por la referencia de la adhesión"):
            self.assertEqual((debit.state, other_debit.state, adhesion.state), ("done", "pending", "done"))
        with self.subTest("contracargo: queda cancelada, con su pago cancelado"):
            self.assertEqual((reverted.state, reverted.payment_id.state), ("cancel", "canceled"))
        with self.subTest("adhesión firmada: queda confirmada y dispara su cobro hijo"):
            child = signed.child_transaction_ids
            self.assertEqual(
                (signed.state, child.operation, child.amount, child.state), ("done", "online_token", 750.0, "pending")
            )
        with self.subTest("cancelada con cobro: sigue cancelada y su factura recibe una nota interna"):
            # The core already logs the cancellation on the invoice: look for the control note.
            notes = invoice.message_ids.filtered(lambda m: "1005" in (m.body or ""))
            self.assertEqual((canceled.state, notes.subtype_id), ("cancel", self.env.ref("mail.mt_note")))

        # Second layer: only what Odoo had not resolved was checked, no transaction has two active
        # payments, and the only transaction created is the child charge.
        self.assertEqual(set(checked), {pending, debit, reverted, signed})
        payments = self.env["account.payment"].search(
            [("payment_transaction_id", "in", txs_before.ids), ("state", "!=", "canceled")]
        )
        self.assertEqual(len(payments), len(payments.payment_transaction_id))
        self.assertEqual(self.Transaction.search([]) - txs_before, child)
        self.assertEqual(already_done.state, "done")
        self.assertIn("rescued=4", summary)
        self.assertIn("unmatched=1", summary)

    @mute_logger(LOGGER)
    def test_control_expired(self):
        """Step 2. Red if the due date is compared with `<=`, or if the control day is taken in UTC."""
        with freeze_time(self.now.replace(hour=1)):  # still the day before in Argentina
            self.assertEqual(self.provider._pagos360_get_control_date(), self.day - timedelta(days=1))
        validity = self.provider.pagos360_coupon_validity_days
        builders = {
            "solicitud de pago": lambda due: self._make_tx(created_on=due - timedelta(days=validity)),
            "débito": lambda due: self._make_tx(
                "offline",
                token_id=self.token.id,
                pagos360_debit_execution_date=due - timedelta(days=const.CONTROL_DEBIT_MARGIN_DAYS),
            ),
            "adhesión": lambda due: self._make_tx(
                "validation", created_on=due - timedelta(days=const.CONTROL_ADHESION_DAYS)
            ),
        }
        moments = {
            "todavía no venció": (1, False),
            "vence el día de control": (0, True),
            "día de la consulta de red": (-const.CONTROL_RECHECK_DAYS, True),
            "pasó la consulta de red": (-const.CONTROL_RECHECK_DAYS - 1, False),
        }
        txs = {
            (kind, moment): build(self.day + timedelta(days=offset))
            for kind, build in builders.items()
            for moment, (offset, __) in moments.items()
        }
        __, checked = self._run_control()
        for (kind, moment), tx in txs.items():
            with self.subTest(kind=kind, moment=moment):
                self.assertEqual(tx in checked, moments[moment][1])
        self.assertEqual(len(checked), len(set(checked)))

        with self.subTest("con N errores seguidos el control corta y deja un error"):
            self.env["ir.config_parameter"].sudo().set_param("pagos360.control_max_consecutive_errors", 2)
            summary, checked = self._run_control(fail_checks=True)
            self.assertEqual(len(checked), 2)
            self.assertIn("aborted=1", summary)
