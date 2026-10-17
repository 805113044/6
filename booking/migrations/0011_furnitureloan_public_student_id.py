# New registrations may explicitly agree to publish their student ID.
# Historical V2/V3 rows are NOT retroactively published.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('booking', '0010_furniture_stock_returns')]
    operations = [
        migrations.AddField(
            model_name='furnitureloan',
            name='public_student_id',
            field=models.BooleanField(default=False),
        ),
    ]
