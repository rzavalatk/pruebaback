# -*- encoding: utf-8 -*-
from openerp import models, fields, api, _
from openerp.exceptions import except_orm, Warning, RedirectWarning


class AccountInvoiceLine(models.Model):
    _inherit = 'account.invoice.line'

    precio_id = fields.Many2one("lista.precios.producto", "Lista de Precio")

    @api.onchange("precio_id")
    def onchangedescuento(self):
        if self.precio_id:
            self.price_unit = self.precio_id.precio

    @api.onchange("price_unit", "product_id")
    def validatepreciocosto(self):
        if self.product_id:
            if self.price_unit < self.product_id.list_price:
                raise Warning(_('No esta permitido establecer precios de ventas por debajo del precio de lista'))

    @api.model
    def create(self, values):
        line = super(AccountInvoiceLine, self).create(values)
        if line.price_unit < line.product_id.list_price:
            raise Warning(_('No esta permitido establecer precios de ventas por debajo del precio de lista -- verifque este producto %s') % (line.product_id.name))

        return line


