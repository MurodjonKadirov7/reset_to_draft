# -*- coding: utf-8 -*-
from . import models
from . import wizard


def post_init_hook(env):
    admin = env.ref('base.user_admin', raise_if_not_found=False)
    if not admin:
        return
    for xmlid in ('reset_to_draft.group_reset_to_draft_sale',
                  'reset_to_draft.group_reset_to_draft_purchase'):
        group = env.ref(xmlid, raise_if_not_found=False)
        if group:
            group.sudo().user_ids = [(4, admin.id)]
