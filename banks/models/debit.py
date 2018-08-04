# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import Warning


class Debit(models.Model):
    _name = 'banks.debit'
    _inherit = ['mail.thread']
    _description = "Management Debits"
    _order = 'date desc'

    def get_sequence(self):
        if self.journal_id:
            for seq in self.journal_id.secuencia_ids:
                if seq.move_type == self.doc_type:
                    return seq.id

    @api.onchange("currency_id")
    def onchangecurrency(self):
        if self.currency_id:
            if self.currency_id != self.company_id.currency_id:
                tasa = self.currency_id.with_context(date=self.date)
                self.currency_rate = 1 / tasa.rate 
                self.es_moneda_base = False
            else:
                self.currency_rate = 1
                self.es_moneda_base = True

    def get_char_seq(self, journal_id, doc_type):
        jr = self.env["account.journal"].search([('id', '=', journal_id)])
        for seq in jr.secuencia_ids:
            if seq.move_type == doc_type:
                return (seq.prefix + '%%0%sd' % seq.padding % seq.number_next_actual)

    def get_msg_number(self):
        if self.journal_id and self.state == 'draft':
            flag = False
            for seq in self.journal_id.secuencia_ids:
                if seq.move_type == self.doc_type:
                    self.number_calc = seq.prefix + '%%0%sd' % seq.padding % seq.number_next_actual
                    flag = True
            if not flag:
                self.msg = "No existe numeración para este banco, verifique la configuración"
                self.number_calc = ""
            else:
                self.msg = ""

    def update_seq(self):
        deb_obj = self.env["banks.debit"].search([('state', '=', 'draft'), ('doc_type', '=', self.doc_type)])
        n = ""
        for seq in self.journal_id.secuencia_ids:
            if seq.move_type == self.doc_type:
                n = seq.prefix + '%%0%sd' % seq.padding % (seq.number_next_actual + 1)
        for db in deb_obj:
            db.write({'number': n})

    @api.model
    def create(self, vals):
        vals["number"] = self.get_char_seq(vals.get("journal_id"), vals.get("doc_type"))
        debit = super(Debit, self).create(vals)
        return debit

    @api.multi
    def unlink(self):
        for move in self:
            if move.state == 'validated':
                raise Warning(_('No puede eliminar registros contabilizados'))
        return super(Debit, self).unlink()

    @api.one
    @api.depends('debit_line.amount', 'total')
    def _compute_rest_credit(self):
        debit_line = 0
        credit_line = 0
        if self.doc_type == 'debit':
            for lines in self.debit_line:
                if lines.move_type == 'debit':
                    debit_line += lines.amount
                elif lines.move_type == 'credit':
                    credit_line += lines.amount
                else:
                    credit_line += 0
                    debit_line += 0
            self.total_debitos = debit_line
            self.total_creditos = credit_line
            self.rest_credit = self.total - (debit_line - credit_line)
        else:
            for lines in self.debit_line:
                if lines.move_type == 'debit':
                    debit_line += lines.amount
                elif lines.move_type == 'credit':
                    credit_line += lines.amount
                else:
                    credit_line += 0
                    debit_line += 0
            self.total_debitos = debit_line
            self.total_creditos = credit_line
            self.rest_credit = round(self.total - (credit_line - debit_line), 2)

    def get_currency(self):
        return self.env.user.company_id.currency_id.id

    currency_id = fields.Many2one("res.currency", "Moneda", domain=[('active', '=', True)])
    journal_id = fields.Many2one("account.journal", "Banco", required=True, domain="[('type', 'in',['bank','cash'])]")
    date = fields.Date(string="Fecha", help="Effective date for accounting entries", required=True)
    total = fields.Float(string='Total', required=True)
    name = fields.Text(string="Descripción", required=True)
    debit_line = fields.One2many("banks.debit.line", "debit_id", "Detalle de debito/credito")
    rate = fields.Float("Tasa de Cambio")
    state = fields.Selection([('draft', 'Borrador'), ('validated', 'Validado'), ('anulated', "Anulado")], string="Estado", default='draft')
    number_calc = fields.Char("Número de Transacción", compute=get_msg_number)
    msg = fields.Char("Error de configuración", compute=get_msg_number)
    rest_credit = fields.Float('Diferencia', compute=_compute_rest_credit)
    move_id = fields.Many2one('account.move', 'Apunte Contable')
    number = fields.Char("Número")
    doc_type = fields.Selection([('debit', 'Débito'), ('credit','Crédito'), ('deposit','Depósito')], string='Tipo', required=True)
    company_id = fields.Many2one("res.company", "Empresa", default=lambda self: self.env.user.company_id, required=True)
    es_moneda_base = fields.Boolean("Es moneda base")
    total_debitos = fields.Float("Total débitos", compute=_compute_rest_credit)
    total_creditos = fields.Float("Total créditos", compute=_compute_rest_credit)

    currency_rate = fields.Float("Tasa de Cambio", digits=(12, 6))

    @api.onchange("journal_id")
    def onchangejournal(self):
        self.get_msg_number()
        if self.journal_id:
            if self.journal_id.currency_id:
                self.currency_id = self.journal_id.currency_id.id
            else:
                self.currency_id = self.company_id.currency_id.id

    @api.multi
    def action_validate(self):
        if not self.number_calc:
            raise Warning(_("El banco no cuenta con configuraciones/parametros para registrar débitos bancarios"))
        if not self.debit_line:
            raise Warning(_("No existen detalles de movimientos a registrar"))
        if self.total < 0:
            raise Warning(_("El total debe de ser mayor que cero"))
        if not round(self.rest_credit, 2) == 0.0:
            raise Warning(_("Existen diferencias entre el detalle y el total de la transacción a realizar"))

        self.write({'state': 'validated'})
        self.number = self.env["ir.sequence"].search([('id', '=', self.get_sequence())]).next_by_id()
        self.write({'move_id': self.generate_asiento()})
        self.update_seq()

    def generate_asiento(self):
        account_move = self.env['account.move']
        lineas = []
        if self.doc_type == 'debit':
            vals_haber = {
                'debit': 0.0,
                'credit': self.total * self.currency_rate,
                'name': self.name,
                'account_id': self.journal_id.default_credit_account_id.id,
                'date': self.date,
            }
            if self.journal_id.currency_id:
                if not self.company_id.currency_id == self.currency_id:
                    vals_haber["currency_id"] = self.currency_id.id
                    vals_haber["amount_currency"] = self.total * -1
                else:
                    vals_haber["amount_currency"] = 0.0
            for line in self.debit_line:
                # LINEA DE DEBITO
                if line.move_type == 'debit':
                    vals_debe = {
                        'debit': line.amount * self.currency_rate,
                        'credit': 0.0,
                        'name': line.name or self.name,
                        'account_id': line.account_id.id,
                        'date': self.date,
                        'partner_id': line.partner_id.id,
                        'analytic_account_id': line.analytic_id.id,
                    }
                    if self.journal_id.currency_id:
                        if not self.company_id.currency_id == self.currency_id:
                            vals_debe["currency_id"] = self.currency_id.id
                            vals_debe["amount_currency"] = line.amount
                        else:
                            vals_debe["amount_currency"] = 0.0
                    lineas.append((0, 0, vals_debe))
                if line.move_type == 'credit':
                    vals_credit = {
                        'debit': 0.0,
                        'credit': line.amount * self.currency_rate,
                        'name': line.name or self.name,
                        'account_id': line.account_id.id,
                        'date': self.date,
                        'partner_id': line.partner_id.id,
                        'analytic_account_id': line.analytic_id.id,
                    }
                    if self.journal_id.currency_id:
                        if not self.company_id.currency_id == self.currency_id:
                            vals_credit["currency_id"] = self.currency_id.id
                            vals_credit["amount_currency"] = line.amount * -1
                        else:
                            vals_credit["amount_currency"] = 0.0
                    lineas.append((0, 0, vals_credit))
            lineas.append((0, 0, vals_haber))
        else:
            vals_credit = {
                'debit': self.total * self.currency_rate,
                'credit': 0.0,
                'name': self.name,
                'account_id': self.journal_id.default_credit_account_id.id,
                'date': self.date,
            }
            if self.journal_id.currency_id:
                if not self.company_id.currency_id == self.currency_id:
                    vals_credit["currency_id"] = self.currency_id.id
                    vals_credit["amount_currency"] = self.total
                else:
                    vals_credit["amount_currency"] = 0.0
            for line in self.debit_line:
                if line.move_type == 'credit':
                    vals_debe = {
                        'debit': 0.0,
                        'credit': line.amount * self.currency_rate,
                        'amount_currency': 0.0,
                        'name': line.name or self.name,
                        'account_id': line.account_id.id,
                        'date': self.date,
                        'partner_id': line.partner_id.id,
                        'analytic_account_id': line.analytic_id.id,
                    }
                    if self.journal_id.currency_id:
                        if not self.company_id.currency_id == self.currency_id:
                            vals_debe["currency_id"] = self.currency_id.id
                            vals_debe["amount_currency"] = line.amount
                        else:
                            vals_debe["amount_currency"] = 0.0
                    lineas.append((0, 0, vals_debe))
                if line.move_type == 'debit':
                    vals_credit = {
                        'debit': line.amount * self.currency_rate,
                        'credit': 0.0,
                        'amount_currency': 0.0,
                        'name': line.name or self.name,
                        'account_id': line.account_id.id,
                        'date': self.date,
                        'partner_id': line.partner_id.id,
                        'analytic_account_id': line.analytic_id.id,
                    }
                    if self.journal_id.currency_id:
                        if not self.company_id.currency_id == self.currency_id:
                            vals_credit["currency_id"] = self.currency_id.id
                            vals_credit["amount_currency"] = line.amount  * -1
                        else:
                            vals_credit["amount_currency"] = 0.0
                    lineas.append((0, 0, vals_credit))
            lineas.append((0, 0, vals_credit))
        values = {
            'journal_id': self.journal_id.id,
            'date': self.date,
            'ref': self.name,
            'line_ids': lineas,
            'state': 'posted',
        }
        id_move = account_move.create(values)
        id_move.write({'name': str(self.number)})
        return id_move.id

    @api.multi
    def action_anulate_debit(self):
        for move in self.move_id:
            move.write({'state': 'draft'})
            move.unlink()
        self.write({'state': 'anulated'})

    @api.multi
    def action_draft(self):
        self.write({'state': 'draft'})

    @api.multi
    def action_anulate(self):
        self.write({'state': 'anulated'})
        self.update_seq()
        self.number = self.env["ir.sequence"].search([('id', '=', self.get_sequence())]).next_by_id()


class Debitline(models.Model):
    _name = 'banks.debit.line'



    @api.onchange("account_id")
    def onchangecuenta(self):
        if self.debit_id.doc_type == 'credit' or self.debit_id.doc_type == 'deposit':
            self.move_type = 'credit'

    debit_id = fields.Many2one('banks.debit', 'Check')
    partner_id = fields.Many2one('res.partner', 'Empresa')
    account_id = fields.Many2one('account.account', 'Cuenta', required=True)
    name = fields.Char('Descripción')
    amount = fields.Float('Monto', required=True)
    currency_id = fields.Many2one('res.currency', string='Currency')
    analytic_id = fields.Many2one("account.analytic.account", string="Cuenta Analitica")
    move_type = fields.Selection([('debit', 'Débito'), ('credit', 'Crédito')], 'Débito/Crédito', default='debit', required=True)
