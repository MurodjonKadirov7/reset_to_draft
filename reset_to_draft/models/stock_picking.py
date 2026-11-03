# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'reset.to.draft.mixin']

    def _reset_collect_chain(self):
        self.ensure_one()
        chain = super()._reset_collect_chain()
        chain['pickings'] = self
        return chain

    def _reset_account_moves(self):
        self.ensure_one()
        return self.sudo().move_ids.account_move_id

    def _reset_detect_blockers(self):
        self.ensure_one()
        blockers = super()._reset_detect_blockers()
        blockers += self._reset_stock_revert_blockers(self.sudo().move_ids)
        return blockers

    def _reset_execute(self):
        self.ensure_one()
        if self.state != 'done':
            raise UserError(_("Only done transfers can be reset (%(name)s is %(state)s).",
                              name=self.display_name, state=self.state))
        self._reset_delete_moves(self.sudo().move_ids)
        self._reset_post_chatter([_("Movements and valuation removed; stock reverted.")])
