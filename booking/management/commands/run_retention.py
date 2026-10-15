import signal
import threading
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from .purge_expired import purge
from booking.timetable import sync_timetables
import time


class Command(BaseCommand):
    help = "每 60 秒清理过期数据；任何清理失败使进程退出，由容器重启并报警。"

    def handle(self, *args, **options):
        stopped = threading.Event()
        next_sync = 0
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        while not stopped.is_set():
            close_old_connections()
            purge()
            if time.monotonic() >= next_sync:
                sync_timetables()
                next_sync = time.monotonic() + 3600
            Path("/tmp/classroom-retention-heartbeat").touch()
            stopped.wait(60)
