# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class ResetToDraftWizard(models.TransientModel):
    _name = 'reset.to.draft.wizard'
    _description = 'Reset to Draft Wizard'

    res_model = fields.Char(readonly=True)
    res_ids = fields.Char(readonly=True)
    target_display = fields.Char(string="Documents", readonly=True)
    is_blocked = fields.Boolean(readonly=True)
    line_ids = fields.One2many('reset.to.draft.wizard.line', 'wizard_id', readonly=True)
    blocker_ids = fields.One2many('reset.to.draft.wizard.blocker', 'wizard_id', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        model = ctx.get('active_model')
        ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
        if not model or not ids:
            return res
        if not isinstance(self.env[model], self.env.registry['reset.to.draft.mixin']):
            raise UserError(_("%(model)s does not support Reset to Draft.", model=model))
        targets = self.env[model].browse(ids).exists()

        line_cmds, blocker_cmds = [], []
        for target in targets:
            chain = target._reset_collect_chain()
            for op in target._reset_describe_chain(chain):
                line_cmds.append((0, 0, op))
            for blk in target._reset_detect_blockers():
                blocker_cmds.append((0, 0, {
                    'document': blk['record'].display_name if blk.get('record') else blk.get('document', ''),
                    'reason': blk['reason'],
                }))

        res.update({
            'res_model': model,
            'res_ids': ','.join(str(i) for i in targets.ids),
            'target_display': ', '.join(targets.mapped('display_name')),
            'line_ids': line_cmds,
            'blocker_ids': blocker_cmds,
            'is_blocked': bool(blocker_cmds),
        })
        return res

    # Model -> reset huquqini beruvchi guruhlar (view/action'lardagi bilan bir xil).
    _RESET_GROUPS = {
        'sale.order': ('reset_to_draft.group_reset_to_draft_sale',),
        'purchase.order': ('reset_to_draft.group_reset_to_draft_purchase',),
    }
    _RESET_GROUPS_DEFAULT = ('reset_to_draft.group_reset_to_draft_sale',
                             'reset_to_draft.group_reset_to_draft_purchase')

    def _check_reset_access(self, model):
        groups = self._RESET_GROUPS.get(model, self._RESET_GROUPS_DEFAULT)
        if not any(self.env.user.has_group(group) for group in groups):
            raise AccessError(_("You are not allowed to reset %(model)s documents to draft.",
                                model=self.env[model]._description))

    def action_confirm_reset(self):
        self.ensure_one()
        if self.is_blocked:
            raise UserError(_("Resolve all blockers before resetting."))
        if not self.res_model or not self.res_ids:
            raise UserError(_("Nothing to reset."))
        self._check_reset_access(self.res_model)
        targets = self.env[self.res_model].browse(
            [int(i) for i in self.res_ids.split(',')]).exists()
        try:
            with self.env.cr.savepoint():
                for target in targets:
                    blockers = target._reset_detect_blockers()
                    if blockers:
                        raise UserError(_("A new blocker appeared on %(doc)s: %(reason)s",
                                          doc=target.display_name, reason=blockers[0]['reason']))
                    target._reset_execute()
        except UserError:
            raise
        except Exception as error:
            _logger.exception("reset_to_draft execution failed")
            raise UserError(_("Reset failed and was fully rolled back: %s", error))
        return {'type': 'ir.actions.act_window_close'}


class ResetToDraftWizardLine(models.TransientModel):
    _name = 'reset.to.draft.wizard.line'
    _description = 'Reset to Draft Wizard Line'

    wizard_id = fields.Many2one('reset.to.draft.wizard', ondelete='cascade')
    document = fields.Char(readonly=True)
    operation = fields.Char(readonly=True)


class ResetToDraftWizardBlocker(models.TransientModel):
    _name = 'reset.to.draft.wizard.blocker'
    _description = 'Reset to Draft Wizard Blocker'

    wizard_id = fields.Many2one('reset.to.draft.wizard', ondelete='cascade')
    document = fields.Char(readonly=True)
    reason = fields.Char(readonly=True)
