# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _name = 'mrp.production'
    _inherit = ['mrp.production', 'reset.to.draft.mixin']

    def _reset_collect_chain(self):
        self.ensure_one()
        chain = super()._reset_collect_chain()
        chain['productions'] = self
        return chain

    def _reset_account_moves(self):
        self.ensure_one()
        return (self.sudo().move_raw_ids | self.sudo().move_finished_ids).account_move_ids

    def _reset_detect_blockers(self):
        self.ensure_one()
        blockers = super()._reset_detect_blockers()
        blockers += self._reset_stock_revert_blockers(
            self.sudo().move_raw_ids | self.sudo().move_finished_ids)
        return blockers

    def _reset_execute(self):
        self.ensure_one()
        if self.state != 'done':
            raise UserError(_("Only done manufacturing orders can be reset (%(name)s is %(state)s).",
                              name=self.display_name, state=self.state))
        self._reset_delete_moves(self.sudo().move_raw_ids | self.sudo().move_finished_ids)
        self.sudo().workorder_ids.action_cancel()
        self.sudo().write({'state': 'draft', 'qty_producing': 0})
        self._reset_post_chatter([_("Movements and valuation removed; stock reverted.")])
