from datetime import datetime
from dateutil.relativedelta import relativedelta
from openerp.osv import fields, osv
from openerp.tools.translate import _
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT
from openerp import SUPERUSER_ID
from psycopg2 import OperationalError

class procurement_group(osv.osv):
    _inherit = 'procurement.group'

    _columns = {
        'propagate_to_purchase': fields.boolean('Propagate grouping to purchase order',
                                                help="When from a procurement belonging to this procurement group a purchase order is made, purchase orders will be grouped by this procurement group"),
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
            res = calendar_obj._schedule_days(cr, uid, procurement.orderpoint_id.purchase_calendar_id.id, 1, datetime.utcnow(), compute_leaves=True, context=context)
            return res[0][0]
        seller_delay = int(procurement.product_id.seller_delay)
        return schedule_date - relativedelta(days=seller_delay)

    def _get_orderpoint_date_planned(self, cr, uid, orderpoint, start_date, context=None):
        date_planned = start_date
        if orderpoint.calendar_id:
            date_planned, date2 = self._get_next_dates(cr, uid, orderpoint)
        return date_planned.strftime(DEFAULT_SERVER_DATE_FORMAT)

    def _prepare_orderpoint_procurement(self, cr, uid, orderpoint, product_qty, group=False, context=None):
        return {
            'name': orderpoint.name,
            'date_planned': self._get_orderpoint_date_planned(cr, uid, orderpoint, datetime.today(), context=context),
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

    def _get_next_dates(self, cr, uid, orderpoint, context=None):
        calendar_obj = self.pool.get('resource.calendar')
        att_obj = self.pool.get('resource.calendar.attendance')
        execute, group, new_date = self._get_group(cr, uid, orderpoint, context=context)
        context = context or {}
        context['no_round_hours'] = True
        if not new_date:
            new_date = datetime.utcnow()
        res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
        if res and res[0][0] < datetime.utcnow():
            new_date = res[0][1] + relativedelta(days=1)
            res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
        att_group = False
        if res:
            att_group = att_obj.browse(cr, uid, res[0][2], context=context).group_id.id
        if res and group and att_group != group:
            new_date = res[0][1] + relativedelta(days=1)
            res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
            att_group = False
            if res:
                att_group = att_obj.browse(cr, uid, res[0][2], context=context).group_id.id
            #number as safety pall for endless loops
            number = 0
            while res != False and att_group != group or number > 100:
                number += 1
                new_date = res[0][1] + relativedelta(days=1)
                res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
                att_group = False
                if res:
                    att_group = att_obj.browse(cr, uid, res[0][2], context=context).group_id.id
            if number > 100:
                res = False
        if res:
            date1 = res[0][1]
            new_date = res[0][1] + relativedelta(days=1)
            res = calendar_obj._schedule_days(cr, uid, orderpoint.calendar_id.id, 1, new_date, compute_leaves=True, context=context)
            if res:
                return (date1, res[0][1])
            else:
                return (False, False)
        else:
            return (False, False)

    def _product_virtual_get(self, cr, uid, order_point):
        product_obj = self.pool.get('product.product')
        ctx={'location': order_point.location_id.id}
        if order_point.calendar_id:
            date1, date2 = self._get_next_dates(cr, uid, order_point)
            if not date1 or not date2:
                return 0.0
            ctx.update({'to_date': date2.strftime(DEFAULT_SERVER_DATETIME_FORMAT)})
        return product_obj._product_available(cr, uid,
                [order_point.product_id.id],
                context=ctx)[order_point.product_id.id]['virtual_available']

    def _get_group(self, cr, uid, orderpoint, context=None):
        """
            Will check if the orderpoint needs to be executed and if yes, will also return group and date to start from
            :return execute, group
        """
        #Check if orderpoint has last execution date and calculate if we need to calculate again already
        calendar_obj = self.pool.get("resource.calendar")
        att_obj = self.pool.get("resource.calendar.attendance")
        execute = False
        group = False
        context = context or {}
        context['no_round_hours'] = True
        date = False
        #TODO: Two cases are more or less similar
        if orderpoint.purchase_calendar_id:
            if orderpoint.last_execution_date:
                new_date = datetime.strptime(orderpoint.last_execution_date, DEFAULT_SERVER_DATETIME_FORMAT)
            else:
                new_date = datetime.utcnow()
            intervals = calendar_obj._schedule_days(cr, uid, orderpoint.purchase_calendar_id.id, 1, new_date, compute_leaves=True, context=context)
            for interval in intervals:
                # If last execution date, interval should start after it in order not to execute the same orderpoint twice
                if (orderpoint.last_execution_date and (interval[0] > new_date and interval[0] < datetime.utcnow() and interval[1] > datetime.utcnow())) or (not orderpoint.last_execution_date and interval[0] < new_date and interval[1] > new_date):
                    execute = True
                    group = att_obj.browse(cr, uid, interval[2], context=context).group_id.id
                    date = interval[1]
        else:
            execute = True
        return execute, group, date

    def _procure_orderpoint_confirm(self, cr, uid, use_new_cursor=False, company_id = False, context=None):
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
        orderpoint_ids = orderpoint_obj.search(cr, uid, dom)
        prev_ids = []
        while orderpoint_ids:
            ids = orderpoint_ids[:100]
            del orderpoint_ids[:100]
            for op in orderpoint_obj.browse(cr, uid, ids, context=context):
                try:
                    execute, group, new_date = self._get_group(cr, uid, op, context=context)
                    if not execute:
                        continue
                    prods = self._product_virtual_get(cr, uid, op)
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
                                                             self._prepare_orderpoint_procurement(cr, uid, op, qty, group = group, context=context),
                                                             context=context)
                            self.check(cr, uid, [proc_id])
                            self.run(cr, uid, [proc_id])
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
                schedule_date = self._get_purchase_schedule_date(cr, uid, procurement, company, context=context)
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