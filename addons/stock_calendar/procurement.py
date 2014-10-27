from datetime import datetime
from dateutil.relativedelta import relativedelta
from openerp.osv import fields, osv
from openerp.tools.translate import _
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT
from openerp import SUPERUSER_ID
from psycopg2 import OperationalError
import pytz
#from profilehooks import profile

class procurement_group(osv.osv):
    _inherit = 'procurement.group'

    _columns = {
        'propagate_to_purchase': fields.boolean('Propagate grouping to purchase order',
                                                help="When from a procurement belonging to this procurement group a purchase order is made, purchase orders will be grouped by this procurement group"),
        'next_delivery_date': fields.datetime('Next Delivery Date',
                                              help="The date of the next delivery for this procurement group, when this group is on the purchase calendar of the orderpoint"),
        'next_purchase_date': fields.datetime('Next Purchase Date',
                                              help="The date the next purchase order should be sent to the supplier"),
        }


class purchase_order(osv.osv):
    _inherit = "purchase.order"
    _columns = {
        'group_id': fields.many2one('procurement.group', string="Procurement Group"),
    }


class procurement_order(osv.osv):
    _inherit = 'procurement.order'


    # TODO: Check those autocommit things
    def run(self, cr, uid, ids, autocommit=False, context=None):
        procs = self.browse(cr, uid, ids, context=context)
        to_assign = [x for x in procs if x.state not in ('running', 'done')]
        for assign in to_assign:
            self._assign(cr, uid, assign, context=context)
        buy_ids = [x.id for x in procs if x.rule_id and x.rule_id.action == 'buy']
        if buy_ids:
            self.make_po(cr, uid, buy_ids, context=context)
            self.write(cr, uid, buy_ids, {'state': 'running'}, context={'tracking_disable': True})
        set_others = set(ids) - set(buy_ids)
        return super(procurement_order, self).run(cr, uid, list(set_others), context=context)

    def assign_group_date(self, cr, uid, ids, context=None):
        orderpoint_obj = self.pool.get("stock.warehouse.orderpoint")
        group_obj = self.pool.get("procurement.group")
        for procurement in self.browse(cr, uid, ids, context=context):
            ops = orderpoint_obj.search(cr, uid, [('location_id', '=', procurement.location_id.id),
                                                  ('product_id', '=', procurement.product_id.id)], context=context)
            if ops and ops[0]:
                orderpoint = orderpoint_obj.browse(cr, uid, ops[0], context=context)
                date_planned = datetime.strptime(procurement.date_planned, DEFAULT_SERVER_DATETIME_FORMAT)
                purchase_date, delivery_date = self._get_previous_dates(cr, uid, orderpoint, date_planned, context=context)
                if purchase_date and delivery_date:
                    group = group_obj.create(cr, uid, {'propagate_to_purchase': True,
                                           'next_delivery_date': self._convert_to_UTC(cr, uid, delivery_date, context=context).strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                                           'next_purchase_date': self._convert_to_UTC(cr, uid, purchase_date, context=context).strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                                           'name': procurement.name}, context=context)
                    self.write(cr, uid, [procurement.id], {'group_id': group}, context=context)

    def _get_purchase_order_date(self, cr, uid, procurement, company, schedule_date, context=None):
        """Return the datetime value to use as Order Date (``date_order``) for the
           Purchase Order created to satisfy the given procurement.

           :param browse_record procurement: the procurement for which a PO will be created.
           :param browse_report company: the company to which the new PO will belong to.
           :param datetime schedule_date: desired Scheduled Date for the Purchase Order lines.
           :rtype: datetime
           :return: the desired Order Date for the PO
        """
        calendar_obj = self.pool.get('resource.calendar')
        if procurement.orderpoint_id.purchase_calendar_id:
            now_date = self._convert_to_tz(cr, uid, datetime.utcnow(), context=context)
            res = calendar_obj._schedule_days(cr, uid, procurement.orderpoint_id.purchase_calendar_id.id, 1, now_date, compute_leaves=True, context=context)
            if res:
                return res[0][0]
        seller_delay = int(procurement.product_id.seller_delay)
        return schedule_date - relativedelta(days=seller_delay)

    def _get_orderpoint_date_planned(self, cr, uid, orderpoint, start_date, context=None):
        date_planned = start_date + relativedelta(days=orderpoint.product_id.seller_delay)
        return date_planned.strftime(DEFAULT_SERVER_DATE_FORMAT)

    def _prepare_orderpoint_procurement(self, cr, uid, orderpoint, product_qty, date=False, group=False, context=None):
        return {
            'name': orderpoint.name,
            'date_planned': date or self._get_orderpoint_date_planned(cr, uid, orderpoint, datetime.today(), context=context),
            'product_id': orderpoint.product_id.id,
            'product_qty': product_qty,
            'company_id': orderpoint.company_id.id,
            'product_uom': orderpoint.product_uom.id,
            'location_id': orderpoint.location_id.id,
            'origin': orderpoint.name,
            'warehouse_id': orderpoint.warehouse_id.id,
            'orderpoint_id': orderpoint.id,
            'group_id': group or orderpoint.group_id.id,
            }


    def _get_previous_dates(self, cr, uid, orderpoint, start_date = False, context=None):
        """
        Date should be given in utc
        """
        calendar_obj = self.pool.get('resource.calendar')
        att_obj = self.pool.get('resource.calendar.attendance')
        context = context or {}
        context['no_round_hours'] = True
        # Date should be converted to the correct timezone
        start_date = self._convert_to_tz(cr, uid, start_date, context=context)
        # First check if the orderpoint has a Calendar as it should be delivered at this calendar date
        purchase_date = False
        delivery_date = start_date
        if orderpoint.calendar_id:
            res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, -1, start_date, compute_leaves=True, context=context)
            if res and res[0][0] < start_date:
                group_to_find = res[0][2] and att_obj.browse(cr, uid, res[0][2], context=context).group_id.id or False
                delivery_date = res[0][0]
                found_date = delivery_date
                if orderpoint.purchase_calendar_id:
                    while not purchase_date:
                        found_date = found_date + relativedelta(days=-1) # won't allow to deliver within the day
                        res = calendar_obj._schedule_days(cr, uid, orderpoint.purchase_calendar_id.id, -1, found_date, compute_leaves=True, context=context)
                        for re in res:
                            group = re[2] and att_obj.browse(cr, uid, re[2], context=context).group_id.id or False
                            found_date = re[0]
                            if not purchase_date and (group_to_find and group_to_find == group or (not group_to_find)):
                                purchase_date = re[0]
        else:
            delivery_date = start_date or datetime.utcnow()
        return purchase_date, delivery_date

    def _get_next_dates(self, cr, uid, orderpoint, new_date=False, group=False, context=None):
        calendar_obj = self.pool.get('resource.calendar')
        att_obj = self.pool.get('resource.calendar.attendance')
        context = context or {}
        context['no_round_hours'] = True
        if not new_date:
            new_date = self._convert_to_tz(cr, uid, datetime.utcnow(), context=context)
        now_date = self._convert_to_tz(cr, uid, datetime.utcnow(), context=context)

        # Search first calendar day (without group)
        res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
        att_group = res and res[0][2] and att_obj.browse(cr, uid, res[0][2], context=context).group_id.id or False
        #If hours are smaller than the current date, search a day further
        #TODO: maybe there could be stuff at the same day
        if res and res[0][0] < now_date:
            new_date = res[0][1] + relativedelta(days=1)
            res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
            if res and res[0][2]:
                att_group = att_obj.browse(cr, uid, res[0][2], context=context).group_id.id
            else:
                att_group = False

        # If you can find an entry and you have a group to match, but it does not, search further until you find one that corresponds
        number = 0
        while res and group and att_group != group and number < 100:
            number += 1
            new_date = res[0][1] + relativedelta(days=1)
            res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
            att_group = False
            if res and res[0][2]:
                att_group = att_obj.browse(cr, uid, res[0][2], context=context).group_id.id
            else:
                att_group = False
        #number as safety pall for endless loops
        if number >= 100:
            res = False

        # If you found a solution(first date), you need the second date until the next delivery because you need to deliver
        # everything needed until the second date on the first date
        if res:
            date1 = res[0][1]
            new_date = res[0][1] + relativedelta(days=1)
            res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
            if res:
                return (date1, res[0][1])
        return (False, False)

    def _product_virtual_get(self, cr, uid, order_point, context=None):
        product_obj = self.pool.get('product.product')
        ctx = context.copy()
        ctx.update({'location': order_point.location_id.id})
        return product_obj._product_available(cr, uid,
                                              [order_point.product_id.id],
                                              context=ctx)[order_point.product_id.id]['virtual_available']

    def _convert_to_tz(self, cr, uid, date, context=None):
        if not context or not context.get('tz'):
            return date
        utc_date = pytz.UTC.localize(date)
        timezone = pytz.timezone(context['tz'])
        return utc_date.astimezone(timezone)

    def _convert_to_UTC(self, cr, uid, date, context=None):
        """
            The date should be timezone aware
        """
        if not context or not context.get('tz'):
            return date
        return date.astimezone(pytz.UTC)

    def _get_group(self, cr, uid, orderpoint, context=None):
        """
            Will return the groups and the end dates of the intervals of the purchase calendar
            that need to be executed now.
            If a purchase calendar is defined, it should give the
            :return [(date, group)]
        """
        #Check if orderpoint has last execution date and calculate if we need to calculate again already
        calendar_obj = self.pool.get("resource.calendar")
        att_obj = self.pool.get("resource.calendar.attendance")
        group = False
        context = context or {}
        context['no_round_hours'] = True
        date = False
        now_date = self._convert_to_tz(cr, uid, datetime.utcnow(), context=context)
        res_intervals = []
        if orderpoint.purchase_calendar_id:
            if orderpoint.last_execution_date:
                new_date = datetime.strptime(orderpoint.last_execution_date, DEFAULT_SERVER_DATETIME_FORMAT)
            else:
                new_date = datetime.utcnow()
            # Convert to timezone of user
            new_date = self._convert_to_tz(cr, uid, new_date, context=context)
            intervals = calendar_obj._schedule_days(cr, uid, orderpoint.purchase_calendar_id.id, 1, new_date, compute_leaves=True, context=context)
            for interval in intervals:
                # If last execution date, interval should start after it in order not to execute the same orderpoint twice
                # TODO: Make the interval a little bigger
                if (orderpoint.last_execution_date and (interval[0] > new_date and interval[0] < now_date and interval[1] > now_date)) or (not orderpoint.last_execution_date and interval[0] < now_date and interval[1] > now_date):
                    group = att_obj.browse(cr, uid, interval[2], context=context).group_id.id
                    date = interval[1]
                    res_intervals += [(date, group), ]
        else:
            return [(now_date, None)]
        return res_intervals

    #@profile(immediate=True)
    def _procure_orderpoint_confirm(self, cr, uid, use_new_cursor=False, company_id=False, context=None):
        '''
        Create procurement based on Orderpoint

        :param bool use_new_cursor: if set, use a dedicated cursor and auto-commit after processing each procurement.
            This is appropriate for batch jobs only.
        '''
        if context is None:
            context = {}
        ctx_chat = context.copy()
        ctx_chat.update({'mail_create_nolog': True, 'tracking_disable': True, 'mail_create_nosubscribe': True})
        if use_new_cursor:
            cr = openerp.registry(cr.dbname).cursor()
        orderpoint_obj = self.pool.get('stock.warehouse.orderpoint')
        procurement_obj = self.pool.get('procurement.order')
        product_obj = self.pool.get('product.product')

        dom = company_id and [('company_id', '=', company_id)] or []
        orderpoint_ids = orderpoint_obj.search(cr, uid, dom, order="location_id, purchase_calendar_id, calendar_id")
        prev_ids = []
        tot_procs = []
        while orderpoint_ids:
            ids = orderpoint_ids[:1000]
            del orderpoint_ids[:1000]
            dates_dict = {}
            product_dict = {}
            ops_dict = {}
            ops = orderpoint_obj.browse(cr, uid, ids, context=context)

            #Calculate groups that can be executed together
            for op in ops:
                key = (op.location_id.id, op.purchase_calendar_id.id, op.calendar_id.id)
                res_groups=[]
                if not dates_dict.get(key):
                    date_groups = self._get_group(cr, uid, op, context=context)
                    for date, group in date_groups:
                        if op.calendar_id:
                            date1, date2 = self._get_next_dates(cr, uid, op, date, group, context=context)
                            res_groups += [(group, date1, date2, date)] #date1/date2 as deliveries and date as purchase confirmation date
                        else:
                            res_groups += [(group, date, False, date)]
                    dates_dict[key] = res_groups
                    product_dict[key] = [op.product_id]
                    ops_dict[key] = [op]
                else:
                    product_dict[key] += [op.product_id]
                    ops_dict[key] += [op]

            for key in product_dict.keys():
                for res_group in dates_dict[key]:
                    ctx = context.copy()
                    ctx.update({'location': ops_dict[key][0].location_id.id})
                    if res_group[2]:
                        ctx.update({'to_date': res_group[2].strftime(DEFAULT_SERVER_DATETIME_FORMAT)})
                    prod_qty = product_obj._product_available(cr, uid, [x.id for x in product_dict[key]],
                                                              context=ctx)
                    group = res_group[0]
                    date = res_group[1]
                    subtract_qty = orderpoint_obj.subtract_procurements_from_orderpoints(cr, uid, [x.id for x in ops_dict[key]], context=context)
                    first_op = True
                    for op in ops_dict[key]:
                        try:
                            prods = prod_qty[op.product_id.id]['virtual_available']
                            if prods is None:
                                continue
                            if prods <= op.product_min_qty:
                                qty = max(op.product_min_qty, op.product_max_qty) - prods
                                reste = op.qty_multiple > 0 and qty % op.qty_multiple or 0.0
                                if reste > 0:
                                    qty += op.qty_multiple - reste

                                if qty < 0:
                                    continue

                                qty -= subtract_qty[op.id]

                                if qty >= 0:
                                    ndelivery = self._convert_to_UTC(cr, uid, date, context=context)
                                    proc_id = procurement_obj.create(cr, uid,
                                                                     self._prepare_orderpoint_procurement(cr, uid, op, qty, date=ndelivery, group=group, context=context),
                                                                     context=ctx_chat)
                                    tot_procs.append(proc_id)
                                    if group and date and res_group[3] and first_op:
                                        npurchase = self._convert_to_UTC(cr, uid, res_group[3], context=context)
                                        first_op = False
                                        self.pool.get("procurement.group").write(cr, uid, [group], {'next_purchase_date': npurchase, 'next_delivery_date': ndelivery}, context=context)
                                    orderpoint_obj.write(cr, uid, [op.id], {'last_execution_date': datetime.utcnow().strftime(DEFAULT_SERVER_DATETIME_FORMAT)}, context=context)
                                if use_new_cursor:
                                    cr.commit()
                        except OperationalError:
                            if use_new_cursor:
                                orderpoint_ids.append(op.id)
                                cr.rollback()
                                continue
                            else:
                                raise
            try:
                self.run(cr, uid, tot_procs, context=context)
                tot_procs = []
                if use_new_cursor:
                    cr.commit()
            except OperationalError:
                if use_new_cursor:
                    cr.rollback()
                    continue
                else:
                    raise


            if use_new_cursor:
                cr.commit()
            if prev_ids == ids:
                break
            else:
                prev_ids = ids

        if use_new_cursor:
            cr.commit()
            cr.close()
        return {}


    #@profile(immediate=True)
    def _get_po_line_values_from_procs(self, cr, uid, procurements, partner, schedule_date, context=None):
        res = {}
        if context is None:
            context = {}
        uom_obj = self.pool.get('product.uom')
        pricelist_obj = self.pool.get('product.pricelist')
        prod_obj = self.pool.get('product.product')
        acc_pos_obj = self.pool.get('account.fiscal.position')

        pricelist_id = partner.property_product_pricelist_purchase.id
        prices_qty = []
        for procurement in procurements:
            seller_qty = procurement.product_id.seller_qty
            uom_id = procurement.product_id.uom_po_id.id
            qty = uom_obj._compute_qty(cr, uid, procurement.product_uom.id, procurement.product_qty, uom_id)
            if seller_qty:
                qty = max(qty, seller_qty)
            prices_qty += [(procurement.product_id, qty, partner)]
        prices = pricelist_obj.price_get_multi(cr, uid, [pricelist_id], prices_qty)

        #Passing partner_id to context for purchase order line integrity of Line name
        new_context = context.copy()
        new_context.update({'lang': partner.lang, 'partner_id': partner.id})
        names = prod_obj.name_get(cr, uid, [x.product_id.id for x in procurements], context=context)
        names_dict = {}
        for id, name in names:
            names_dict[id] = name
        for procurement in procurements:
            taxes_ids = procurement.product_id.supplier_taxes_id
            taxes = acc_pos_obj.map_tax(cr, uid, partner.property_account_position, taxes_ids)
            name = names_dict[procurement.product_id.id]
            if procurement.product_id.description_purchase:
                name += '\n' + procurement.product_id.description_purchase
            price = prices[procurement.product_id.id][pricelist_id]

            values = {
                'name': name,
                'product_qty': qty,
                'product_id': procurement.product_id.id,
                'product_uom': procurement.product_id.uom_po_id.id,
                'price_unit': price or 0.0,
                'date_planned': schedule_date.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                'taxes_id': [(6, 0, taxes)],
                'procurement_ids': [(4, procurement.id)]
                }
            res[procurement.id] = values
        return res


    def _get_grouping_dicts(self, cr, uid, ids, context=None):
        """
        It will group the procurements according to the pos they should go into.  That way, lines going to the same
        po, can be processed at once.
        Returns two dictionaries:
        add_purchase_dicts: key: po value: procs to add to the po
        create_purchase_dicts: key: values for proc to create (not that necessary as they are in procurement => TODO),
                                values: procs to add
        """
        po_obj = self.pool.get('purchase.order')
        # Regroup POs
        cr.execute("""
            SELECT psi.name, p.id, pr.id, pr.picking_type_id, p.location_id, p.partner_dest_id, p.company_id, p.group_id,
            pg.propagate_to_purchase, psi.qty
             FROM procurement_order AS p
                LEFT JOIN procurement_rule AS pr ON pr.id = p.rule_id
                LEFT JOIN procurement_group AS pg ON p.group_id = pg.id,
            product_supplierinfo AS psi, product_product AS pp
            WHERE
             p.product_id = pp.id AND p.id in %s AND psi.product_tmpl_id = pp.product_tmpl_id
             ORDER BY psi.sequence,
                psi.name, p.rule_id, p.location_id, p.company_id, p.partner_dest_id, p.group_id
        """, (tuple(ids), ))
        res = cr.fetchall()
        old = False
        # A giant dict for grouping lines, ... to do at once
        create_purchase_procs = {} # Lines to add to a newly to create po
        add_purchase_procs = {} # Lines to add/adjust in an existing po
        proc_seller = {} # To check we only process one po
        for partner, proc, rule, pick_type, location, partner_dest, company, group, propagate_to_purchase, qty in res:
            if not proc_seller.get(proc):
                proc_seller[proc] = partner
                new = partner, rule, pick_type, location, company, group, propagate_to_purchase
                if new != old:
                    old = new
                    available_draft_po = False
                    dom = [
                        ('partner_id', '=', partner), ('state', '=', 'draft'), ('picking_type_id', '=', pick_type),
                        ('location_id', '=', location), ('company_id', '=', company), ('dest_address_id', '=', partner_dest)]
                    if group and propagate_to_purchase:
                        dom += [('group_id', '=', group)]
                    available_draft_po_ids = po_obj.search(cr, uid, dom, context=context)
                    available_draft_po = available_draft_po_ids and available_draft_po_ids[0] or False
                # Add to dictionary
                if available_draft_po:
                    if add_purchase_procs.get(available_draft_po):
                        add_purchase_procs[available_draft_po] += [proc]
                    else:
                        add_purchase_procs[available_draft_po] = [proc]
                else:
                    if create_purchase_procs.get(new):
                        create_purchase_procs[new] += [proc]
                    else:
                        create_purchase_procs[new] = [proc]
        return add_purchase_procs, create_purchase_procs

    def make_po(self, cr, uid, ids, context=None):
        res = {}
        company = self.pool.get('res.users').browse(cr, uid, uid, context=context).company_id
        po_obj = self.pool.get('purchase.order')
        po_line_obj = self.pool.get('purchase.order.line')
        seq_obj = self.pool.get('ir.sequence')
        uom_obj = self.pool.get('product.uom')
        add_purchase_procs, create_purchase_procs = self._get_grouping_dicts(cr, uid, ids, context=context)

        # Let us check existing purchase orders and add/adjust lines on them
        for add_purchase in add_purchase_procs.keys():
            po = po_obj.browse(cr, uid, add_purchase, context=context)
            lines_to_update = {}
            line_values = []
            procurements = self.browse(cr, uid, add_purchase_procs[add_purchase], context=context)
            po_line_ids = po_line_obj.search(cr, uid, [('order_id', '=', add_purchase), ('product_id', 'in', [x.product_id.id for x in procurements])], context=context)
            po_lines = po_line_obj.browse(cr, uid, po_line_ids, context=context)
            po_prod_dict = {}
            for po in po_lines:
                po_prod_dict[po.product_id.id] = po
            procs_to_create = []
            #Check which procurements need a new line and which need to be added to an existing one
            for proc in procurements:
                if po_prod_dict.get(proc.product_id.id):
                    po_line = po_prod_dict[proc.product_id.id]
                    uom_id = po_line.product_uom #Convert to UoM of existing line
                    qty = uom_obj._compute_qty_obj(cr, uid, proc.product_uom, proc.product_qty, uom_id)
                    if lines_to_update.get(po_line):
                        lines_to_update[po_line] += [(proc.id, qty)]
                    else:
                        lines_to_update[po_line] = [(proc.id, qty)]
                else:
                    procs_to_create.append(proc)

            procs = []
            # Update the quantities of the lines that need to
            for line in lines_to_update.keys():
                tot_qty = 0
                for proc, qty in lines_to_update[line]:
                    tot_qty += qty
                    procs += [proc]
                line_values += [(1, line.id, {'product_qty': line.product_qty + tot_qty, 'procurement_ids': [(4, x[0]) for x in lines_to_update[line]]})]
            #if procs:
            #    print procs
            #    self.message_post(cr, uid, procs, body=_("Quantity added in existing Purchase Order Line"), context=context)

            # Create lines for which no line exists yet
            if procs_to_create:
                partner = po.partner_id
                schedule_date = po.minimum_planned_date
                value_lines = self._get_po_line_values_from_procs(cr, uid, procs_to_create, partner, schedule_date, context=context)
                line_values += [(0, 0, value_lines[x]) for x in value_lines.keys()]
                # self.message_post(cr, uid, [x.id for x in procs_to_create], body=_("Purchase line created and linked to an existing Purchase Order"), context=context)
            po_obj.write(cr, uid, [add_purchase], {'order_line': line_values},context=context)

        # Create new purchase orders
        partner_obj = self.pool.get("res.partner")
        new_pos = []
        procs = []
        for create_purchase in create_purchase_procs.keys():
            line_values = []
            procs += create_purchase_procs[create_purchase]
            procurements = self.browse(cr, uid, create_purchase_procs[create_purchase], context=context)
            partner = partner_obj.browse(cr, uid, create_purchase[0], context=context)

            #Create purchase order itself:
            procurement = procurements[0]
            if procurement.group_id and procurement.group_id.propagate_to_purchase:
                next_deliv_date = procurement.group_id.next_delivery_date
                schedule_date = datetime.strptime(next_deliv_date, DEFAULT_SERVER_DATETIME_FORMAT)
            else:
                schedule_date = self._get_purchase_schedule_date(cr, uid, procurement, procurement.company_id, context=context)

            if procurement.group_id and procurement.group_id.next_purchase_date:
                purchase_date = datetime.strptime(procurement.group_id.next_purchase_date, DEFAULT_SERVER_DATETIME_FORMAT)
            else:
                purchase_date = self._get_purchase_order_date(cr, uid, procurement, procurement.company_id, schedule_date, context=context)

            value_lines = self._get_po_line_values_from_procs(cr, uid, procurements, partner, schedule_date, context=context)
            line_values += [(0, 0, value_lines[x]) for x in value_lines.keys()]
            name = seq_obj.get(cr, uid, 'purchase.order') or _('PO: %s') % procurement.name
            po_vals = {
                'name': name,
                'origin': procurement.origin,
                'partner_id': create_purchase[0],
                'location_id': procurement.location_id.id,
                'picking_type_id': procurement.rule_id.picking_type_id.id,
                'pricelist_id': partner.property_product_pricelist_purchase.id,
                'date_order': purchase_date.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                'company_id': procurement.company_id.id,
                'fiscal_position': partner.property_account_position.id,
                'payment_term_id': partner.property_supplier_payment_term.id,
                'dest_address_id': procurement.partner_dest_id.id,
                'group_id': (procurement.group_id and procurement.group_id.propagate_to_purchase and procurement.group_id.id) or False,
                'order_line': line_values,
                }
            new_po = po_obj.create(cr, uid, po_vals, context=context)
            new_pos.append(new_po)
