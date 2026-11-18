# -*- coding: utf-8 -*-
"""Odoo 18 valuation (stock.valuation.layer) qabul kriteriyalari.

Reset qatlamlarni operatsiyadan OLDINGI holatga aynan qaytarishi kerak:
FIFO'da kirim qatlamlarining remaining_qty/remaining_value'si, AVCO'da o'rtacha
tannarx. Natija aniq bo'lmaydigan holat (keyingi chiqim, lot bo'yicha baholash)
bloklanadi."""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestResetValuation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1
        )
        cls.stock = cls.warehouse.lot_stock_id
        cls.supplier = cls.env.ref("stock.stock_location_suppliers")
        cls.customer = cls.env.ref("stock.stock_location_customers")
        category = cls.env["product.category"]
        cls.categ_fifo = category.create(
            {
                "name": "RtD FIFO",
                "property_cost_method": "fifo",
                "property_valuation": "manual_periodic",
            }
        )
        cls.categ_avco = category.create(
            {
                "name": "RtD AVCO",
                "property_cost_method": "average",
                "property_valuation": "manual_periodic",
            }
        )
        cls.categ_std = category.create(
            {
                "name": "RtD Standard",
                "property_cost_method": "standard",
                "property_valuation": "manual_periodic",
            }
        )

    # ------------------------------------------------------------------
    # Yordamchilar
    # ------------------------------------------------------------------
    def _product(self, categ, **extra):
        return self.env["product.product"].create(
            dict(
                {
                    "name": f"RtD {categ.name}",
                    "is_storable": True,
                    "categ_id": categ.id,
                    "standard_price": 1.0,
                },
                **extra,
            )
        )

    def _transfer(self, product, qty, src, dst, picking_type, price=0.0, lot=None):
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "location_id": src.id,
                "location_dest_id": dst.id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "name": product.name,
                            "product_id": product.id,
                            "product_uom": product.uom_id.id,
                            "product_uom_qty": qty,
                            "price_unit": price,
                            "location_id": src.id,
                            "location_dest_id": dst.id,
                        },
                    )
                ],
            }
        )
        picking.action_confirm()
        picking.move_ids.quantity = qty
        if lot:
            picking.move_ids.move_line_ids.lot_name = lot
        picking.move_ids.picked = True
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        return picking

    def _receipt(self, product, qty, price, lot=None):
        return self._transfer(
            product,
            qty,
            self.supplier,
            self.stock,
            self.warehouse.in_type_id,
            price,
            lot,
        )

    def _delivery(self, product, qty):
        return self._transfer(
            product, qty, self.stock, self.customer, self.warehouse.out_type_id
        )

    def _layers(self, product):
        return self.env["stock.valuation.layer"].search(
            [("product_id", "=", product.id), ("company_id", "=", self.company.id)]
        )

    @staticmethod
    def _state(layers):
        return [(layer.remaining_qty, layer.remaining_value) for layer in layers]

    def _on_hand(self, product):
        return self.env["stock.quant"]._get_available_quantity(product, self.stock)

    def _reasons(self, record):
        return " | ".join(b["reason"] for b in record._reset_detect_blockers())

    # ------------------------------------------------------------------
    # FIFO
    # ------------------------------------------------------------------
    def test_fifo_delivery_reset_gives_the_layers_back(self):
        """Yetkazish o'chsa, u "yegan" kirim qatlami avvalgi holatiga qaytadi."""
        product = self._product(self.categ_fifo)
        self._receipt(product, 10, 10.0)
        self._receipt(product, 10, 20.0)
        incoming = self._layers(product)
        before = self._state(incoming)
        delivery = self._delivery(product, 5)
        self.assertEqual(incoming[0].remaining_qty, 5, "FIFO eng eski qatlamdan oladi")

        self.assertFalse(delivery._reset_detect_blockers())
        delivery._reset_execute()

        self.assertEqual(self._layers(product), incoming, "Chiqim qatlami o'chdi")
        self.assertEqual(self._state(incoming), before)
        self.assertAlmostEqual(product.standard_price, 15.0)
        self.assertEqual(self._on_hand(product), 20)

    def test_fifo_receipt_reset_keeps_the_older_layers(self):
        product = self._product(self.categ_fifo)
        self._receipt(product, 10, 10.0)
        first = self._layers(product)
        self._delivery(product, 5)
        second = self._receipt(product, 10, 20.0)

        self.assertFalse(second._reset_detect_blockers())
        second._reset_execute()

        self.assertEqual(self._state(first), [(5, 50)])
        self.assertEqual(len(self._layers(product)), 2, "Kirim va chiqim qoladi")
        self.assertAlmostEqual(product.standard_price, 10.0)
        self.assertEqual(self._on_hand(product), 5)

    def test_a_later_delivery_blocks_an_earlier_one(self):
        """Keyingi chiqim avvalgisining qatlamlari ustiga qiymatlangan."""
        product = self._product(self.categ_fifo)
        self._receipt(product, 10, 10.0)
        incoming = self._layers(product)
        first = self._delivery(product, 3)
        second = self._delivery(product, 4)

        self.assertIn(second.name, self._reasons(first))
        self.assertFalse(second._reset_detect_blockers())

        second._reset_execute()
        self.assertFalse(first._reset_detect_blockers(), "Endi oldingisi ochiq")
        first._reset_execute()
        self.assertEqual(self._state(incoming), [(10, 100)])

    # ------------------------------------------------------------------
    # AVCO
    # ------------------------------------------------------------------
    def test_avco_receipt_reset_recomputes_the_average(self):
        product = self._product(self.categ_avco)
        self._receipt(product, 10, 10.0)
        second = self._receipt(product, 10, 20.0)
        self.assertAlmostEqual(product.standard_price, 15.0)

        second._reset_execute()

        self.assertAlmostEqual(product.standard_price, 10.0)
        self.assertAlmostEqual(product.quantity_svl, 10)
        self.assertAlmostEqual(product.value_svl, 100)

    def test_avco_delivery_reset(self):
        product = self._product(self.categ_avco)
        self._receipt(product, 10, 10.0)
        self._receipt(product, 10, 30.0)
        incoming = self._layers(product)
        before = self._state(incoming)
        delivery = self._delivery(product, 5)

        delivery._reset_execute()

        self.assertAlmostEqual(product.quantity_svl, 20)
        self.assertAlmostEqual(product.value_svl, 400)
        self.assertAlmostEqual(product.standard_price, 20.0)
        self.assertEqual(self._state(incoming), before)

    def test_an_avco_receipt_used_by_a_later_delivery_is_blocked(self):
        """Omborda yetarli bo'lsa ham: chiqim shu kirim bilan o'rtachalangan narxda."""
        product = self._product(self.categ_avco)
        first = self._receipt(product, 10, 10.0)
        delivery = self._delivery(product, 5)
        self._receipt(product, 10, 20.0)
        self.assertEqual(self._on_hand(product), 15)

        self.assertIn(delivery.name, self._reasons(first))

    # ------------------------------------------------------------------
    # Standard narx va lot
    # ------------------------------------------------------------------
    def test_standard_price_is_not_restricted(self):
        product = self._product(self.categ_std, standard_price=10.0)
        self._receipt(product, 10, 10.0)
        first = self._delivery(product, 3)
        self._delivery(product, 4)

        self.assertFalse(first._reset_detect_blockers())
        first._reset_execute()
        self.assertEqual(self._on_hand(product), 6)
        self.assertAlmostEqual(product.value_svl, 60)

    def test_lot_valuated_products_are_blocked(self):
        product = self._product(self.categ_fifo, tracking="lot", lot_valuated=True)
        receipt = self._receipt(product, 5, 10.0, lot="RTD-LOT-1")

        self.assertIn("lot", self._reasons(receipt))
