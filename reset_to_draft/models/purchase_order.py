# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'reset.to.draft.mixin']

    def _reset_collect_chain(self):
        self.ensure_one()
        chain = super()._reset_collect_chain()
        # sudo: bill/picking o'qish huquqi cheklangan bo'lsa ham zanjir to'liq
        # yig'ilsin (bitta kompaniya — xavf yo'q).
        chain['invoices'] = self.sudo().invoice_ids
        chain['pickings'] = self.sudo().picking_ids.filtered(lambda p: p.state == 'done')
        chain['pending_pickings'] = self.sudo().picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel'))
        return chain

    def _reset_describe_chain(self, chain):
        ops = super()._reset_describe_chain(chain)
        ops.append({'document': self.display_name, 'operation': _("Reset the order to draft")})
        return ops

    def _reset_account_moves(self):
        self.ensure_one()
        return self.sudo().invoice_ids | self.sudo().picking_ids.move_ids.account_move_ids

    def _reset_linked_productions(self):
        self.ensure_one()
        # sudo: Purchase user'da MRP o'qish huquqi bo'lmasligi mumkin —
        # bog'liq ishlab chiqarishni tekshirishda manufacturing ruxsati so'ralmasin.
        return self.sudo().picking_ids.move_ids.move_dest_ids.created_production_id.filtered(
            lambda m: m.state == 'done')

    def _reset_detect_blockers(self):
        blockers = super()._reset_detect_blockers()
        # Core bir vendorning bir nechta buyurtmasini bitta bill'ga birlashtira
        # oladi — bunday umumiy bill'ni o'chirish boshqa buyurtmani buzadi.
        for bill in self.sudo().invoice_ids:
            other_orders = bill.invoice_line_ids.purchase_line_id.order_id - self
            if other_orders:
                blockers.append({
                    'record': bill,
                    'reason': _(
                        "Bill %(bill)s is shared with order(s) %(orders)s — "
                        "reset is not allowed while the shared bill exists.",
                        bill=bill.display_name,
                        orders=', '.join(other_orders.mapped('display_name'))),
                })
        done_pickings = self.sudo().picking_ids.filtered(lambda p: p.state == 'done')
        blockers += self._reset_stock_revert_blockers(done_pickings.move_ids)
        # stock_landed_costs o'rnatilmagan bo'lishi mumkin — registry orqali tekshiramiz.
        if 'stock.landed.cost' in self.env:
            landed_costs = self.env['stock.landed.cost'].sudo().search([
                ('picking_ids', 'in', self.sudo().picking_ids.ids),
                ('state', '=', 'done'),
            ])
            for cost in landed_costs:
                blockers.append({
                    'record': cost,
                    'reason': _(
                        "Landed cost %(name)s is validated on this order's "
                        "transfers — cancel it before resetting.",
                        name=cost.display_name),
                })
        if self._reset_linked_productions():
            blockers.append({
                'record': self,
                'reason': _("This order has linked manufacturing orders — reset those first."),
            })
        return blockers

    def _reset_execute(self):
        self.ensure_one()
        if self.state not in ('purchase', 'done'):
            raise UserError(_("Only confirmed purchase orders can be reset (%(name)s is %(state)s).",
                              name=self.display_name, state=self.state))
        self._reset_delete_account_moves(self.sudo().invoice_ids)
        # skip_order_flow_restore: receipt_delivery_restore unlink paytida (PO
        # hali 'purchase' holatda) yangi receipt yaratib qo'ymasin.
        pickings = self.sudo().picking_ids.with_context(skip_order_flow_restore=True)
        # done/pending'ni O'CHIRISHDAN OLDIN ajratamiz (done unlink qilingach
        # pickings ichida o'chgan record qolib, .state MissingError bermasin).
        done_pickings = pickings.filtered(lambda p: p.state == 'done')
        pending = pickings.filtered(lambda p: p.state not in ('done', 'cancel'))
        for picking in done_pickings:
            self._reset_delete_moves(picking.move_ids)
            picking.sudo().unlink()   # bo'sh receipt hujjatini ham o'chiramiz
        # Pending (done bo'lmagan) receipt'lar — cancel + o'chirish (stock hali
        # o'zgarmagan, faqat rezerv bo'shaydi) — PO draft'da osilib qolmasin.
        if pending:
            pending.sudo().action_cancel()
            pending.sudo().unlink()
        # PO amallari sudo bilan; `self` real user bo'lib qoladi -> chatter
        # haqiqiy user nomidan yoziladi.
        if self.state == 'done':
            # 18.0: qulflangan PO — state 'done' (purchase_order.py:104, button_unlock :559)
            self.sudo().button_unlock()
        self.sudo().button_draft()
        self._reset_post_chatter([_("Bills, movements and valuation removed; order reset to draft.")])
