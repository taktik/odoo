from datetime import datetime
from dateutil.relativedelta import relativedelta
from openerp.osv import fields, osv
from openerp.tools.translate import _
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT
from openerp import SUPERUSER_ID
from psycopg2 import OperationalError
import pytz

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

    def _procure_orderpoint_confirm(self, cr, uid, use_new_cursor=False, company_id=False, context=None):
        '''
        Create procurement based on Orderpoint

        :param bool use_new_cursor: if set, use a dedicated cursor and auto-commit after processing each procurement.
            This is appropriate for batch jobs only.
        '''
        if context is None:
            context = {}
        if use_new_cursor:
            cr = openerp.registry(cr.dbname).cursor()
        orderpoint_obj = self.pool.get('stock.warehouse.orderpoint')

        procurement_obj = self.pool.get('procurement.order')
        dom = company_id and [('company_id', '=', company_id)] or []
        orderpoint_ids = orderpoint_obj.search(cr, uid, dom, order="location_id, purchase_calendar_id, calendar_id")
        product_obj = self.pool.get('product.product')
        prev_ids = []
        while orderpoint_ids:
            ids = orderpoint_ids[:100]
            del orderpoint_ids[:100]
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
                    for op in ops_dict[key]:
                        try:
                            prods = prod_qty[op.product_id.id]['virtual_available']
                            if prods is None:
                                continue
                            if prods < op.product_min_qty:
                                qty = max(op.product_min_qty, op.product_max_qty) - prods
                                reste = op.qty_multiple > 0 and qty % op.qty_multiple or 0.0
                                if reste > 0:
                                    qty += op.qty_multiple - reste

                                if qty <= 0:
                                    continue

                                qty -= orderpoint_obj.subtract_procurements(cr, uid, op, context=context)

                                if qty > 0:
                                    proc_id = procurement_obj.create(cr, uid,
                                                                     self._prepare_orderpoint_procurement(cr, uid, op, qty, date=self._convert_to_UTC(cr, uid, date, context=context), group=group, context=context),
                                                                     context=context)
                                    if group and date:
                                        npurchase = self._convert_to_UTC(cr, uid, res_group[3], context=context)
                                        ndelivery = self._convert_to_UTC(cr, uid, date1, context=context)
                                        self.pool.get("procurement.group").write(cr, uid, [group], {'next_purchase_date': npurchase, 'next_delivery_date': ndelivery}, context=context)
                                    self.run(cr, uid, [proc_id], context=context)
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

    def make_po(self, cr, uid, ids, context=None):
        """ Resolve the purchase from procurement, which may result in a new PO creation, a new PO line creation or a quantity change on existing PO line.
        Note that some operations (as the PO creation) are made as SUPERUSER because the current user may not have rights to do it (mto product launched by a sale for example)

        @return: dictionary giving for each procurement its related resolving PO line.
        """
        res = {}
        company = self.pool.get('res.users').browse(cr, uid, uid, context=context).company_id
        po_obj = self.pool.get('purchase.order')
        po_line_obj = self.pool.get('purchase.order.line')
        seq_obj = self.pool.get('ir.sequence')
        pass_ids = []
        linked_po_ids = []
        sum_po_line_ids = []
        for procurement in self.browse(cr, uid, ids, context=context):
            partner = self._get_product_supplier(cr, uid, procurement, context=context)
            if not partner:
                self.message_post(cr, uid, [procurement.id], _('There is no supplier associated to product %s') % (procurement.product_id.name))
                res[procurement.id] = False
            else:
                if procurement.group_id and procurement.group_id.next_delivery_date:
                    next_deliv_date = procurement.group_id.next_delivery_date
                    schedule_date = datetime.strptime(next_deliv_date, DEFAULT_SERVER_DATETIME_FORMAT)
                else:
                    schedule_date = self._get_purchase_schedule_date(cr, uid, procurement, company, context=context)
                if procurement.group_id and procurement.group_id.next_purchase_date:
                    purchase_date = datetime.strptime(procurement.group_id.next_purchase_date, DEFAULT_SERVER_DATETIME_FORMAT)
                else:
                    purchase_date = self._get_purchase_order_date(cr, uid, procurement, company, schedule_date, context=context)
                line_vals = self._get_po_line_values_from_proc(cr, uid, procurement, partner, company, schedule_date, context=context)
                dom = [
                    ('partner_id', '=', partner.id), ('state', '=', 'draft'), ('picking_type_id', '=', procurement.rule_id.picking_type_id.id),
                    ('location_id', '=', procurement.location_id.id), ('company_id', '=', procurement.company_id.id), ('dest_address_id', '=', procurement.partner_dest_id.id)]
                if procurement.group_id and procurement.group_id.propagate_to_purchase:
                    dom += [('group_id', '=', procurement.group_id.id)]
                #look for any other draft PO for the same supplier, to attach the new line on instead of creating a new draft one
                available_draft_po_ids = po_obj.search(cr, uid, dom, context=context)
                if available_draft_po_ids:
                    po_id = available_draft_po_ids[0]
                    po_rec = po_obj.browse(cr, uid, po_id, context=context)
                    #if the product has to be ordered earlier those in the existing PO, we replace the purchase date on the order to avoid ordering it too late
                    if datetime.strptime(po_rec.date_order, DEFAULT_SERVER_DATETIME_FORMAT) > purchase_date:
                        po_obj.write(cr, uid, [po_id], {'date_order': purchase_date.strftime(DEFAULT_SERVER_DATETIME_FORMAT)}, context=context)
                    #look for any other PO line in the selected PO with same product and UoM to sum quantities instead of creating a new po line
                    available_po_line_ids = po_line_obj.search(cr, uid, [('order_id', '=', po_id), ('product_id', '=', line_vals['product_id']), ('product_uom', '=', line_vals['product_uom'])], context=context)
                    if available_po_line_ids:
                        po_line = po_line_obj.browse(cr, uid, available_po_line_ids[0], context=context)
                        po_line_obj.write(cr, SUPERUSER_ID, po_line.id, {'product_qty': po_line.product_qty + line_vals['product_qty']}, context=context)
                        po_line_id = po_line.id
                        sum_po_line_ids.append(procurement.id)
                    else:
                        line_vals.update(order_id=po_id)
                        po_line_id = po_line_obj.create(cr, SUPERUSER_ID, line_vals, context=context)
                        linked_po_ids.append(procurement.id)
                else:
                    name = seq_obj.get(cr, uid, 'purchase.order') or _('PO: %s') % procurement.name
                    po_vals = {
                        'name': name,
                        'origin': procurement.origin,
                        'partner_id': partner.id,
                        'location_id': procurement.location_id.id,
                        'picking_type_id': procurement.rule_id.picking_type_id.id,
                        'pricelist_id': partner.property_product_pricelist_purchase.id,
                        'date_order': purchase_date.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                        'company_id': procurement.company_id.id,
                        'fiscal_position': partner.property_account_position and partner.property_account_position.id or False,
                        'payment_term_id': partner.property_supplier_payment_term.id or False,
                        'dest_address_id': procurement.partner_dest_id.id,
                        'group_id': (procurement.group_id and procurement.group_id.propagate_to_purchase and procurement.group_id.id) or False,
                    }
                    po_id = self.create_procurement_purchase_order(cr, SUPERUSER_ID, procurement, po_vals, line_vals, context=context)
                    po_line_id = po_obj.browse(cr, uid, po_id, context=context).order_line[0].id
                    pass_ids.append(procurement.id)
                res[procurement.id] = po_line_id
                self.write(cr, uid, [procurement.id], {'purchase_line_id': po_line_id}, context=context)
        if pass_ids:
            self.message_post(cr, uid, pass_ids, body=_("Draft Purchase Order created"), context=context)
        if linked_po_ids:
            self.message_post(cr, uid, linked_po_ids, body=_("Purchase line created and linked to an existing Purchase Order"), context=context)
        if sum_po_line_ids:
            self.message_post(cr, uid, sum_po_line_ids, body=_("Quantity added in existing Purchase Order Line"), context=context)
        return res