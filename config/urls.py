from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from booking import views

# System user/permission administration is reserved for superusers.
admin.site.has_permission = lambda request: (
    request.user.is_active and request.user.is_superuser
)
admin.site.site_header = "知室 · 系统管理"
admin.site.site_title = "知室管理"
admin.site.login = views.SchoolLoginView.as_view()
urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", views.SchoolLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "password/",
        auth_views.PasswordChangeView.as_view(
            template_name="registration/password.html", success_url="/password/done/"
        ),
        name="password_change",
    ),
    path(
        "password/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="registration/password_done.html"
        ),
        name="password_change_done",
    ),
    path("", views.rooms, name="rooms"),
    path("schedule/", views.schedule, name="schedule"),
    path("manage/school/", views.school_settings, name="school_settings"),
    path("manage/rooms/", views.manage_rooms, name="manage_rooms"),
    path("manage/rooms/new/", views.room_edit, name="room_create"),
    path("manage/rooms/<int:pk>/", views.room_edit, name="room_edit"),
    path("rooms/<int:pk>/", views.room_detail, name="room_detail"),
    path("bookings/", views.my_bookings, name="my_bookings"),
    path("receipt/", views.receipt, name="receipt"),
    path("lookup/", views.lookup, name="lookup"),
    path("forget/", views.forget_guest, name="forget_guest"),
    path("bookings/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("bookings/<int:pk>/verify/", views.verify_booking, name="verify_booking"),
    path("bookings/<int:pk>/info/", views.booking_info, name="booking_info"),
    path("bookings/<int:pk>/edit/", views.booking_edit, name="booking_edit"),
    path("review/", views.review_queue, name="review"),
    path("review/<int:pk>/", views.review_booking, name="review_booking"),
    path("courses/import/", views.import_courses, name="import_courses"),
    path("courses/template/", views.course_template, name="course_template"),
    path("courses/rules/<int:pk>/stop/", views.stop_timetable, name="stop_timetable"),
    path("reports/", views.reports, name="reports"),
    path("healthz/", views.health, name="health"),
]
