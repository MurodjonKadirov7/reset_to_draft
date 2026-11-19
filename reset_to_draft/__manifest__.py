# -*- coding: utf-8 -*-
{
    'name': (
        "Reset to Draft | Cancel Done Sale, Purchase, Delivery & Manufacturing "
        "Orders"
    ),
    'version': '20.0.1.2.5',
    'category': 'Inventory/Inventory',
    'summary': (
        "Undo a done Sales, Purchase, Transfer or Manufacturing document back to "
        "draft: stock moves, valuation and journal entries removed, stock "
        "reverted. Reset to draft, cancel sale order, cancel purchase order, "
        "cancel delivery order, cancel picking, cancel done transfer, cancel "
        "manufacturing order, reverse stock valuation, undo validated transfer."
    ),
    'description': """
Reset to Draft
==============

A wrong document was confirmed and everything behind it is already done: the
goods moved, the valuation was posted, the invoice was created. In standard
Odoo there is no way back — you cancel, reverse and live with the noise.

This module adds a **Reset to Draft** button that takes the whole chain apart
and puts the document back to draft, as if it had never been confirmed.

What it undoes
--------------
* Stock moves and their move lines, with the on-hand quantity (``stock.quant``)
  put back to what it was before.
* The valuation layer (``product.value``) and the stock journal entries.
* The linked invoice or vendor bill, with the payment reconciliation released
  first — the payment itself stays posted and simply becomes outstanding again.
* Analytic lines of the moves, and the now-empty transfer documents.
* Pending (not yet done) transfers are cancelled and removed, so the order does
  not come back to draft with deliveries still hanging on it.

Before anything happens
-----------------------
A wizard lists, document by document, exactly what will be removed, and a
separate tab lists the **blockers** — the reasons a reset is refused:

* a journal entry inside a locked period, or one protected by an inalterable hash;
* an invoice or bill shared with another order;
* goods that have already moved on, so reverting would push stock negative;
* a validated landed cost on the transfer;
* a linked manufacturing order that must be reset first;
* a multi-lot / serial manufacturing order.

The whole reset runs inside a single savepoint: any error rolls everything
back. There is no half-reset state.

Where it appears
----------------
* Sales Order form and list (confirmed orders)
* Purchase Order form and list (confirmed orders)
* Transfer form and list (done transfers)
* Manufacturing Order form and list (done orders)

Access
------
Two groups, ``Reset to Draft — Sales`` and ``Reset to Draft — Purchase``, under
their own privilege. Nobody else sees the button, and the wizard re-checks the
group on confirmation, so the action cannot be reached through a crafted call.

Deleting posted accounting entries is a deliberate deviation from the usual
reversal: this is a tool for correcting mistakes in a controlled way, and it is
guarded by group, lock date and hash.
""",
    'author': 'todoo.uz',
    'website': 'https://todoo.uz',
    'support': 'info@todoo.uz',
    'license': 'OPL-1',
    'images': ['static/description/banner.png'],
    'depends': [
        'sale',
        'sale_stock',
        'sale_mrp',
        'purchase',
        'purchase_stock',
        'stock',
        'stock_account',
        'mrp',
        'account',
        'mail',
    ],
    'data': [
        'security/reset_to_draft_security.xml',
        'security/ir.access.csv',
        'wizard/reset_to_draft_wizard_views.xml',
        'views/reset_to_draft_buttons.xml',
        'views/reset_to_draft_actions.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