#        if procs:
#            self.message_post(cr, uid, procs, body=_("Draft Purchase Order created"), context=context)
        return dict.fromkeys(ids, True)



class stock_warehouse_orderpoint(osv.osv):
    """
    Defines Minimum stock rules.
    """
    _inherit = "stock.warehouse.orderpoint"

    def subtract_procurements_from_orderpoints(self, cr, uid, orderpoint_ids, context=None):
        '''This function returns quantity of product that needs to be deducted from the orderpoint computed quantity because there's already a procurement created with aim to fulfill it.
        '''

        cr.execute("""select op.id, p.id, p.product_uom, p.product_qty, pt.uom_id, sm.product_qty from procurement_order as p left join stock_move as sm ON sm.procurement_id = p.id,
                                    stock_warehouse_orderpoint op, product_product pp, product_template pt
                                WHERE p.orderpoint_id = op.id AND p.state not in ('done', 'cancel') AND sm.state not in ('draft')
                                AND pp.id = p.product_id AND pp.product_tmpl_id = pt.id
                                AND op.id IN %s
                                ORDER BY op.id, p.id
                    """, (tuple(orderpoint_ids),))
        results = cr.fetchall()
        current_proc = False
        current_op = False
        uom_obj = self.pool.get("product.uom")
        op_qty = 0
        res = dict.fromkeys(orderpoint_ids, 0.0)
        for move_result in results:
            op = move_result[0]
            if current_op != op:
                if current_op:
                    res[current_op] = op_qty
                current_op = op
                op_qty = 0
            proc = move_result[1]
            if proc != current_proc:
                op_qty += uom_obj._compute_qty(cr, uid, move_result[2], move_result[3], move_result[4], round=False)
                current_proc = proc
            if move_result[5]: #If a move is associated (is move qty)
                op_qty -= move_result[5]
        return res
