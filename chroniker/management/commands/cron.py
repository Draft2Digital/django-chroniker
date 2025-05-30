import logging
import os
import socket
import sys
import time
from collections import defaultdict
from functools import partial
from multiprocessing import Queue
from optparse import make_option

import psutil

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from chroniker import settings as _settings, utils
from chroniker.models import Job, Log


def kill_stalled_processes(dryrun=True):
    """
    Due to a bug in the Django|Postgres backend, occassionally
    the `manage.py cron` process will hang even through all processes
    have been marked completed.
    We compare all recorded PIDs against those still running,
    and kill any associated with complete jobs.
    """
    pids = set(map(int, Job.objects\
        .filter(is_running=False, current_pid__isnull=False)\
        .exclude(current_pid='')\
        .values_list('current_pid', flat=True)))
    for pid in pids:
        try:
            if utils.pid_exists(pid): # and not utils.get_cpu_usage(pid):
                p = psutil.Process(pid)
                cmd = ' '.join(p.cmdline())
                if 'manage.py cron' in cmd:
                    jobs = Job.objects.filter(current_pid=pid)
                    job = None
                    if jobs:
                        job = jobs[0]
                    utils.smart_print('Killing process %s associated with %s.' % (pid, job))
                    if not dryrun:
                        utils.kill_process(pid)
                else:
                    print('PID not cron.')
            else:
                print('PID dead.')
        except psutil.NoSuchProcess:
            print('PID does not exist.')


