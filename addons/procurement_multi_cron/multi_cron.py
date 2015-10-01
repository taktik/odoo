# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import time
from psycopg2 import OperationalError

from openerp import SUPERUSER_ID, tools
from openerp.osv import fields, osv
from openerp.tools.config import config
import logging
from openerp.tools.translate import _
from openerp.api import Environment
import openerp
_logger = logging.getLogger(__name__)


class company_lock(osv.Model):
    _name='company.lock'
    _columns = {
        'company_id': fields.many2one('res.company', 'Company'),
        'last_execution_date': fields.datetime("Last Execution Date"),
    }


class procurement_order(osv.Model):
    _inherit='procurement.order'

    def run_scheduler_lock(self, cr, uid, company_id, use_new_cursor=False, context=None):
        #with Environment.manage():
        new_cr = self.pool.cursor()
        lock_obj = self.pool.get('company.lock')
        companies = lock_obj.search(cr, uid, [('company_id', '=', company_id)], context=context)
        if not companies:
            lock_obj.create(cr, uid, {'company_id': company_id}, context=context)
        try:
            with tools.mute_logger('openerp.sql_db'):
                cr.execute("SELECT id FROM company_lock WHERE company_id = %s FOR UPDATE NOWAIT", (company_id,))
        except Exception:
            _logger.info('Attempt to run procurement scheduler aborted, as already running')
            new_cr.rollback()
            new_cr.close()
            return {}

        #If we don't have a lock on one of these records:
        print context
        self.run_scheduler(new_cr, uid, use_new_cursor=True, company_id=company_id, context=context)
        new_cr.close()
        return {}

    def run_scheduler_parts(self, cr, uid, modulo_coeff, context=None):
        company_obj = self.pool.get("res.company")
        company_ids = company_obj.search(cr, uid, [], context=context)
        nbcronworkers = config.options['max_cron_threads']
        result_comps = [ item for i,item in enumerate(company_ids) if i % nbcronworkers==modulo_coeff ]
        for company in result_comps:
            self.run_scheduler_lock(cr, uid, company, True, context=context)
        return {}



