import datetime

from openerp.osv import fields, osv
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT
from operator import itemgetter

class resource_calendar_leaves(osv.osv):
    _inherit = "resource.calendar.leaves"
    _columns = {
        'group_id': fields.many2one('procurement.group', string="Procurement Group"),
    }

class resource_calendar(osv.osv):
    _inherit = "resource.calendar"

    def _calculate_next_day(self, cr, uid, ids, fields, names, context=None):
        res = {}
        for calend in self.browse(cr, uid, ids, context=context):
            # date1 = self.get_next_day(cr, uid, calend.id, datetime.utcnow() + relativedelta(days = 1))
            _format = '%Y-%m-%d %H:%M:%S'
            sched_date = self.schedule_days_get_date(
                cr, uid, calend.id, 1, day_date=datetime.datetime.utcnow(), compute_leaves=True)
            res[calend.id] = sched_date and sched_date.strftime(_format) or False
        return res

    _columns = {
            'next_day': fields.function(_calculate_next_day, string='Next day it should trigger', type='datetime'),
        }

    def get_leave_intervals(self, cr, uid, id, resource_id=None,
                            start_datetime=None, end_datetime=None,
                            context=None):
        """Get the leaves of the calendar. Leaves can be filtered on the resource,
        the start datetime or the end datetime.

        :param int resource_id: the id of the resource to take into account when
                                computing the leaves. If not set, only general
                                leaves are computed. If set, generic and
                                specific leaves are computed.
        :param datetime start_datetime: if provided, do not take into account leaves
                                        ending before this date.
        :param datetime end_datetime: if provided, do not take into account leaves
                                        beginning after this date.

        :return list leaves: list of tuples (start_datetime, end_datetime) of
                             leave intervals
        """
        resource_calendar = self.browse(cr, uid, id, context=context)
        proc_obj = self.pool.get("procurement.order")
        leaves = []
        for leave in resource_calendar.leave_ids:
            if leave.resource_id and not resource_id == leave.resource_id.id:
                continue
            date_from_db = datetime.datetime.strptime(leave.date_from, DEFAULT_SERVER_DATETIME_FORMAT)
            date_from = proc_obj._convert_to_tz(cr, uid, date_from_db, context=context)
            if end_datetime and date_from > end_datetime:
                continue

            date_to_db = datetime.datetime.strptime(leave.date_to, DEFAULT_SERVER_DATETIME_FORMAT)
            date_to = proc_obj._convert_to_tz(cr, uid, date_to_db, context=context)
            if start_datetime and date_to < start_datetime:
                continue

            leaves.append((date_from, date_to, leave.group_id.id))
        return leaves

    # --------------------------------------------------
    # Utility methods
    # --------------------------------------------------

    def interval_remove_leaves_group(self, cr, uid, interval, leave_intervals, context=None):
        """
            The same as interval_remove_leaves, but take into account the group
        """
        if not interval:
            return interval
        if leave_intervals is None:
            leave_intervals = []
        intervals = []
        #leave_intervals = self.interval_clean(leave_intervals) NOT NECESSARY TO CLEAN HERE AS IT WOULD REMOVE GROUP INFO
        current_interval = list(interval)
        for leave in leave_intervals:
            if len(leave) > 2:
                current_group = False
                att_obj = self.pool.get("resource.calendar.attendance")
                if leave[2]:
                    if len(current_interval) > 2:
                        current_group = current_interval[2] and att_obj.browse(cr, uid, current_interval[2], context=context).group_id.id or False
                    if leave[2] != current_group:
                        continue
            if leave[1] <= current_interval[0]:
                continue
            if leave[0] >= current_interval[1]:
                break

            if current_interval[0] < leave[0] < current_interval[1]:
                current_interval[1] = leave[0]
                intervals.append((current_interval[0], current_interval[1]))
                current_interval = [leave[1], interval[1]]
            # if current_interval[0] <= leave[1] <= current_interval[1]:
            if current_interval[0] <= leave[1]:
                current_interval[0] = leave[1]
        if current_interval and current_interval[0] < interval[1]:  # remove intervals moved outside base interval due to leaves
            if len(interval) > 2:
                intervals.append((current_interval[0], current_interval[1], interval[2]))
            else:
                intervals.append((current_interval[0], current_interval[1],))
        return intervals

    def interval_remove_leaves(self, interval, leave_intervals):
        """ Utility method that remove leave intervals from a base interval:

         - clean the leave intervals, to have an ordered list of not-overlapping
           intervals
         - initiate the current interval to be the base interval
         - for each leave interval:

          - finishing before the current interval: skip, go to next
          - beginning after the current interval: skip and get out of the loop
            because we are outside range (leaves are ordered)
          - beginning within the current interval: close the current interval
            and begin a new current interval that begins at the end of the leave
            interval
          - ending within the current interval: update the current interval begin
            to match the leave interval ending

        :param tuple interval: a tuple (beginning datetime, ending datetime) that
                               is the base interval from which the leave intervals
                               will be removed
        :param list leave_intervals: a list of tuples (beginning datetime, ending datetime)
                                    that are intervals to remove from the base interval
        :return list intervals: a list of tuples (begin datetime, end datetime)
                                that are the remaining valid intervals """
        if not interval:
            return interval
        if leave_intervals is None:
            leave_intervals = []
        intervals = []
        leave_intervals = self.interval_clean(leave_intervals)
        current_interval = [interval[0], interval[1]]
        for leave in leave_intervals:
            if leave[1] <= current_interval[0]:
                continue
            if leave[0] >= current_interval[1]:
                break
            if current_interval[0] < leave[0] < current_interval[1]:
                current_interval[1] = leave[0]
                intervals.append((current_interval[0], current_interval[1]))
                current_interval = [leave[1], interval[1]]
            # if current_interval[0] <= leave[1] <= current_interval[1]:
            if current_interval[0] <= leave[1]:
                current_interval[0] = leave[1]
        if current_interval and current_interval[0] < interval[1]:  # remove intervals moved outside base interval due to leaves
            if len(interval) > 2:
                intervals.append((current_interval[0], current_interval[1], interval[2]))
            else:
                intervals.append((current_interval[0], current_interval[1],))
        return intervals

    def get_attendances_for_weekday_date(self, cr, uid, id, weekdays, date, context=None):
        """
            Different possibilities
        """
        calendar = self.browse(cr, uid, id, context=None)
        res = [att for att in calendar.attendance_ids if int(att.dayofweek) in weekdays]
        date = date.strftime(DEFAULT_SERVER_DATE_FORMAT)
        res = []
        for att in calendar.attendance_ids:
            if int(att.dayofweek) in weekdays:
                if not ((att.date_from and date < att.date_from) or (att.date_to and date > att.date_to)):
                    res.append(att)
        return res

    # --------------------------------------------------
    # Days scheduling
    # --------------------------------------------------

    def _schedule_days(self, cr, uid, id, days, day_date=None, compute_leaves=False,
                       resource_id=None, default_interval=None, context=None):
        """Schedule days of work, using a calendar and an optional resource to
        compute working and leave days. This method can be used backwards, i.e.
        scheduling days before a deadline.

        :param int days: number of days to schedule. Use a negative number to
                         compute a backwards scheduling.
        :param date day_date: reference date to compute working days. If days is > 0
                              date is the starting date. If days is < 0 date is the
                              ending date.
        :param boolean compute_leaves: if set, compute the leaves based on calendar
                                       and resource. Otherwise no leaves are taken
                                       into account.
        :param int resource_id: the id of the resource to take into account when
                                computing the leaves. If not set, only general
                                leaves are computed. If set, generic and
                                specific leaves are computed.
        :param tuple default_interval: if no id, try to return a default working
                                       day using default_interval[0] as beginning
                                       hour, and default_interval[1] as ending hour.
                                       Example: default_interval = (8, 16).
                                       Otherwise, a void list of working intervals
                                       is returned when id is None.

        :return tuple (datetime, intervals): datetime is the beginning/ending date
                                             of the schedulign; intervals are the
                                             working intervals of the scheduling.

        Implementation note: rrule.rrule is not used because rrule it des not seem
        to allow getting back in time.
        """
        if day_date is None:
            day_date = datetime.datetime.now()
        backwards = (days < 0)
        days = abs(days)
        intervals = []
        planned_days = 0
        iterations = 0

        # if backwards:
        #     current_datetime = day_date.replace(hour=23, minute=59, second=59)
        # else:
        current_datetime = day_date.replace(hour=0, minute=0, second=0)

        while planned_days < days and iterations < 100:
            working_intervals = self.get_working_intervals_of_day(
                cr, uid, id, current_datetime,
                compute_leaves=compute_leaves, resource_id=resource_id,
                default_interval=default_interval,
                context=context)
            if id is None or working_intervals:  # no calendar -> no working hours, but day is considered as worked
                planned_days += 1
                intervals += working_intervals
            # get next day
            if backwards:
                current_datetime = self.get_previous_day(cr, uid, id, current_datetime, context)
            else:
                current_datetime = self.get_next_day(cr, uid, id, current_datetime, context)
            # avoid infinite loops
            iterations += 1

        return intervals

    def get_working_intervals_of_day(self, cr, uid, id, start_dt=None, end_dt=None,
                                     leaves=None, compute_leaves=False, resource_id=None,
                                     default_interval=None, context=None):
        """ Get the working intervals of the day based on calendar. This method
        handle leaves that come directly from the leaves parameter or can be computed.

        :param int id: resource.calendar id; take the first one if is a list
        :param datetime start_dt: datetime object that is the beginning hours
                                  for the working intervals computation; any
                                  working interval beginning before start_dt
                                  will be truncated. If not set, set to end_dt
                                  or today() if no end_dt at 00.00.00.
        :param datetime end_dt: datetime object that is the ending hour
                                for the working intervals computation; any
                                working interval ending after end_dt
                                will be truncated. If not set, set to start_dt()
                                at 23.59.59.
        :param list leaves: a list of tuples(start_datetime, end_datetime) that
                            represent leaves.
        :param boolean compute_leaves: if set and if leaves is None, compute the
                                       leaves based on calendar and resource.
                                       If leaves is None and compute_leaves false
                                       no leaves are taken into account.
        :param int resource_id: the id of the resource to take into account when
                                computing the leaves. If not set, only general
                                leaves are computed. If set, generic and
                                specific leaves are computed.
        :param tuple default_interval: if no id, try to return a default working
                                       day using default_interval[0] as beginning
                                       hour, and default_interval[1] as ending hour.
                                       Example: default_interval = (8, 16).
                                       Otherwise, a void list of working intervals
                                       is returned when id is None.

        :return list intervals: a list of tuples (start_datetime, end_datetime)
                                of work intervals """
        if isinstance(id, (list, tuple)):
            id = id[0]

        # Computes start_dt, end_dt (with default values if not set) + off-interval work limits
        work_limits = []
        if start_dt is None and end_dt is not None:
            start_dt = end_dt.replace(hour=0, minute=0, second=0)
        elif start_dt is None:
            start_dt = datetime.datetime.now().replace(hour=0, minute=0, second=0)
        else:
            work_limits.append((start_dt.replace(hour=0, minute=0, second=0), start_dt))
        if end_dt is None:
            end_dt = start_dt.replace(hour=23, minute=59, second=59)
        else:
            work_limits.append((end_dt, end_dt.replace(hour=23, minute=59, second=59)))
        assert start_dt.date() == end_dt.date(), 'get_working_intervals_of_day is restricted to one day'

        intervals = []
        work_dt = start_dt.replace(hour=0, minute=0, second=0)

        # no calendar: try to use the default_interval, then return directly
        if id is None:
            if default_interval:
                working_interval = (start_dt.replace(hour=default_interval[0], minute=0, second=0),
                                    start_dt.replace(hour=default_interval[1], minute=0, second=0))
            intervals = self.interval_remove_leaves(working_interval, work_limits)
            return intervals

        working_intervals = []
        for calendar_working_day in self.get_attendances_for_weekday_date(cr, uid, id, [start_dt.weekday()], start_dt,
                                                                          context):
            if context and context.get('no_round_hours'):
                min_from = int((calendar_working_day.hour_from - int(calendar_working_day.hour_from)) * 60)
                min_to = int((calendar_working_day.hour_to - int(calendar_working_day.hour_to)) * 60)
                working_interval = (
                    work_dt.replace(hour=int(calendar_working_day.hour_from), minute=min_from),
                    work_dt.replace(hour=int(calendar_working_day.hour_to), minute=min_to),
                    calendar_working_day.id,
                )
            else:
                working_interval = (
                    work_dt.replace(hour=int(calendar_working_day.hour_from)),
                    work_dt.replace(hour=int(calendar_working_day.hour_to)),
                    calendar_working_day.id,
                )
            working_intervals += self.interval_remove_leaves(working_interval, work_limits)

        # find leave intervals
        if leaves is None and compute_leaves:
            leaves = self.get_leave_intervals(cr, uid, id, resource_id=resource_id, context=context)

        # filter according to leaves
        for interval in working_intervals:
            work_intervals = self.interval_remove_leaves_group(cr, uid, interval, leaves, context=context)
            intervals += work_intervals

        return intervals


class resource_calendar_attendance(osv.osv):
    _inherit = "resource.calendar.attendance"

    _columns = {
        'date_to': fields.date('End Date'),
        'group_id': fields.many2one('procurement.group', 'Procurement Group'),
    }

