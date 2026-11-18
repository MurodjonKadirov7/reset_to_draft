# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged("post_install", "-at_install")
class TestResetToDraft(TransactionCase):
    """Cover the behaviour the manifest promises: the movements, their
    valuation and the stock entry go away, the on-hand comes back, and the
    document becomes a real draft."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1
        )
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.supplier_location = cls.env.ref("stock.stock_location_suppliers")
        cls.customer_location = cls.env.ref("stock.stock_location_customers")

        cls.product = cls.env["product.product"].create(
            {
                "name": "Reset Product",
                "is_storable": True,
                "standard_price": 10.0,
                "list_price": 25.0,
            }
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _on_hand(self, location=None):
        """Quantity available at the given location, defaults to stock."""
        return self.env["stock.quant"]._get_available_quantity(
            self.product, location or self.stock_location
        )

    def _receipt(self, quantity=5.0):
        """A validated incoming transfer bringing the goods in."""
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.warehouse.in_type_id.id,
                "location_id": self.supplier_location.id,
                "location_dest_id": self.stock_location.id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "uom_id": self.product.uom_id.id,
                            "product_uom_qty": quantity,
                            "location_id": self.supplier_location.id,
                            "location_dest_id": self.stock_location.id,
                        },
                    )
                ],
            }
        )
        picking.action_confirm()
        picking.move_ids.quantity = quantity
        picking.move_ids.picked = True
        picking.button_validate()
        return picking

    def _delivery(self, quantity):
        """A validated outgoing transfer taking the goods away."""
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.warehouse.out_type_id.id,
                "location_id": self.stock_location.id,
                "location_dest_id": self.customer_location.id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "uom_id": self.product.uom_id.id,
                            "product_uom_qty": quantity,
                            "location_id": self.stock_location.id,
                            "location_dest_id": self.customer_location.id,
                        },
                    )
                ],
            }
        )
        picking.action_confirm()
        picking.move_ids.quantity = quantity
        picking.move_ids.picked = True
        picking.button_validate()
        return picking

    def _wizard_for(self, records):
        """The wizard as the dialog builds it, from the action context."""
        return (
            self.env["reset.to.draft.wizard"]
            .with_context(active_model=records._name, active_ids=records.ids)
            .create({})
        )

    # ------------------------------------------------------------------
    # The receipt goes back
    # ------------------------------------------------------------------
    def test_receipt_reset_reverts_the_on_hand(self):
        """Kirim qaytarilsa ombor qoldig'i avvalgi holatga qaytsin."""
        before = self._on_hand()
        picking = self._receipt(5.0)
        self.assertEqual(self._on_hand(), before + 5.0, "Kirim qoldiqni oshirsin")

        picking._reset_execute()

        self.assertEqual(self._on_hand(), before, "Qoldiq avvalgi holatga qaytsin")

    def test_receipt_reset_removes_the_movements(self):
        """Move'lar va ularning valuation qatlami o'chirilsin."""
        picking = self._receipt(5.0)
        move_ids = picking.move_ids.ids
        self.assertTrue(move_ids)

        picking._reset_execute()

        self.assertFalse(
            self.env["stock.move"].search([("id", "in", move_ids)]),
            "Harakatlar o'chirilsin",
        )
        self.assertFalse(
            self.env["product.value"].sudo().search([("move_id", "in", move_ids)]),
            "Valuation qatlami o'chirilsin",
        )

    def test_receipt_becomes_draft_again(self):
        """Move'siz qolgan hujjat haqiqiy draft bo'lsin (stock_picking.py:845)."""
        picking = self._receipt(5.0)

        picking._reset_execute()

        self.assertEqual(picking.state, "draft")

    def test_reset_posts_to_the_chatter(self):
        """Nima qilinganini chatter yozib qo'ysin — audit izi qolsin."""
        picking = self._receipt(5.0)
        before = len(picking.message_ids)

        picking._reset_execute()

        self.assertGreater(len(picking.message_ids), before)

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    def test_only_done_transfers_can_be_reset(self):
        """Done bo'lmagan hujjat reset qilinmasin."""
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.warehouse.in_type_id.id,
                "location_id": self.supplier_location.id,
                "location_dest_id": self.stock_location.id,
            }
        )

        with self.assertRaises(UserError):
            picking._reset_execute()

    def test_goods_already_moved_on_are_blocked(self):
        """Tovar keyingi bosqichga o'tgan bo'lsa — bloklansin, aks holda
        ombor manfiyga tushardi (_reset_stock_revert_blockers)."""
        receipt = self._receipt(5.0)
        self._delivery(5.0)

        blockers = receipt._reset_detect_blockers()

        self.assertTrue(blockers, "Qoldiq yetmasa to'siq chiqsin")
        self.assertIn("moved on", blockers[0]["reason"])

    def test_a_reset_with_blockers_is_refused(self):
        """Wizard to'siq borligida hech narsa o'chirmasin."""
        receipt = self._receipt(5.0)
        self._delivery(5.0)
        wizard = self._wizard_for(receipt)
        self.assertTrue(wizard.is_blocked)

        with self.assertRaises(UserError):
            wizard.action_confirm_reset()

        self.assertEqual(receipt.state, "done", "Hujjat tegilmasin")

    # ------------------------------------------------------------------
    # The wizard dialog
    # ------------------------------------------------------------------
    def test_the_wizard_describes_what_goes_away(self):
        """Dialog "Will be removed" ro'yxatini to'ldirsin."""
        receipt = self._receipt(5.0)

        wizard = self._wizard_for(receipt)

        self.assertFalse(wizard.is_blocked)
        self.assertEqual(wizard.res_model, "stock.picking")
        self.assertIn(receipt.name, wizard.target_display)
        self.assertTrue(wizard.line_ids, "Nima o'chishi ko'rsatilsin")

    def test_the_wizard_resets_the_receipt(self):
        """Dialog tasdiqlansa — reset bajarilsin.

        Guruh aniq beriladi: post_init_hook uni faqat base.user_admin'ga
        qo'yadi (__init__.py:6-13), test foydalanuvchisi esa boshqa.
        """
        before = self._on_hand()
        receipt = self._receipt(5.0)
        wizard = self._wizard_for(receipt)
        self.env.user.group_ids = [
            (4, self.env.ref("reset_to_draft.group_reset_to_draft_purchase").id)
        ]

        wizard.action_confirm_reset()

        self.assertEqual(receipt.state, "draft")
        self.assertEqual(self._on_hand(), before)

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------
    def test_a_user_without_the_group_cannot_reset(self):
        """Guruhsiz foydalanuvchi reset qila olmasin."""
        receipt = self._receipt(5.0)
        wizard = self._wizard_for(receipt)
        plain = new_test_user(
            self.env, login="reset_plain", groups="stock.group_stock_user"
        )

        with self.assertRaises(AccessError):
            wizard.with_user(plain)._check_reset_access("stock.picking")

    def test_a_user_with_the_group_may_reset(self):
        """Guruhi bor foydalanuvchi uchun tekshiruv o'tsin."""
        receipt = self._receipt(5.0)
        wizard = self._wizard_for(receipt)
        allowed = new_test_user(
            self.env,
            login="reset_allowed",
            groups="stock.group_stock_user,"
            "reset_to_draft.group_reset_to_draft_purchase",
        )

        wizard.with_user(allowed)._check_reset_access("stock.picking")

    def test_the_admin_gets_the_groups_on_install(self):
        """post_init_hook admin'ga ikkala guruhni bersin."""
        admin = self.env.ref("base.user_admin")

        self.assertTrue(admin.has_group("reset_to_draft.group_reset_to_draft_sale"))
        self.assertTrue(admin.has_group("reset_to_draft.group_reset_to_draft_purchase"))
