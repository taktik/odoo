# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import datetime
from openerp.exceptions import AccessError

##############################################################################
#
#    OLD API
#
##############################################################################
from openerp.osv import osv, fields


class res_partner(osv.Model):
    _inherit = 'res.partner'

    #
    # add related fields to test them
    #
    _columns = {
        # a regular one
        'related_company_partner_id': fields.related(
            'company_id', 'partner_id', type='many2one', obj='res.partner'),
        # a related field with a single field
        'single_related_company_id': fields.related(
            'company_id', type='many2one', obj='res.company'),
        # a related field with a single field that is also a related field!
        'related_related_company_id': fields.related(
            'single_related_company_id', type='many2one', obj='res.company'),
    }


class TestFunctionCounter(osv.Model):
    _name = 'test_old_api.function_counter'

    def _compute_cnt(self, cr, uid, ids, fname, arg, context=None):
        res = {}
        for cnt in self.browse(cr, uid, ids, context=context):
            res[cnt.id] = cnt.access and cnt.cnt + 1 or 0
        return res

    _columns = {
        'access': fields.datetime('Datetime Field'),
        'cnt': fields.function(
            _compute_cnt, type='integer', string='Function Field', store=True),
    }


class TestFunctionNoInfiniteRecursion(osv.Model):
    _name = 'test_old_api.function_noinfiniterecursion'

    def _compute_f1(self, cr, uid, ids, fname, arg, context=None):
        res = {}
        for tf in self.browse(cr, uid, ids, context=context):
            res[tf.id] = 'create' in tf.f0 and 'create' or 'write'
        cntobj = self.pool['test_old_api.function_counter']
        cnt_id = self.pool['ir.model.data'].xmlid_to_res_id(
            cr, uid, 'test_new_api.c1')
        cntobj.write(
            cr, uid, cnt_id, {'access': datetime.datetime.now()},
            context=context)
        return res

    _columns = {
        'f0': fields.char('Char Field'),
        'f1': fields.function(
            _compute_f1, type='char', string='Function Field', store=True),
    }

##############################################################################
#
#    NEW API
#
##############################################################################
from openerp import models, fields, api, _


class Category(models.Model):
    _name = 'test_new_api.category'

    name = fields.Char(required=True)
    parent = fields.Many2one('test_new_api.category')
    display_name = fields.Char(compute='_compute_display_name', inverse='_inverse_display_name')
    dummy = fields.Char(store=False)
    discussions = fields.Many2many('test_new_api.discussion', 'test_new_api_discussion_category',
                                   'category', 'discussion')

    @api.one
    @api.depends('name', 'parent.display_name')     # this definition is recursive
    def _compute_display_name(self):
        if self.parent:
            self.display_name = self.parent.display_name + ' / ' + self.name
        else:
            self.display_name = self.name

    @api.one
    def _inverse_display_name(self):
        names = self.display_name.split('/')
        # determine sequence of categories
        categories = []
        for name in names[:-1]:
            category = self.search([('name', 'ilike', name.strip())])
            categories.append(category[0])
        categories.append(self)
        # assign parents following sequence
        for parent, child in zip(categories, categories[1:]):
            if parent and child:
                child.parent = parent
        # assign name of last category, and reassign display_name (to normalize it)
        self.name = names[-1].strip()

    @api.multi
    def read(self, fields=None, load='_classic_read'):
        if self.search_count([('id', 'in', self._ids), ('name', '=', 'NOACCESS')]):
            raise AccessError('Sorry')
        return super(Category, self).read(fields=fields, load=load)

class Discussion(models.Model):
    _name = 'test_new_api.discussion'

    name = fields.Char(string='Title', required=True,
        help="General description of what this discussion is about.")
    moderator = fields.Many2one('res.users')
    categories = fields.Many2many('test_new_api.category',
        'test_new_api_discussion_category', 'discussion', 'category')
    participants = fields.Many2many('res.users')
    messages = fields.One2many('test_new_api.message', 'discussion')
    message_concat = fields.Text(string='Message concatenate')

    @api.onchange('moderator')
    def _onchange_moderator(self):
        self.participants |= self.moderator

    @api.onchange('messages')
    def _onchange_messages(self):
        self.message_concat = "\n".join(["%s:%s" % (m.name, m.body) for m in self.messages])


class Message(models.Model):
    _name = 'test_new_api.message'

    discussion = fields.Many2one('test_new_api.discussion', ondelete='cascade')
    body = fields.Text()
    author = fields.Many2one('res.users', default=lambda self: self.env.user)
    name = fields.Char(string='Title', compute='_compute_name', store=True)
    display_name = fields.Char(string='Abstract', compute='_compute_display_name')
    size = fields.Integer(compute='_compute_size', search='_search_size')
    double_size = fields.Integer(compute='_compute_double_size')
    discussion_name = fields.Char(related='discussion.name')

    @api.one
    @api.constrains('author', 'discussion')
    def _check_author(self):
        if self.discussion and self.author not in self.discussion.participants:
            raise ValueError(_("Author must be among the discussion participants."))

    @api.one
    @api.depends('author.name', 'discussion.name')
    def _compute_name(self):
        self.name = "[%s] %s" % (self.discussion.name or '', self.author.name or '')

    @api.one
    @api.depends('author.name', 'discussion.name', 'body')
    def _compute_display_name(self):
        stuff = "[%s] %s: %s" % (self.author.name, self.discussion.name or '', self.body or '')
        self.display_name = stuff[:80]

    @api.one
    @api.depends('body')
    def _compute_size(self):
        self.size = len(self.body or '')

    def _search_size(self, operator, value):
        if operator not in ('=', '!=', '<', '<=', '>', '>=', 'in', 'not in'):
            return []
        # retrieve all the messages that match with a specific SQL query
        query = """SELECT id FROM "%s" WHERE char_length("body") %s %%s""" % \
                (self._table, operator)
        self.env.cr.execute(query, (value,))
        ids = [t[0] for t in self.env.cr.fetchall()]
        return [('id', 'in', ids)]

    @api.one
    @api.depends('size')
    def _compute_double_size(self):
        # This illustrates a subtle situation: self.double_size depends on
        # self.size. When size is computed, self.size is assigned, which should
        # normally invalidate self.double_size. However, this may not happen
        # while self.double_size is being computed: the last statement below
        # would fail, because self.double_size would be undefined.
        self.double_size = 0
        size = self.size
        self.double_size = self.double_size + size


class MixedModel(models.Model):
    _name = 'test_new_api.mixed'

    number = fields.Float(digits=(10, 2), default=3.14)
    date = fields.Date()
    now = fields.Datetime(compute='_compute_now')
    lang = fields.Selection(string='Language', selection='_get_lang')
    reference = fields.Reference(string='Related Document',
        selection='_reference_models')

    @api.one
    def _compute_now(self):
        # this is a non-stored computed field without dependencies
        self.now = fields.Datetime.now()

    @api.model
    def _get_lang(self):
        langs = self.env['res.lang'].search([])
        return [(lang.code, lang.name) for lang in langs]

    @api.model
    def _reference_models(self):
        models = self.env['ir.model'].search([('state', '!=', 'manual')])
        return [(model.model, model.name)
                for model in models
                if not model.model.startswith('ir.')]
