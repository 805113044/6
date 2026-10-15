from django.core.management.base import BaseCommand
from booking.timetable import sync_timetables


class Command(BaseCommand):
    help = "按学期单双周规则展开未来可预约窗口内的教学占用。"

    def handle(self, *args, **options):
        sync_timetables()
        self.stdout.write("课表已同步。")
