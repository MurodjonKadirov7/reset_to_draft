# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class ResetToDraftMixin(models.AbstractModel):
    _name = 'reset.to.draft.mixin'
    _description = 'Reset to Draft Mixin'

    def action_open_reset_to_draft_wizard(self):
        self.ensure_one()
        return self._reset_wizard_action(self.ids)

    def action_open_reset_to_draft_wizard_multi(self):
        return self._reset_wizard_action(self.ids)

    def _reset_wizard_action(self, ids):
        return {
            'name': _("Reset to Draft"),
            'type': 'ir.actions.act_window',
            'res_model': 'reset.to.draft.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_model': self._name, 'active_ids': ids},
        }

    def _reset_collect_chain(self):
        self.ensure_one()
        return {
            'invoices': self.env['account.move'],
            'pickings': self.env['stock.picking'],
            'productions': self.env['mrp.production'],
        }

    def _reset_describe_chain(self, chain):
        self.ensure_one()
        ops = []
        for move in chain.get('invoices', self.env['account.move']):
            ops.append({'document': move.display_name,
                        'operation': _("Delete the accounting entry")})
        for picking in chain.get('pickings', self.env['stock.picking']):
            ops.append({'document': picking.display_name,
                        'operation': _("Remove movements & valuation, revert stock")})
        for picking in chain.get('pending_pickings', self.env['stock.picking']):
            ops.append({'document': picking.display_name,
                        'operation': _("Cancel and delete the pending transfer")})
        for production in chain.get('productions', self.env['mrp.production']):
            ops.append({'document': production.display_name,
                        'operation': _("Remove movements & valuation, revert stock")})
        return ops

    def _reset_account_moves(self):
        self.ensure_one()
        return self.env['account.move']

    def _reset_detect_blockers(self):
        self.ensure_one()
        blockers = []
        for move in self._reset_account_moves().filtered(lambda m: m.state == 'posted'):
            if move.inalterable_hash:
                blockers.append({
                    'record': move,
                    'reason': _("Journal entry %(name)s is hash-locked and cannot be removed.",
                                name=move.display_name),
                })
            # To'lov (payment) to'siq EMAS — o'chirishda avtomatik uziladi
            # (_reset_delete_account_moves). Payment posted qoladi, faqat
            # invoice/bill bilan bog'i uziladi ("outstanding" bo'lib qoladi).
            violated = move._get_violated_lock_dates(move.date, bool(move.line_ids.tax_ids))
            if violated:
                blockers.append({
                    'record': move,
                    'reason': _("Date falls in a locked period: %(dates)s.",
                                dates=move.company_id._format_lock_dates(violated)),
                })
        return blockers

    def _reset_stock_revert_blockers(self, moves):
        """Receipt/kirim move'ni qaytarish MANFIY qoldiq bermasligini tekshiradi.
        Agar kelgan tovar allaqachon keyingi bosqichga o'tgan bo'lsa (dest
        joyda yetarli qoldiq yo'q) — bloklaymiz, aks holda ombor manfiyga
        tushardi. Manba: stock_quant.py:825 (_get_available_quantity)."""
        Quant = self.env['stock.quant'].sudo()
        # Odoo 19+ da precision nomi 'Product Unit' (uom/data/uom_data.xml:6)
        prec = self.env['decimal.precision'].precision_get('Product Unit')
        blockers = []
        for line in moves.sudo().move_line_ids:
            qty = line.quantity_product_uom
            if not qty or line.location_dest_id.usage != 'internal':
                continue
            avail = Quant._get_available_quantity(
                line.product_id, line.location_dest_id,
                lot_id=line.lot_id, package_id=line.result_package_id,
                owner_id=line.owner_id, strict=True)
            if float_compare(avail, qty, precision_digits=prec) < 0:
                blockers.append({
                    'record': line.picking_id or line.move_id,
                    'reason': _(
                        "%(product)s: %(avail)s available at %(loc)s but "
                        "%(qty)s must be reverted — the goods have already "
                        "moved on. Reset the later step first.",
                        product=line.product_id.display_name, avail=avail,
                        loc=line.location_dest_id.display_name, qty=qty),
                })
        return blockers

    def _reset_execute(self):
        raise NotImplementedError()

    def _reset_delete_account_moves(self, moves):
        if not moves:
            return
        # sudo: reset guruhidagi user'da buxgalteriya o'chirish huquqi bo'lmasligi
        # mumkin (bitta kompaniya — record rule xavfi yo'q). Chatter esa `self`
        # (real user) orqali yoziladi, shuning uchun log'da haqiqiy user ko'rinadi.
        moves = moves.sudo()
        # Avval to'lov bog'ini uzamiz — payment posted qoladi (o'chmaydi/qaytmaydi),
        # invoice/bill esa keyin o'chadi. Manba: account_move.py:4204 (unlink ham
        # remove_move_reconcile chaqiradi; buni oldindan qilib toza uzamiz).
        moves.line_ids.remove_move_reconcile()
        posted = moves.filtered(lambda m: m.state == 'posted')
        # Odoo 20: `checked` o'rniga `review_state`. Draft qilishdagi tekshiruv
        # (_check_review_state_access) sudo'da o'tkazib yuboriladi
        # (account_move.py:3921 — `if self.env.su ... return`), `moves` esa sudo.
        posted.with_context(force_delete=True).button_draft()
        moves.with_context(force_delete=True).unlink()

    def _reset_revert_quants(self, moves):
        Quant = self.env['stock.quant'].sudo()
        for line in moves.sudo().move_line_ids:
            qty = line.quantity_product_uom
            if not qty:
                continue
            if line.location_dest_id.usage == 'internal':
                Quant._update_available_quantity(
                    line.product_id, line.location_dest_id, -qty,
                    lot_id=line.lot_id, package_id=line.result_package_id, owner_id=line.owner_id)
            if line.location_id.usage == 'internal':
                Quant._update_available_quantity(
                    line.product_id, line.location_id, qty,
                    lot_id=line.lot_id, package_id=line.package_id, owner_id=line.owner_id)

    def _reset_delete_moves(self, moves):
        if not moves:
            return
        moves = moves.sudo()
        move_ids = moves.ids
        products = moves.product_id
        self._reset_revert_quants(moves)
        self._reset_delete_account_moves(moves.account_move_id)
        # product.value = valuation qatlami (stock_account/models/product_value.py:14)
        self.env['product.value'].sudo().search([('move_id', 'in', move_ids)]).unlink()
        moves.analytic_account_line_ids.unlink()
        moves.write({'state': 'draft'})
        moves.unlink()
        products.sudo()._update_standard_price()

    def _reset_post_chatter(self, lines):
        body = Markup("<p><b>%s</b></p>") % _("Reset to Draft")
        if lines:
            items = Markup("").join(Markup("<li>%s</li>") % line for line in lines)
            body += Markup("<ul>%s</ul>") % items
        self.message_post(body=body)
