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

import logging
from openerp import SUPERUSER_ID
from openerp.tools.config import config
from openerp.osv import osv, fields
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT
from datetime import datetime

_logger = logging.getLogger(__name__)

class cron_generate(osv.osv_memory):
    _name='cron.generate'
    _description = 'Generate CRONs'

    _columns = {
        'execution_date': fields.datetime("Next execution date", required=True)
    }

    _defaults = {
        'execution_date': datetime.utcnow().strftime(DEFAULT_SERVER_DATETIME_FORMAT)
    }

    def generate(self, cr, uid, ids, context=None):
        for wiz in self.browse(cr, uid, ids, context=context):
            self.generate_cron(cr, uid, ids, wiz.execution_date, context=context)



    def generate_cron(self, cr, uid, ids, date, context=None):
        nbcronworkers = config.options['max_cron_threads']
        ir_cron=self.pool.get('ir.cron')
        # Delete from ir_cron
        existing_crons = ir_cron.search(cr, uid, [('function', '=', 'run_scheduler_parts')], context=context)
        if existing_crons:
            ir_cron.unlink(cr, uid, existing_crons, context=context)

        # Create crons for number of crons
        for cron in range(nbcronworkers):
            ir_cron.create(cr, uid, {'name': 'Run scheduler parts:' + str(cron),
                                     'function': 'run_scheduler_parts',
                                     'nextcall': date,
                                     'args': '(' + str(cron) + ',' + str(context) + ',)',
                                     'interval_type': 'days',
                                     'active': True,
                                     'numbercall': -1,
                                     'user_id': SUPERUSER_ID,
                                     'model': 'procurement.order'}, context=context)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
