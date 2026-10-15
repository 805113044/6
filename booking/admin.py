from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import AuditEvent, Booking, Room, User


@admin.register(User)
class SchoolUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("学校信息", {"fields": ("display_name", "department")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("学校信息", {"fields": ("display_name", "department", "is_staff")}),
    )
    list_display = (
        "username",
        "display_name",
        "department",
        "is_staff",
        "is_active",
    )
    list_filter = ("is_staff", "is_active", "department")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "campus", "building", "capacity", "department", "is_active")
    list_filter = ("campus", "building", "room_type", "is_active")
    search_fields = ("name", "building")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "room", "kind", "status", "start_at", "end_at")
    list_filter = ("status", "kind")
    exclude = ("lookup_hash",)

    def get_queryset(self, request):
        return super().get_queryset(request).retained()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("booking_id", "actor", "action", "created_at")

    def get_queryset(self, request):
        from datetime import timedelta
        from django.conf import settings
        from django.utils import timezone

        return (
            super()
            .get_queryset(request)
            .filter(
                booking__created_at__gt=timezone.now()
                - timedelta(days=settings.RETENTION_DAYS)
            )
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
