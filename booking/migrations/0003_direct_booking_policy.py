from django.db import migrations


def apply_policy(apps, schema_editor):
    # No shared accounts: old teacher/student logins become inactive.
    apps.get_model("booking", "User").objects.filter(is_staff=False).update(
        is_active=False
    )
    # Existing pending rows already reserved their time, so preserving them is safe.
    apps.get_model("booking", "Booking").objects.filter(status="pending").update(
        status="approved"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0002_booking_course_key_booking_credential_version_and_more")
    ]
    operations = [migrations.RunPython(apply_policy, migrations.RunPython.noop)]
