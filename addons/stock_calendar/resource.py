import datetime

from openerp.osv import fields, osv
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT


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
        if current_interval and current_interval[0] < interval[
            1]:  # remove intervals moved outside base interval due to leaves
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
        date = date.strftime(DEFAULT_SERVER_DATETIME_FORMAT)
        res = []
        for att in calendar.attendance_ids:
            if int(att.dayofweek) in weekdays:
                if not ((att.date_from and date < att.date_from) or (att.date_to and date > att.date_to)):
                    res.append(att)
        return res


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
            leaves = self.get_leave_intervals(cr, uid, id, resource_id=resource_id, context=None)

        # filter according to leaves
        for interval in working_intervals:
            work_intervals = self.interval_remove_leaves(interval, leaves)
            intervals += work_intervals

        return intervals


class resource_calendar_attendance(osv.osv):
    _inherit = "resource.calendar.attendance"

    _columns = {
        'date_to': fields.date('End Date'),
        'group_id': fields.many2one('procurement.group', 'Procurement Group'),
    }

