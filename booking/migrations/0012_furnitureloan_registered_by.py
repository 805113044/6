from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('booking', '0011_furnitureloan_public_student_id'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name='furnitureloan',
            name='registered_by',
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='furniture_registrations',
            ),
        ),
    ]
