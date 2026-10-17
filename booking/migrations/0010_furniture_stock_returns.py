from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def carry_forward_loans(apps, schema_editor):
    """Old V2 loans had no inventory totals. Preserve owed quantities, not fictitious stock."""
    from collections import defaultdict
    from datetime import timedelta
    from django.utils import timezone
    Loan = apps.get_model('booking', 'FurnitureLoan')
    Stock = apps.get_model('booking', 'FurnitureStock')
    cutoff = timezone.now() - timedelta(days=settings.RETENTION_DAYS)
    amounts = defaultdict(int)
    for loan in Loan.objects.filter(returned_at__isnull=True, created_at__gt=cutoff).iterator():
        for item in loan.items or []:
            name = str(item.get('name', '')).strip()
            try:
                quantity = int(item.get('quantity', 0))
            except (TypeError, ValueError):
                continue
            if name and 0 < quantity <= 100000:
                amounts[name] += quantity
    for name, borrowed in amounts.items():
        # Set total == already borrowed so administrators explicitly configure true stock.
        Stock.objects.get_or_create(name=name[:60], defaults={
            'total_count': borrowed, 'loaned_count': borrowed, 'is_active': True
        })


class Migration(migrations.Migration):
    dependencies = [('booking', '0009_furnitureloan')]
    operations = [
        migrations.AlterField(model_name='booking', name='status',
            field=models.CharField('状态', max_length=12, default='pending', choices=[
                ('pending','待审批'),('approved','已批准'),('rejected','已驳回'),
                ('cancelled','已取消'),('returned','已退回'),('completed','已归还')
            ])),
        migrations.CreateModel(name='FurnitureStock', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('name', models.CharField(max_length=60, unique=True, verbose_name='物品名称')),
            ('total_count', models.PositiveIntegerField(default=0, verbose_name='库存总数')),
            ('loaned_count', models.PositiveIntegerField(default=0, verbose_name='未归还数量')),
            ('is_active', models.BooleanField(default=True, verbose_name='可借用')),
        ], options={'ordering': ['name'], 'verbose_name': '桌椅库存', 'verbose_name_plural': '桌椅库存'}),
        migrations.AddField(model_name='furnitureloan', name='public_consent', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='furnitureloan', name='return_code_hash', field=models.CharField(max_length=128, blank=True, default='', editable=False)),
        migrations.AddField(model_name='furnitureloan', name='return_source', field=models.CharField(max_length=12, blank=True, default='')),
        migrations.AddField(model_name='furnitureloan', name='returned_by', field=models.ForeignKey(to=settings.AUTH_USER_MODEL, blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='furniture_returns')),
        migrations.RunPython(carry_forward_loans, migrations.RunPython.noop),
    ]
