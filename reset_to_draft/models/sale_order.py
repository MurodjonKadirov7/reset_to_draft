# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'reset.to.draft.mixin']

    def _reset_collect_chain(self):
        self.ensure_one()
        chain = super()._reset_collect_chain()
        # sudo: user'da invoice/picking o'qish huquqi cheklangan bo'lsa ham
        # zanjir to'liq yig'ilsin (bitta kompaniya — xavf yo'q).
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
        return self.sudo().invoice_ids | self.sudo().picking_ids.move_ids.account_move_id

    def _reset_detect_blockers(self):
        blockers = super()._reset_detect_blockers()
        # Core bir mijozning bir nechta buyurtmasini bitta invoice'ga birlashtira
        # oladi — bunday umumiy invoice'ni o'chirish boshqa buyurtmani buzadi.
        for invoice in self.sudo().invoice_ids:
            other_orders = invoice.invoice_line_ids.sale_line_ids.order_id - self
            if other_orders:
                blockers.append({
                    'record': invoice,
                    'reason': _(
                        "Invoice %(invoice)s is shared with order(s) %(orders)s — "
                        "reset is not allowed while the shared invoice exists.",
                        invoice=invoice.display_name,
                        orders=', '.join(other_orders.mapped('display_name'))),
                })
        done_pickings = self.sudo().picking_ids.filtered(lambda p: p.state == 'done')
        blockers += self._reset_stock_revert_blockers(done_pickings.move_ids)
        # sudo: Sales user'da MRP o'qish huquqi bo'lmasligi mumkin — bog'liq
        # ishlab chiqarishni tekshirish uchun manufacturing ruxsati so'ralmasin.
        if self.sudo().mrp_production_ids.filtered(lambda m: m.state == 'done'):
            blockers.append({
                'record': self,
                'reason': _("This order has linked manufacturing orders — reset those first."),
            })
        return blockers

    def _reset_execute(self):
        self.ensure_one()
        if self.state != 'sale':
            raise UserError(_("Only confirmed sales orders can be reset (%(name)s is %(state)s).",
                              name=self.display_name, state=self.state))
        self._reset_delete_account_moves(self.sudo().invoice_ids)
        # skip_order_flow_restore: receipt_delivery_restore unlink paytida (SO
        # hali 'sale' holatda) yangi delivery yaratib qo'ymasin.
        pickings = self.sudo().picking_ids.with_context(skip_order_flow_restore=True)
        # done/pending'ni O'CHIRISHDAN OLDIN ajratamiz (done unlink qilingach
        # pickings ichida o'chgan record qolib, .state MissingError bermasin).
        done_pickings = pickings.filtered(lambda p: p.state == 'done')
        pending = pickings.filtered(lambda p: p.state not in ('done', 'cancel'))
        for picking in done_pickings:
            self._reset_delete_moves(picking.move_ids)
            picking.sudo().unlink()   # bo'sh delivery hujjatini ham o'chiramiz
        # Pending (done bo'lmagan) delivery'lar — cancel + o'chirish (stock hali
        # o'zgarmagan, faqat rezerv bo'shaydi) — SO draft'da osilib qolmasin.
        if pending:
            pending.sudo().action_cancel()
            pending.sudo().unlink()
        # SO amallari sudo bilan (huquq muammosi bo'lmasin); `self` real user
        # bo'lib qoladi, shuning uchun chatter haqiqiy user nomidan yoziladi.
        self.sudo().write({'locked': False})
        self.sudo()._action_cancel()
        self.sudo().action_draft()
        self._reset_post_chatter([_("Invoices, movements and valuation removed; order reset to draft.")])
