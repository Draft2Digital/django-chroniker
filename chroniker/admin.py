from django import forms
from django.conf import settings
from django.urls import re_path as url
from django.contrib import admin
from django.core.management import get_commands
from django.urls import reverse, NoReverseMatch
from django.db import models
from django.forms import TextInput
from django.shortcuts import render
from django.utils.encoding import force_str
from django.http import HttpResponseRedirect, Http404, HttpResponse
from django.utils import dateformat, timezone
from django.utils.datastructures import MultiValueDict
from django.utils.formats import get_format
from django.utils.html import format_html
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from chroniker.models import Job, Log, JobDependency, Monitor, CallbackMethod
from chroniker import utils
from chroniker.widgets import ImproveRawIdFieldsFormTabularInline

try:
    from admin_steroids.queryset import ApproxCountQuerySet
except ImportError:
    ApproxCountQuerySet = None

@admin.register(CallbackMethod)
class CallbackMethodAdmin(admin.ModelAdmin):
    list_display = ('name', 'reference')


class JobDependencyInline(ImproveRawIdFieldsFormTabularInline):
    model = JobDependency
    extra = 1
    fk_name = 'dependent'

    readonly_fields = ('criteria_met',)

    raw_id_fields = ('dependee',)


class JobAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.CharField: {
            'widget': TextInput(attrs={
                'size': '100',
            })
        },
    }

    actions = (
        'enable_jobs',
        'disable_jobs',
        'run_selected_jobs',
        'toggle_enabled',
        'clear_stalled',
    )
    list_display = (
        'name',
        'last_run_with_link',
        'get_timeuntil',
        'get_frequency',
        'job_type',
        'hostname',
        'enabled',
        'check_is_complete',
        'is_fresh',
        'last_run_successful',
        'progress_percent_str',
        'estimated_completion_datetime_str',
        'run_button',
        'stop_button',
        'view_logs_button',
    )

    @admin.display(
        description='type'
    )
    def job_type(self, obj=''):
        if not obj:
            return ''
        if obj.is_monitor:
            return 'monitor'
        return 'job'


    readonly_fields = (
        'check_is_complete',
        'view_logs_button',
        'last_run_successful',
        'last_heartbeat',
        'is_fresh',
        'last_run_start_timestamp',
        'last_run',
        'total_parts',
        'total_parts_complete',
        'progress_percent_str',
        'estimated_completion_datetime_str',
        'monitor_records',
        'current_hostname',
        'current_pid',
        'job_type',
        'is_due',
    )
    list_display_links = ('name',)
    list_filter = (
        'frequency',
        'enabled',
        'is_running',
        'last_run_successful',
        'hostname',
        'is_monitor',
    )
    filter_horizontal = ('subscribers','callbacks')
    fieldsets = (
        ('Job Details', {
            'classes': ('wide',),
            'fields': (
                'name',
                'command',
                'args',
                'raw_command',
                'hostname',
                'current_hostname',
                'current_pid',
            )
        }),
        (
            'Status', {
                'classes': ('wide',),
                'fields': (
                    'is_running',
                    'is_due',
                    'is_fresh',
                    'last_run_successful',
                    'total_parts',
                    'total_parts_complete',
                    'progress_percent_str',
                    'estimated_completion_datetime_str',
                    'last_heartbeat',
                    'last_run_start_timestamp',
                    'last_run',
                )
            }
        ),
        ('Flags', {
            'classes': ('wide',),
            'fields': (
                'enabled',
                'force_run',
                'force_stop',
            )
        }),
        ('Logging', {
            'classes': ('wide',),
            'fields': (
                'view_logs_button',
                'log_stdout',
                'log_stderr',
                'maximum_log_entries',
            )
        }),
        ('Monitor', {
            'classes': ('wide',),
            'fields': (
                'is_monitor',
                'monitor_url',
                'monitor_error_template',
                'monitor_description',
                'monitor_records',
            )
        }),
        ('E-mail subscriptions', {
            'classes': ('wide',),
            'fields': (
                'subscribers',
                'email_errors_to_subscribers',
                'email_success_to_subscribers',
            )
        }),
        ('Callbacks', {
            'classes': ('wide',),
            'fields': (
                'callbacks',
                'callback_errors_to_subscribers',
                'callback_success_to_subscribers',
            )
        }),
        ('Frequency options', {
            'classes': ('wide',),
            'fields': (
                'frequency',
                'next_run',
                'params',
                'timeout_seconds',
            )
        }),
    )
    search_fields = (
        'name',
        'hostname',
        'current_hostname',
        'current_pid',
    )

    inlines = (JobDependencyInline,)

    class Media:
        js = ("chroniker/js/dygraph-combined.js",)

    def queryset(self, *args, **kwargs):
        qs = super().queryset(*args, **kwargs)
        if ApproxCountQuerySet is not None:
            qs = qs._clone(klass=ApproxCountQuerySet)
        return qs

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if getattr(settings, 'CHRONIKER_DISABLE_RAW_COMMAND', False):
            fields.append('raw_command')
        return fields

    @admin.display(
        description='Last run',
        ordering='last_run',
    )
    def last_run_with_link(self, obj=None):
        if not obj or not obj.id:
            return ''
        fmt = get_format('DATETIME_FORMAT')
        value = None
        try:
            if obj.last_run is not None:
                value = utils.localtime(obj.last_run)
                value = capfirst(dateformat.format(value, fmt))
            log_id = obj.log_set.latest('run_start_datetime').id
            try:
                # Old way
                u = reverse('chroniker_log_change', args=(log_id,))
            except NoReverseMatch:
                # New way
                u = reverse('admin:chroniker_log_change', args=(log_id,))
            return format_html('<a href="%s">%s</a>' % (u, value))
        except Exception:
            return value


    @admin.display(
        description=_('is complete'),
        boolean=True,
        ordering='is_running',
    )
    def check_is_complete(self, obj=None):
        if not obj or not obj.id:
            return ''
        return not obj.check_is_running()


    @admin.display(
        description=_('next scheduled run'),
        ordering='next_run',
    )
    def get_timeuntil(self, obj=None):
        if not obj or not obj.id or not obj.next_run:
            return ''
        fmt = get_format('DATETIME_FORMAT')
        dt = obj.next_run
        dt = utils.localtime(dt)
        value = capfirst(dateformat.format(dt, fmt))
        return format_html("%s<br /><span class='mini'>(%s)</span>" % (value, obj.get_timeuntil()))


    @admin.display(
        description='Frequency',
        ordering='frequency',
    )
    def get_frequency(self, obj=None):
        if not obj or not obj.id:
            return ''
        freq = capfirst(obj.frequency.lower())
        if obj.params:
            return "%s (%s)" % (freq, obj.params)
        return freq


    @admin.display(
        description='Run'
    )
    def run_button(self, obj=None):
        if not obj or not obj.id:
            return ''
        kwargs = dict(url='%d/run/?inline=1' % obj.id,)
        return format_html('<a href="{url}" class="button">Run</a>'.format(**kwargs))


    @admin.display(
        description='Stop'
    )
    def stop_button(self, obj=None):
        if not obj or not obj.id:
            return ''
        kwargs = dict(url='%d/stop/?inline=1' % obj.id, disabled='')
        if not obj.is_running:
            kwargs['disabled'] = 'disabled'
        s = '<a href="{url}" class="button" {disabled}>Stop</a>'.format(**kwargs)
        return format_html(s)


    @admin.display(
        description='Logs'
    )
    def view_logs_button(self, obj=None):
        if not obj or not obj.id:
            return ''
        q = obj.logs.all()
        kwargs = dict(
            url=utils.get_admin_changelist_url(Log),
            id=obj.id,
            count=q.count(),
        )
        return format_html('<a href="{url}?job__id__exact={id}"' ' target="_blank" class="button">View&nbsp;{count}</a>'.format(**kwargs))


    def run_job_view(self, request, job_id):
        """
        Runs the specified job.
        """
        try:
            Job.objects.filter(id=job_id).update(
                force_run=True,
                force_stop=False,
            )
        except (TypeError, ValueError) as exc:
            raise Http404 from exc
        self.message_user(request, _('Job %(job)s has been signalled to start running immediately.') % {'job': job_id})
        if 'inline' in request.GET:
            redirect = request.path + '../../'
        else:
            redirect = request.GET.get('next', request.path + "../")
        return HttpResponseRedirect(redirect)

    def stop_job_view(self, request, job_id):
        """
        Stop the specified job.
        """
        try:
            Job.objects.filter(id=job_id).update(
                force_run=False,
                force_stop=True,
            )
        except (TypeError, ValueError) as exc:
            raise Http404 from exc
        self.message_user(
            request,
            _('Job %(job)s has been signalled to stop running immediately.') \
                % {'job': job_id})
        if 'inline' in request.GET:
            redirect = request.path + '../../'
        else:
            redirect = request.GET.get('next', request.path + "../")
        return HttpResponseRedirect(redirect)

    def view_duration_graph(self, request, object_id):

        model = self.model
        opts = model._meta
        try:
            object_id = int(object_id)
        except (TypeError, ValueError) as exc:
            raise Http404 from exc
        obj = self.get_object(request, object_id)

        q = None
        max_duration = None
        errors = None
        if obj:
            q = obj.logs.all()
            q = q.order_by('run_start_datetime')
            q = q.only('duration_seconds', 'run_start_datetime')
            max_duration = q.aggregate(models.Max('duration_seconds'))['duration_seconds__max']
            errors = q.filter(success=False)

        media = self.media

        context = {
            'title': _('Change %s') % force_str(opts.verbose_name),
            'object_id': object_id,
            'original': obj,
            'is_popup': False,
            'media': media,
            'app_label': opts.app_label,
            'opts': opts,
            'q': q,
            'errors': errors,
            'max_duration': max_duration,
        }

        return render(request, 'admin/chroniker/job/duration_graph.html', context)

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            url(r'^(.+)/run/$', self.admin_site.admin_view(self.run_job_view), name="chroniker_job_run"),
            url(r'^(.+)/stop/$', self.admin_site.admin_view(self.stop_job_view), name="chroniker_job_stop"),
            url(r'^(.+)/graph/duration/$', self.admin_site.admin_view(self.view_duration_graph), name='chroniker_job_duration_graph'),
        ]
        return my_urls + urls

    @admin.action(
        description="Force run selected jobs"
    )
    def run_selected_jobs(self, request, queryset):
        rows_updated = queryset.update(force_run=True)
        if rows_updated == 1:
            message_bit = "1 job was"
        else:
            message_bit = "%s jobs were" % rows_updated
        self.message_user(request, "%s successfully set to run." % message_bit)


    @admin.action(
        description='Mark the selected stalled %(verbose_name_plural)s as not running'
    )
    def clear_stalled(self, request, queryset):
        reset_count = 0
        for job in queryset:
            if not job.is_fresh():
                reset_count += 1
                job.is_running = False
                job.save()
        self.message_user(request, 'Cleared %i stalled jobs.' % (reset_count,))

    @admin.action(
        description="Toggle enabled flag on selected jobs"
    )
    def toggle_enabled(self, request, queryset):
        for row in queryset:
            row.enabled = not row.enabled
            row.save()
        rows_updated = queryset.count()
        if rows_updated == 1:
            message_bit = "1 job was toggled"
        else:
            message_bit = "%s jobs were toggled" % rows_updated
        self.message_user(request, message_bit)


    @admin.action(
        description="Disable selected jobs"
    )
    def disable_jobs(self, request, queryset):
        queryset.update(enabled=False)
        rows_updated = queryset.count()
        if rows_updated == 1:
            message_bit = "1 job was toggled"
        else:
            message_bit = "%s jobs were toggled" % rows_updated
        self.message_user(request, message_bit)


    @admin.action(
        description="Enable selected jobs"
    )
    def enable_jobs(self, request, queryset):
        queryset.update(enabled=True)
        rows_updated = queryset.count()
        if rows_updated == 1:
            message_bit = "1 job was toggled"
        else:
            message_bit = "%s jobs were toggled" % rows_updated
        self.message_user(request, message_bit)


    def formfield_for_dbfield(self, db_field, **kwargs):
        request = kwargs.pop("request", None)

        # Add a select field of available commands
        if db_field.name == 'command':
            choices_dict = MultiValueDict()
            for command, app in get_commands().items():
                choices_dict.appendlist(app, command)

            choices = []
            for key in choices_dict.keys():
                commands = choices_dict.getlist(key)
                commands.sort()
                choices.append([key, [[c, c] for c in commands]])

            choices.insert(0, ('', '--- None ---'))
            kwargs['widget'] = forms.widgets.Select(choices=choices)
            return db_field.formfield(**kwargs)

        kwargs['request'] = request
        return super().formfield_for_dbfield(db_field, **kwargs)


