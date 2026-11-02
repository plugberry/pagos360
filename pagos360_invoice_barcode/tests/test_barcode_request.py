from datetime import timedelta
from unittest.mock import patch

import requests
from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

BARCODE = "73850000000300000000000000000000000000000000000"
REQUEST_ID_OFFSET = 900000
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'


class _FakeResponse:
    content = SVG


@tagged("post_install", "-at_install")
class TestBarcodeRequest(AccountTestInvoicingCommon):
    """Confirming an invoice never waits for Pagos360: the barcode is requested by a cron, with
    retries, and the invoice is not sent until it has one."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = (
            cls.env.ref("payment_pagos360.payment_provider_pagos360")
            .sudo()
            .copy({"company_id": cls.company_data["company"].id})
        )
        cls.provider.write({"active": True, "is_live": False, "pagos360_test_api_key": "test-key"})
        # The demo provider shares the company: archive it so it does not take part. By SQL, as
        # payment_pagos360 checks the keys on write.
        demo = cls.env.ref("payment_pagos360.payment_provider_pagos360")
        cls.env.cr.execute("UPDATE payment_provider SET active = FALSE WHERE id = %s", [demo.id])
        demo.invalidate_recordset(["active"])

    def setUp(self):
        super().setUp()
        self.pagos360_down = False
        self.down_invoice_ids = set()
        self.no_barcode = False
        self.images_down = False
        self.requests = []
        self.reads = []

        def _fake_request(provider, endpoint, data=None, method="POST"):
            if method == "GET":
                request_id = int(endpoint.rsplit("/", 1)[1])
                self.reads.append(request_id)
                invoice_id = request_id - REQUEST_ID_OFFSET
            else:
                reference = data["payment_request"]["external_reference"]
                self.requests.append(reference)
                invoice_id = int(reference.rsplit("-", 1)[1])
            if self.pagos360_down or invoice_id in self.down_invoice_ids:
                raise ValidationError("PAGOS360: Could not establish the connection to the API.")
            response = {"id": REQUEST_ID_OFFSET + invoice_id, "first_total": 1000.0}
            if not self.no_barcode:
                response.update(barcode=BARCODE, barcode_url="https://example.com/barcode.svg")
            return response

        def _fake_get(url, timeout=None):
            if self.images_down:
                raise requests.ConnectionError("image server down")
            return _FakeResponse()

        self.startPatcher(patch.object(type(self.provider), "_pagos360_make_request", _fake_request))
        self.startPatcher(patch("odoo.addons.pagos360_invoice_barcode.models.account_move.requests.get", _fake_get))
        # The cron commits its progress, which a test cursor refuses.
        self.startPatcher(patch.object(self.env.cr, "commit"))

    def _post_invoice(self):
        return self.init_invoice("out_invoice", amounts=[1000.0], taxes=[], post=True)

    @mute_logger("odoo.addons.pagos360_invoice_barcode.models.account_move")
    def _run_cron(self):
        self.env["account.move"]._cron_pagos360_request_barcodes()

    def test_confirming_does_not_wait_for_pagos360(self):
        self.pagos360_down = True

        invoice = self._post_invoice()

        self.assertEqual(invoice.state, "posted")
        self.assertFalse(invoice.pagos360_barcode)
        self.assertTrue(invoice.pagos360_barcode_pending_since)
        self.assertFalse(self.requests)

    def test_cron_gets_the_barcode(self):
        invoice = self._post_invoice()

        self._run_cron()

        self.assertEqual(invoice.pagos360_barcode, BARCODE)
        self.assertEqual(invoice.pagos360_barcode_image.content, SVG)
        self.assertEqual(invoice.pagos360_barcode_amount, 1000.0)
        self.assertFalse(invoice.pagos360_barcode_pending_since)

    def test_a_failed_image_download_does_not_create_the_payment_request_again(self):
        invoice = self._post_invoice()
        self.images_down = True

        self._run_cron()

        self.assertEqual(invoice.pagos360_barcode, BARCODE)
        self.assertFalse(invoice.pagos360_barcode_image)
        self.assertEqual(invoice.pagos360_payment_request_id, REQUEST_ID_OFFSET + invoice.id)
        self.assertTrue(invoice.pagos360_barcode_pending_since)

        self.images_down = False
        invoice.pagos360_barcode_last_try = False
        self._run_cron()

        self.assertEqual(len(self.requests), 1)
        self.assertEqual(invoice.pagos360_barcode_image.content, SVG)
        self.assertFalse(invoice.pagos360_barcode_pending_since)

    def test_a_payment_request_without_barcode_is_read_back_not_created_again(self):
        invoice = self._post_invoice()
        self.no_barcode = True

        self._run_cron()

        self.assertFalse(invoice.pagos360_barcode)
        self.assertEqual(invoice.pagos360_payment_request_id, REQUEST_ID_OFFSET + invoice.id)

        self.no_barcode = False
        invoice.pagos360_barcode_last_try = False
        self._run_cron()

        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.reads, [REQUEST_ID_OFFSET + invoice.id])
        self.assertEqual(invoice.pagos360_barcode, BARCODE)
        self.assertFalse(invoice.pagos360_barcode_pending_since)

    def test_a_failure_keeps_the_invoice_pending_and_the_others_go_on(self):
        failing = self._post_invoice()
        working = self._post_invoice()
        self.down_invoice_ids.add(failing.id)

        self._run_cron()

        self.assertFalse(failing.pagos360_barcode)
        self.assertTrue(failing.pagos360_barcode_pending_since)
        self.assertEqual(working.pagos360_barcode, BARCODE)
        self.assertFalse(working.pagos360_barcode_pending_since)

        self.down_invoice_ids.clear()
        failing.pagos360_barcode_last_try = False
        self._run_cron()

        self.assertEqual(failing.pagos360_barcode, BARCODE)
        self.assertFalse(failing.pagos360_barcode_pending_since)

    def test_an_invoice_paid_meanwhile_is_not_requested(self):
        invoice = self._post_invoice()
        self.env["account.payment.register"].with_context(active_model="account.move", active_ids=invoice.ids).create(
            {"amount": invoice.amount_residual}
        )._create_payments()

        self._run_cron()

        self.assertFalse(self.requests)
        self.assertFalse(invoice.pagos360_barcode_pending_since)

    def test_a_manual_send_waits_for_the_barcode(self):
        invoice = self._post_invoice()
        self.pagos360_down = True

        with self.assertRaisesRegex(UserError, "barcode"):
            self.env["account.move.send"]._generate_and_send_invoices(invoice, sending_methods=["manual"])

        self.assertFalse(invoice.is_move_sent)
        self.assertTrue(invoice.pagos360_barcode_pending_since)

    def test_a_manual_send_requests_the_barcode_when_missing(self):
        invoice = self._post_invoice()

        self.env["account.move.send"]._generate_and_send_invoices(invoice, sending_methods=["manual"])

        self.assertEqual(invoice.pagos360_barcode, BARCODE)
        self.assertFalse(invoice.pagos360_barcode_pending_since)
        self.assertTrue(invoice.invoice_pdf_report_id)

    def _queue(self, invoice):
        invoice.sending_data = {"author_user_id": self.env.user.id, "author_partner_id": self.env.user.partner_id.id}

    def test_a_queued_send_waits_out_of_the_queue_and_comes_back(self):
        invoice = self._post_invoice()
        self.pagos360_down = True
        self._queue(invoice)

        with mute_logger("odoo.addons.pagos360_invoice_barcode.models.account_move"):
            self.env["account.move.send"]._generate_and_send_invoices(invoice, from_cron=True)

        self.assertFalse(invoice.sending_data)
        self.assertTrue(invoice.pagos360_sending_data)
        self.assertFalse(invoice.invoice_pdf_report_id)

        self.pagos360_down = False
        invoice.pagos360_barcode_last_try = False
        self._run_cron()

        self.assertEqual(invoice.pagos360_barcode, BARCODE)
        self.assertTrue(invoice.sending_data)
        self.assertFalse(invoice.pagos360_sending_data)

    def test_waiting_invoices_do_not_block_the_sending_queue(self):
        waiting = self._post_invoice()
        ready = self._post_invoice()
        ready.pagos360_barcode_pending_since = False
        self.down_invoice_ids.add(waiting.id)
        self._queue(waiting)
        self._queue(ready)

        with mute_logger("odoo.addons.pagos360_invoice_barcode.models.account_move"):
            self.env["account.move"]._cron_account_move_send(job_count=1)
            self.env["account.move"]._cron_account_move_send(job_count=1)

        self.assertTrue(ready.invoice_pdf_report_id)
        self.assertFalse(ready.sending_data)
        self.assertFalse(waiting.sending_data)
        self.assertTrue(waiting.pagos360_sending_data)

    def test_a_send_that_must_not_raise_waits_instead(self):
        invoice = self._post_invoice()
        self.pagos360_down = True

        with mute_logger("odoo.addons.pagos360_invoice_barcode.models.account_move"):
            self.env["account.move.send"]._generate_and_send_invoices(
                invoice, allow_raising=False, allow_fallback_pdf=True
            )

        self.assertFalse(invoice.invoice_pdf_report_id)
        self.assertTrue(invoice.pagos360_sending_data)

    def test_a_provider_without_the_key_of_its_mode_does_not_request_barcodes(self):
        # As the copy a new company gets once payment_pagos360 does not copy the keys: today
        # res.company.create copies them, so the company of this test already has one with keys.
        self.env.cr.execute(
            "UPDATE payment_provider SET pagos360_api_key = NULL, pagos360_test_api_key = NULL"
            " WHERE code = 'pagos360' AND company_id = %s",
            [self.provider.company_id.id],
        )
        self.env["payment.provider"].invalidate_model(["pagos360_api_key", "pagos360_test_api_key"])

        invoice = self._post_invoice()

        self.assertFalse(invoice.pagos360_barcode_pending_since)

    def test_a_company_without_provider_stops_waiting(self):
        invoice = self._post_invoice()
        self.env.cr.execute(
            "UPDATE payment_provider SET active = FALSE WHERE code = 'pagos360' AND company_id = %s",
            [invoice.company_id.id],
        )
        self.env["payment.provider"].invalidate_model(["active"])

        self._run_cron()

        self.assertFalse(self.requests)
        self.assertFalse(invoice.pagos360_barcode_pending_since)

    def test_an_invoice_tried_recently_is_not_retried(self):
        invoice = self._post_invoice()
        invoice.pagos360_barcode_last_try = fields.Datetime.now() - timedelta(minutes=10)

        self._run_cron()

        self.assertFalse(self.requests)
        self.assertTrue(invoice.pagos360_barcode_pending_since)

    def test_after_five_tries_the_cron_gives_up_and_tells_the_responsible(self):
        invoice = self._post_invoice()
        invoice.invoice_user_id = self.env.user
        self.pagos360_down = True

        for _try in range(4):
            invoice.pagos360_barcode_last_try = False
            self._run_cron()
        self.assertEqual(invoice.pagos360_barcode_tries, 4)
        self.assertFalse(invoice.activity_ids)

        invoice.pagos360_barcode_last_try = False
        self._run_cron()
        self.assertEqual(invoice.pagos360_barcode_tries, 5)
        self.assertEqual(len(invoice.activity_ids), 1)
        self.assertEqual(invoice.activity_ids.user_id, self.env.user)

        invoice.pagos360_barcode_last_try = False
        self._run_cron()
        self.assertEqual(len(self.requests), 5)
        self.assertEqual(len(invoice.activity_ids), 1)

        # Sending it by hand requests the barcode again.
        self.pagos360_down = False
        self.env["account.move.send"]._generate_and_send_invoices(invoice, sending_methods=["manual"])
        self.assertEqual(invoice.pagos360_barcode, BARCODE)
        self.assertEqual(invoice.pagos360_barcode_tries, 0)

    def test_an_invoice_posted_without_action_post_waits_too(self):
        invoice = self.init_invoice("out_invoice", amounts=[1000.0], taxes=[])
        invoice._post()

        self.assertTrue(invoice.pagos360_barcode_pending_since)

    def test_printing_waits_for_the_barcode(self):
        invoice = self._post_invoice()
        self.pagos360_down = True

        with (
            mute_logger("odoo.addons.pagos360_invoice_barcode.models.account_move"),
            self.assertRaisesRegex(UserError, "barcode"),
        ):
            self.env["ir.actions.report"]._render_qweb_pdf("account.account_invoices", invoice.ids)

        self.pagos360_down = False
        self.env["ir.actions.report"]._render_qweb_pdf("account.account_invoices", invoice.ids)
        self.assertEqual(invoice.pagos360_barcode, BARCODE)

    def test_without_a_real_responsible_the_alert_is_a_note(self):
        invoice = self._post_invoice()
        invoice.write({"invoice_user_id": False, "pagos360_barcode_tries": 4})
        # As when a cron creates the invoice; create_uid cannot be written through the ORM.
        self.env.cr.execute(
            "UPDATE account_move SET create_uid = %s WHERE id = %s", [self.env.ref("base.user_root").id, invoice.id]
        )
        invoice.invalidate_recordset(["create_uid"])
        self.pagos360_down = True
        messages_before = invoice.message_ids

        self._run_cron()

        self.assertFalse(invoice.activity_ids)
        new_bodies = (invoice.message_ids - messages_before).mapped("body")
        self.assertEqual(len([body for body in new_bodies if "did not return the barcode" in body]), 1)
