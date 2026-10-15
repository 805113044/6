from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from booking.models import Room


class Command(BaseCommand):
    help = "仅在 DEBUG=true 时幂等创建示例教室；不创建默认账号。"

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("生产环境禁止载入示例数据，请在管理后台录入真实教室。")
        for name, building, floor, capacity, room_type, equipment in [
            ("A101", "致远楼", 1, 60, "lecture", "激光投影 · 无线麦克风 · 空调"),
            ("A203", "致远楼", 2, 30, "seminar", "交互白板 · 讨论桌 · 空调"),
            ("B305", "求实楼", 3, 48, "lab", "计算机 · 投影仪 · 实验工作台"),
            ("A302", "致远楼", 3, 120, "lecture", "双屏投影 · 扩音系统 · 空调"),
            ("B201", "求实楼", 2, 24, "seminar", "白板 · 圆桌 · 无线投屏"),
            ("C102", "明德楼", 1, 80, "lecture", "高清投影 · 讲台 · 空调"),
        ]:
            Room.objects.get_or_create(
                name=name,
                defaults={
                    "building": building,
                    "floor": floor,
                    "capacity": capacity,
                    "room_type": room_type,
                    "equipment": equipment,
                },
            )
        self.stdout.write(self.style.SUCCESS("示例教室已就绪；未创建任何默认账号。"))
