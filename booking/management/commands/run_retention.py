import signal
import threading
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from .purge_expired import purge


class Command(BaseCommand):
    help = "每 60 秒清理过期数据；任何清理失败使进程退出，由容器重启并报警。"

    def handle(self, *args, **options):
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        while not stopped.is_set():
            close_old_connections()
            purge()
            Path("/tmp/classroom-retention-heartbeat").touch()
            stopped.wait(60)
