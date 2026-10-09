from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

ENTITY_ID = 900001
REQUEST_RESULT_ID = 900002
PAID_AT = '2026-09-14T12:50:11.979Z'
INVOICE_AMOUNT = 30000.0


@tagged('post_install', '-at_install')
class TestBarcodePaymentAmount(AccountTestInvoicingCommon):
    """A cash notice (PagoFacil/Rapipago) must register what Pagos360 collected, whatever the
    invoice residual is when the notice arrives."""

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        # The accounting test user cannot manage providers; the webhook runs in sudo anyway.
        cls.provider = cls.env.ref('payment_pagos360.payment_provider_pagos360').sudo().copy({
            'company_id': cls.company_data['company'].id,
        })
        cls.provider.write({'state': 'test'})
        cls.provider.journal_id = cls.company_data['default_journal_bank']
        cls.env.ref('payment_pagos360.payment_method_pagofacil').sudo().active = True

    def setUp(self):
        super().setUp()
        self.api_response = {}
        self.api_calls = []

        def _fake_request(provider, endpoint, data=None, method='POST'):
            self.api_calls.append((method, endpoint))
            return self.api_response if method == 'GET' else {}

        self.startPatcher(patch.object(type(self.provider), '_pagos360_make_request', _fake_request))

    def _create_invoice(self):
        return self.init_invoice('out_invoice', amounts=[INVOICE_AMOUNT], taxes=[], post=True)

    def _register_manual_payment(self, invoice, amount):
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({'amount': amount})._create_payments()

    def _reference(self, invoice):
        return 'inv-%s-%s' % (self.provider.id, invoice.id)

    def _webhook_data(self, invoice):
        """A `payment_request.paid` notification as Pagos360 posts it: no amount in it."""
        return {
            'entity_name': 'payment_request',
            'type': 'paid',
            'entity_id': ENTITY_ID,
            'created_at': PAID_AT,
            'payload': {
                'id': ENTITY_ID,
                'request_result_id': REQUEST_RESULT_ID,
                'external_reference': self._reference(invoice),
            },
            'from_webhook': True,
        }

    def _api_payment_request(self, invoice, amount=INVOICE_AMOUNT, paid=True):
        request_result = [{
            'id': REQUEST_RESULT_ID, 'type': 'collected', 'amount': amount, 'paid_at': PAID_AT,
        }] if paid else []
        return {'data': [{
            'id': ENTITY_ID,
            'state': 'paid' if paid else 'pending',
            'external_reference': self._reference(invoice),
            'first_total': INVOICE_AMOUNT,
            'request_result': request_result,
        }]}

    def _process_notice(self, invoice):
        tx = self.env['payment.transaction'].sudo()._handle_notification_data(
            'pagos360', self._webhook_data(invoice))
        tx._finalize_post_processing()
        return tx

    def _receivable_residual(self, payment):
        return sum(payment.move_id.line_ids.filtered(
            lambda line: line.account_id == payment.destination_account_id
        ).mapped('amount_residual'))

    def _assert_inbound_payment(self, tx, amount):
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.amount, amount)
        payment = tx.payment_id
        self.assertTrue(payment)
        self.assertEqual(payment.state, 'posted')
        self.assertEqual(payment.payment_type, 'inbound')
        self.assertEqual(payment.amount, amount)
        self.assertEqual(str(payment.date), PAID_AT[:10])
        return payment

    def test_settled_invoice_leaves_the_payment_as_customer_credit(self):
        invoice = self._create_invoice()
        self._register_manual_payment(invoice, INVOICE_AMOUNT)
        self.assertEqual(invoice.amount_residual, 0.0)
        self.api_response = self._api_payment_request(invoice)

        tx = self._process_notice(invoice)

        payment = self._assert_inbound_payment(tx, INVOICE_AMOUNT)
        self.assertFalse(payment.is_reconciled)
        self.assertEqual(self._receivable_residual(payment), -INVOICE_AMOUNT)
        self.assertEqual(self.api_calls.count(('GET', '/payment-request?id=%s' % ENTITY_ID)), 1)

    def test_unpaid_invoice_is_reconciled_with_the_payment(self):
        invoice = self._create_invoice()
        self.api_response = self._api_payment_request(invoice)

        tx = self._process_notice(invoice)

        payment = self._assert_inbound_payment(tx, INVOICE_AMOUNT)
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))
        self.assertEqual(self._receivable_residual(payment), 0.0)

    def test_partially_paid_invoice_keeps_the_excess_as_customer_credit(self):
        invoice = self._create_invoice()
        self._register_manual_payment(invoice, 10000.0)
        self.assertEqual(invoice.amount_residual, 20000.0)
        self.api_response = self._api_payment_request(invoice)

        tx = self._process_notice(invoice)

        payment = self._assert_inbound_payment(tx, INVOICE_AMOUNT)
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertEqual(self._receivable_residual(payment), -10000.0)

    def test_amount_comes_from_the_payload_when_present(self):
        invoice = self._create_invoice()
        data = self._webhook_data(invoice)
        data['payload']['request_result'] = self._api_payment_request(invoice)['data'][0]['request_result']

        tx = self.env['payment.transaction'].sudo()._handle_notification_data('pagos360', data)
        tx._finalize_post_processing()

        self._assert_inbound_payment(tx, INVOICE_AMOUNT)
        self.assertFalse([call for call in self.api_calls if call[0] == 'GET'])

    def test_missing_amount_registers_nothing(self):
        invoice = self._create_invoice()
        self._register_manual_payment(invoice, INVOICE_AMOUNT)
        self.api_response = self._api_payment_request(invoice, paid=False)
        messages_before = invoice.message_ids

        try:
            self.env['payment.transaction'].sudo()._handle_notification_data(
                'pagos360', self._webhook_data(invoice))
        except ValidationError as error:
            self.assertIn('did not return the amount collected', str(error))
        else:
            self.fail("A notice without the amount collected must not be processed")

        self.assertFalse(self.env['payment.transaction'].search([
            ('reference', '=', self._reference(invoice)),
        ]))
        self.assertFalse(self.env['account.payment'].search([
            ('partner_id', '=', invoice.commercial_partner_id.id),
            ('payment_type', '=', 'outbound'),
        ]))
        note = invoice.message_ids - messages_before
        self.assertEqual(len(note), 1)
        self.assertIn('did not return the amount collected', note.body)
