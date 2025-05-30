import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    args = '[time in seconds to loop]'
    help = 'A simple command that simply sleeps for the specified duration'

    def create_parser(self, prog_name, subcommand):
        parser = super().create_parser(prog_name, subcommand)
        parser.add_argument('target_time')
        self.add_arguments(parser)
        return parser

    def handle(self, target_time, **options):
        start_time = time.time()
        target_time = float(target_time)

        print(f"Sleeping for {target_time} seconds...")
        time.sleep(target_time)

        end_time = time.time()
        print(f"Job ran for {end_time - start_time} seconds")
