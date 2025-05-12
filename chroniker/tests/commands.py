import sys
import time

from django.core.management.base import BaseCommand


class Sleeper(BaseCommand):
    args = '[time in seconds to loop]'
    help = 'A simple command that simply sleeps for the specified duration'

    def handle(self, target_time, **options):
        start_time = time.time()
        target_time = float(target_time)

        print(f"Sleeping for {target_time} seconds...")
        time.sleep(target_time)

        end_time = time.time()
        print(f"Job ran for {end_time - start_time} seconds")


class InfiniteWaiter(BaseCommand):
    args = ''
    help = 'A simple command that waits indefinitely.'

    def handle(self, *args, **options):
        while 1:
            time.sleep(1)
            print('Waiting...')


class ErrorThrower(BaseCommand):
    args = ''
    help = 'A simple command that always fails.'

    def handle(self, *args, **options):
        print('Something went wrong (but not really, this is just a test).', file=sys.stderr)