@admin.register(Log)
class LogAdmin(admin.ModelAdmin):
    list_display = ('job_name', 'run_start_datetime', 'run_end_datetime', 'duration_seconds', 'duration_str', 'job_success', 'on_time', 'hostname')

    list_filter = (
        'success',
        'on_time',
        'job',
    )

    search_fields = (
        'stdout',
        'stderr',
        'job__name',
        'job__command',
    )

    readonly_fields = (
        'run_start_datetime',
        'run_end_datetime',
        'duration_seconds',
        'stdout_long_sample',
        'stderr_long_sample',
        'stdout_link',
        'stderr_link',
        'duration_str',
        'hostname',
    )
    date_hierarchy = 'run_start_datetime'
    fieldsets = (
        (None, {
            'fields': (
                'job',
                'run_start_datetime',
                'run_end_datetime',
                'duration_seconds',
                'duration_str',
                'hostname',
            )
        }),
        ('Output', {
            'fields': (
                'stderr_link',
                'stderr_long_sample',
                'stdout_link',
                'stdout_long_sample',
            )
        }),
    )

    def queryset(self, *args, **kwargs):
        qs = super().queryset(*args, **kwargs)
        qs = qs.only(
            'id',
            'run_start_datetime',
            'run_end_datetime',
            'duration_seconds',
            'success',
            'on_time',
        )
        if ApproxCountQuerySet is not None:
            qs = qs._clone(klass=ApproxCountQuerySet)
        return qs

    @admin.display(
        description='Stdout full'
    )
    def stdout_link(self, obj):
        return format_html('<a href="%s">Download</a>' % (reverse("admin:chroniker_log_stdout", args=(obj.id,)),))


    @admin.display(
        description='Stderr full'
    )
    def stderr_link(self, obj):
        return format_html('<a href="%s">Download</a>' % (reverse("admin:chroniker_log_stderr", args=(obj.id,)),))


    def view_full_stdout(self, request, log_id):
        log = Log.objects.get(id=log_id)
        resp = HttpResponse(log.stdout, content_type='application/x-download')
        resp['Content-Disposition'] = 'filename=log-%s-stdout.txt' % (log_id,)
        return resp

    def view_full_stderr(self, request, log_id):
        log = Log.objects.get(id=log_id)
        resp = HttpResponse(log.stderr, content_type='application/x-download')
        resp['Content-Disposition'] = 'filename=log-%s-stderr.txt' % (log_id,)
        return resp

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            url(r'^(?P<log_id>[0-9]+)/stdout/?$', self.admin_site.admin_view(self.view_full_stdout), name='chroniker_log_stdout'),
            url(r'^(?P<log_id>[0-9]+)/stderr/?$', self.admin_site.admin_view(self.view_full_stderr), name='chroniker_log_stderr'),
        ]
        return my_urls + urls

    @admin.display(
        description=_('Name')
    )
    def job_name(self, obj):
        return obj.job.name


    @admin.display(
        description=_('OK'),
        boolean=True,
        ordering='success',
    )
    def job_success(self, obj):
        return obj.success


    def has_add_permission(self, request):
        return False


