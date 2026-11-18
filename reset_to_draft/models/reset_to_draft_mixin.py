# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


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
        tushardi. Manba: stock_quant.py:811 (_get_available_quantity)."""
        Quant = self.env['stock.quant'].sudo()
        # Odoo 18'da precision nomi 'Product Unit of Measure' (product/data/product_data.xml:36)
        prec = self.env['decimal.precision'].precision_get('Product Unit of Measure')
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
        blockers += self._reset_valuation_blockers(moves)
        return blockers

    def _reset_valuation_blockers(self, moves):
        """Odoo 18: qatlamlarni o'chirish faqat ANIQ natija beradigan holda ruxsat.

        18.0 da har bir chiqim `_run_fifo` orqali kirim qatlamlarining
        remaining_qty/remaining_value'sini kamaytiradi (FIFO ham, AVCO ham —
        stock_account/models/product.py:286), qaysi qatlamdan qancha olingani
        esa yozilmaydi. FIFO/AVCO'da keyingi chiqim (yoki qo'lda qayta baholash)
        shu qatlamlar ustiga qiymatlangan bo'ladi — ularni o'chirish keyingi
        hujjat tannarxini buzadi. Shunday holatlarni bloklaymiz:
        - lot bo'yicha baholanadigan mahsulot (lot_valuated);
        - manfiy qoldiq (remaining_qty < 0, hali vacuum qilinmagan);
        - shu qatlamlardan KEYIN shu mahsulot bo'yicha chiqim/qayta baholash bor.
        Standard narxda chiqim qiymati tarixga bog'liq emas — cheklov yo'q.
        """
        Layer = self.env['stock.valuation.layer'].sudo()
        blockers = []
        groups = {}
        for layer in moves.sudo().stock_valuation_layer_ids:
            key = (layer.product_id, layer.company_id)
            groups[key] = groups.get(key, Layer) | layer
        for (product, company), own in groups.items():
            product = product.with_company(company)
            first_move = own[:1].stock_move_id
            if product.lot_valuated:
                blockers.append({
                    'record': first_move.picking_id or first_move,
                    'reason': _(
                        "%(product)s is valued per lot/serial number — resetting its "
                        "movements is not supported.",
                        product=product.display_name),
                })
                continue
            if product.cost_method not in ('average', 'fifo'):
                continue
            rounding = product.uom_id.rounding
            history = Layer.search([('product_id', '=', product.id), ('company_id', '=', company.id)])
            if any(float_compare(layer.remaining_qty, 0, precision_rounding=rounding) < 0 for layer in history):
                blockers.append({
                    'record': first_move.picking_id or first_move,
                    'reason': _(
                        "%(product)s has a negative stock valuation that is not settled "
                        "yet — receive the missing quantity first.",
                        product=product.display_name),
                })
                continue
            last = max(own, key=lambda layer: (layer.create_date, layer.id))
            later = (history - own).filtered(
                lambda layer: (layer.create_date, layer.id) > (last.create_date, last.id)
                and (float_compare(layer.quantity, 0, precision_rounding=rounding) < 0
                     or (float_is_zero(layer.quantity, precision_rounding=rounding)
                         and not layer.stock_valuation_layer_id)))
            if later:
                move = later[0].stock_move_id
                doc = (move.picking_id or move).display_name or later[0].description
                blockers.append({
                    'record': move.picking_id or move or later[0],
                    'reason': _(
                        "%(product)s: %(doc)s was valued after this document with "
                        "FIFO/AVCO costing, so its cost depends on it. Reset %(doc)s first.",
                        product=product.display_name, doc=doc),
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
        # invoice/bill esa keyin o'chadi. Manba: account_move.py:5617 (button_draft
        # ham remove_move_reconcile chaqiradi; buni oldindan qilib toza uzamiz).
        moves.line_ids.remove_move_reconcile()
        posted = moves.filtered(lambda m: m.state == 'posted')
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
        pairs = {(move.product_id, move.company_id) for move in moves}
        # Odoo 18: valuation = stock.valuation.layer (stock_account/models/
        # stock_valuation_layer.py:16); bog'langan tuzatish qatlamlari ham
        # (stock_valuation_layer_id, masalan landed cost) shu harakatga tegishli.
        layers = moves.stock_valuation_layer_ids
        layers |= layers.stock_valuation_layer_ids
        self._reset_revert_quants(moves)
        # 18.0: stock.move.account_move_ids (stock_account/models/stock_move.py:19)
        self._reset_delete_account_moves(moves.account_move_ids | layers.account_move_id)
        layers.unlink()
        moves.analytic_account_line_ids.unlink()
        moves.write({'state': 'draft'})
        moves.unlink()
        self._reset_rebuild_valuation(pairs)

    def _reset_rebuild_valuation(self, pairs):
        """Qatlamlar o'chgach FIFO qoldig'ini va o'rtacha tannarxni tiklaydi.

        Chiqim qatlami o'chganda u "yegan" kirim qatlamlarining remaining_qty'si
        qaytishi kerak, lekin 18.0 qaysi qatlamdan olinganini saqlamaydi. FIFO
        har doim eng eski qatlamdan oladi (_run_fifo, _order='create_date, id'),
        demak qolgan miqdor doim ENG YANGI kirim qatlamlarida turadi: jami
        miqdorni yangidan eskiga qarab taqsimlaymiz — bu aynan FIFO holati.
        Keyingi chiqimlar bo'lsa reset bloklangan (_reset_valuation_blockers),
        shuning uchun natija operatsiyadan oldingi holat bilan bir xil.
        Narx: qatlamda qoldiq bo'lsa uning joriy birlik qiymati (landed cost
        kabi tuzatishlar bilan), bo'lmasa qatlamning o'z birlik qiymati.
        O'rtacha tannarx — core idiomasi (stock_account/models/product.py:565).
        """
        Layer = self.env['stock.valuation.layer'].sudo()
        for product, company in pairs:
            product = product.sudo().with_company(company)
            rounding = product.uom_id.rounding
            currency = company.currency_id
            history = Layer.search([('product_id', '=', product.id), ('company_id', '=', company.id)])
            left = sum(history.mapped('quantity'))
            incoming = history.filtered(
                lambda layer: float_compare(layer.quantity, 0, precision_rounding=rounding) > 0)
            for layer in reversed(incoming):
                qty = max(0.0, min(layer.quantity, left))
                left -= qty
                if float_compare(layer.remaining_qty, 0, precision_rounding=rounding) > 0:
                    unit = layer.remaining_value / layer.remaining_qty
                else:
                    unit = (layer.value + sum(layer.stock_valuation_layer_ids.mapped('value'))) / layer.quantity
                value = currency.round(qty * unit)
                if (float_compare(layer.remaining_qty, qty, precision_rounding=rounding)
                        or currency.compare_amounts(layer.remaining_value, value)):
                    layer.write({'remaining_qty': qty, 'remaining_value': value})
            product.invalidate_recordset(['value_svl', 'quantity_svl'])
            if (product.cost_method in ('average', 'fifo')
                    and not float_is_zero(product.quantity_svl, precision_rounding=rounding)):
                product.with_context(disable_auto_svl=True).write(
                    {'standard_price': product.value_svl / product.quantity_svl})

    def _reset_post_chatter(self, lines):
        body = Markup("<p><b>%s</b></p>") % _("Reset to Draft")
        if lines:
            items = Markup("").join(Markup("<li>%s</li>") % line for line in lines)
            body += Markup("<ul>%s</ul>") % items
        self.message_post(body=body)