class JobProcess(utils.TimedProcess):

    def __init__(self, job, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job


def run_job(job, **kwargs):

    update_heartbeat = kwargs.pop('update_heartbeat', None)
    stdout_queue = kwargs.pop('stdout_queue', None)
    stderr_queue = kwargs.pop('stderr_queue', None)
    force_run = kwargs.pop('force_run', False)

    # TODO:causes UnicodeEncodeError: 'ascii' codec can't encode
    # character u'\xa0' in position 59: ordinal not in range(128)
    #print(u"Running Job: %i - '%s' with args: %s" \
    #    % (job.id, job, job.args))

    # TODO:Fix? Remove multiprocess and just running all jobs serially?
    # Multiprocessing does not play well with Django's PostgreSQL
    # connection, as it seems Django's connection code is not thread-safe.
    # It's a hacky solution, but the short-term fix seems to be to close
    # the connection in this thread, forcing Django to open a new
    # connection unique to this thread.
    # Without this call to connection.close(), we'll get the error
    # "Lost connection to MySQL server during query".
    print('Closing connection.')
    connection.close()
    print('Connection closed.')
    job.run(
        update_heartbeat=update_heartbeat,
        check_running=False,
        stdout_queue=stdout_queue,
        stderr_queue=stderr_queue,
        force_run=force_run,
    )
    #TODO:mark job as not running if still marked?
    #TODO:normalize job termination and cleanup outside of handle_run()?


def run_cron(jobs=None, **kwargs):

    update_heartbeat = kwargs.pop('update_heartbeat', True)
    force_run = kwargs.pop('force_run', False)
    dryrun = kwargs.pop('dryrun', False)
    clear_pid = kwargs.pop('clear_pid', False)
    sync = kwargs.pop('sync', False)

    try:

        # TODO: auto-kill inactive long-running cron processes whose
        # threads have stalled and not exited properly?
        # Check for 0 cpu usage.
        #ps -p <pid> -o %cpu

        stdout_map = defaultdict(list) # {prod_id:[]}
        stderr_map = defaultdict(list) # {prod_id:[]}
        stdout_queue = Queue()
        stderr_queue = Queue()

        if _settings.CHRONIKER_AUTO_END_STALE_JOBS and not dryrun:
            Job.objects.end_all_stale()

        # Check PID file to prevent conflicts with prior executions.
        # TODO: is this still necessary? deprecate? As long as jobs run by
        # JobProcess don't wait for other jobs, multiple instances of cron
        # should be able to run simeltaneously without issue.
        if _settings.CHRONIKER_USE_PID:
            pid_fn = _settings.CHRONIKER_PID_FN
            pid = str(os.getpid())
            any_running = Job.objects.all_running().count()
            if not any_running:
                # If no jobs are running, then even if the PID file exists,
                # it must be stale, so ignore it.
                pass
            elif os.path.isfile(pid_fn):
                try:
                    old_pid = int(open(pid_fn).read())
                    if utils.pid_exists(old_pid):
                        print('%s already exists, exiting' % pid_fn)
                        sys.exit()
                    else:
                        print(('%s already exists, but contains stale ' 'PID, continuing') % pid_fn)
                except ValueError:
                    pass
                except TypeError:
                    pass
            open(pid_fn, 'w').write(pid)
            clear_pid = True

        procs = []
        if force_run:
            q = Job.objects.all()
            if jobs:
                q = q.filter(id__in=jobs)
        else:
            q = Job.objects.due_with_met_dependencies_ordered(jobs=jobs)

        running_ids = set()
        for job in q:

            # This is necessary, otherwise we get the exception
            # DatabaseError: SSL error: sslv3 alert bad record mac
            # even through we're not using SSL...
            # We work around this by forcing Django to use separate
            # connections for each process by explicitly closing the
            # current connection.
            connection.close()

            # Re-check dependencies to incorporate any previous iterations
            # that marked jobs as running, potentially causing dependencies
            # to become unmet.
            Job.objects.update()
            job = Job.objects.get(id=job.id)
            if not force_run and not job.is_due_with_dependencies_met(running_ids=running_ids):
                utils.smart_print('Job {} {} is due but has unmet dependencies.'\
                    .format(job.id, job))
                continue

            # Immediately mark the job as running so the next jobs can
            # update their dependency check.
            utils.smart_print(f'Running job {job.id} {job}.')
            running_ids.add(job.id)
            if dryrun:
                continue
            job.is_running = True
            Job.objects.filter(id=job.id).update(is_running=job.is_running)

            # Launch job.
            if sync:
                # Run job synchronously.
                run_job(
                    job,
                    update_heartbeat=update_heartbeat,
                    stdout_queue=stdout_queue,
                    stderr_queue=stderr_queue,
                    force_run=force_run or job.force_run,
                )
            else:
                # Run job asynchronously.
                job_func = partial(
                    run_job,
                    job=job,
                    force_run=force_run or job.force_run,
                    update_heartbeat=update_heartbeat,
                    name=str(job),
                )
                proc = JobProcess(
                    job=job,
                    max_seconds=job.timeout_seconds,
                    target=job_func,
                    name=str(job),
                    kwargs=dict(
                        stdout_queue=stdout_queue,
                        stderr_queue=stderr_queue,
                    )
                )
                proc.start()
                procs.append(proc)

        if not dryrun:
            print("%d Jobs are due." % len(procs))

            # Wait for all job processes to complete.
            while procs:

                while not stdout_queue.empty():
                    proc_id, proc_stdout = stdout_queue.get()
                    stdout_map[proc_id].append(proc_stdout)

                while not stderr_queue.empty():
                    proc_id, proc_stderr = stderr_queue.get()
                    stderr_map[proc_id].append(proc_stderr)

                for proc in list(procs):

                    if not proc.is_alive():
                        print('Process %s ended.' % (proc,))
                        procs.remove(proc)
                    elif proc.is_expired:
                        print('Process %s expired.' % (proc,))
                        proc_id = proc.pid
                        proc.terminate()
                        run_end_datetime = timezone.now()
                        procs.remove(proc)

                        connection.close()
                        Job.objects.update()
                        j = Job.objects.get(id=proc.job.id)
                        run_start_datetime = j.last_run_start_timestamp
                        proc.job.is_running = False
                        proc.job.force_run = False
                        proc.job.force_stop = False
                        proc.job.save()

                        # Create log record since the job was killed before it had
                        # a chance to do so.
                        Log.objects.create(
                            job=proc.job,
                            run_start_datetime=run_start_datetime,
                            run_end_datetime=run_end_datetime,
                            success=False,
                            on_time=False,
                            hostname=socket.gethostname(),
                            stdout=''.join(stdout_map[proc_id]),
                            stderr=''.join(stderr_map[proc_id] + ['Job exceeded timeout\n']),
                        )

                time.sleep(1)
            print('!' * 80)
            print('All jobs complete!')
    finally:
        if _settings.CHRONIKER_USE_PID and os.path.isfile(pid_fn) and clear_pid:
            os.unlink(pid_fn)


class Command(BaseCommand):
    help = 'Runs all jobs that are due.'
    option_list = getattr(BaseCommand, 'option_list', ()) + (
        make_option('--update_heartbeat',
            dest='update_heartbeat',
            default=1,
            help='If given, launches a thread to asynchronously update ' + \
                'job heartbeat status.'),
        make_option('--force_run',
            dest='force_run',
            action='store_true',
            default=False,
            help='If given, forces all jobs to run.'),
        make_option('--dryrun',
            action='store_true',
            default=False,
            help='If given, only displays jobs to be run.'),
        make_option('--jobs',
            dest='jobs',
            default='',
            help='A comma-delimited list of job ids to limit executions to.'),
        make_option('--name',
            dest='name',
            default='',
            help='A name to give this process.'),
        make_option('--sync',
            action='store_true',
            default=False,
            help='If given, runs jobs one at a time.'),
        make_option('--verbose',
            action='store_true',
            default=False,
            help='If given, shows debugging info.'),
    )

    def add_arguments(self, parser):
        parser.add_argument('--update_heartbeat',
            dest='update_heartbeat',
            default=1,
            help='If given, launches a thread to asynchronously update ' + \
                'job heartbeat status.')
        parser.add_argument('--force_run', dest='force_run', action='store_true', default=False, help='If given, forces all jobs to run.')
        parser.add_argument('--dryrun', action='store_true', default=False, help='If given, only displays jobs to be run.')
        parser.add_argument('--jobs', dest='jobs', default='', help='A comma-delimited list of job ids to limit executions to.')
        parser.add_argument('--name', dest='name', default='', help='A name to give this process.')
        parser.add_argument('--sync', action='store_true', default=False, help='If given, runs jobs one at a time.')
        parser.add_argument('--verbose', action='store_true', default=False, help='If given, shows debugging info.')

    def handle(self, *args, **options):
        verbose = options['verbose']
        if verbose:
            logging.basicConfig(level=logging.DEBUG)

        kill_stalled_processes(dryrun=False)

        # Find specific job ids to run, if any.
        jobs = [int(_.strip()) for _ in options.get('jobs', '').strip().split(',') if _.strip().isdigit()]

        run_cron(
            jobs,
            update_heartbeat=int(options['update_heartbeat']),
            force_run=options['force_run'],
            dryrun=options['dryrun'],
            sync=options['sync'],
        )