try:
    admin.site.register(Job, JobAdmin)
except admin.sites.AlreadyRegistered:
    pass



@admin.register(Monitor)
class MonitorAdmin(admin.ModelAdmin):
    list_display = (
        'name_str',
        'status',
        'monitor_records',
        'get_timeuntil',
        'enabled',
        'action_buttons',
    )
    list_filter = ('last_run_successful',)
    readonly_fields = (
        'name_str',
        'monitor_records',
        'action_buttons',
        'status',
        'get_timeuntil',
    )

    @admin.display(
        description=_('next check'),
        ordering='next_run',
    )
    def get_timeuntil(self, obj):
        fmt = get_format('DATETIME_FORMAT')
        next_run = obj.next_run or timezone.now()
        value = capfirst(dateformat.format(utils.localtime(next_run), fmt))
        return format_html("%s<br /><span class='mini'>(%s)</span>" % (value, obj.get_timeuntil()))


    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def queryset(self, request):
        qs = super().queryset(request)
        qs = qs.filter(is_monitor=True)
        qs = qs.order_by('name')
        return qs

    @admin.display(
        description='Name'
    )
    def name_str(self, obj):
        if obj.monitor_url:
            return format_html('<a href="%s" target="_blank">%s</a>' % (obj.monitor_url_rendered, obj.name))
        return obj.name


    @admin.display(
        description='Actions'
    )
    def action_buttons(self, obj):
        buttons = []
        buttons.append('<a href="%s" class="button">Check now</a>' % '%d/run/?inline=1' % obj.id)
        buttons.append('<a href="/chroniker/job/%i/"' 'target="_blank" class="button">Edit</a>' % (obj.id,))
        return format_html(' '.join(buttons))


    def status(self, obj):
        if obj.is_running:
            help_text = 'The monitor is currently being checked.'
            temp = '<img src="' + settings.STATIC_URL + 'admin/img/icon-unknown.svg" alt="%(help_text)s" title="%(help_text)s" />'
        elif obj.last_run_successful:
            help_text = 'All checks passed.'
            temp = '<img src="' + settings.STATIC_URL + 'admin/img/icon-yes.svg" alt="%(help_text)s" title="%(help_text)s" />'
        else:
            help_text = 'Requires attention.'
            temp = '<img src="' + settings.STATIC_URL + 'admin/img/icon-no.svg" alt="%(help_text)s" title="%(help_text)s" />'
        return format_html(temp % dict(help_text=help_text))


    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, extra_context=dict(title='View monitors'))

    def run_job_view(self, request, pk):
        """
        Runs the specified job.
        """
        try:
            job = Job.objects.get(pk=pk)
        except (TypeError, ValueError, Job.DoesNotExist) as exc:
            raise Http404 from exc
        # Rather than actually running the Job right now, we
        # simply force the Job to be run by the next cron job
        job.force_run = True
        job.save()
        self.message_user(request, _('The monitor "%(job)s" will be checked.') % {'job': job})
        if 'inline' in request.GET:
            redirect = request.path + '../../'
        else:
            redirect = request.GET.get('next', request.path + "../")
        return HttpResponseRedirect(redirect)

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            url(r'^(.+)/run/$', self.admin_site.admin_view(self.run_job_view), name="chroniker_job_run"),
        ]
        return my_urls + urls


