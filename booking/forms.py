import csv
import io
from datetime import datetime
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone


class BookingForm(forms.Form):
    title = forms.CharField(
        label="预约用途",
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "例如：学习小组研讨、班级活动"}),
    )
    date = forms.DateField(
        label="使用日期", widget=forms.DateInput(attrs={"type": "date"})
    )
    start = forms.TimeField(
        label="开始时间",
        widget=forms.TimeInput(attrs={"type": "time", "min": "07:00", "max": "22:00"}),
    )
    end = forms.TimeField(
        label="结束时间",
        widget=forms.TimeInput(attrs={"type": "time", "min": "07:00", "max": "22:00"}),
    )
    participants = forms.IntegerField(label="使用人数", min_value=1)

    def clean(self):
        data = super().clean()
        if all(x in data for x in ("date", "start", "end")):
            data["start_at"] = timezone.make_aware(
                datetime.combine(data["date"], data["start"])
            )
            data["end_at"] = timezone.make_aware(
                datetime.combine(data["date"], data["end"])
            )
        return data


class CourseImportForm(forms.Form):
    file = forms.FileField(
        label="课表 CSV 文件", widget=forms.FileInput(attrs={"accept": ".csv"})
    )
    confirm = forms.BooleanField(
        label="确认写入（不勾选仅预览验证，不保存）", required=False
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 1024 * 1024:
            raise ValidationError("文件不能超过 1 MB。")
        try:
            reader = csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig")))
            if reader.fieldnames != ["room", "title", "date", "start", "end"]:
                raise ValidationError("CSV 表头须严格为 room,title,date,start,end。")
            rows = []
            for number, row in enumerate(reader, 2):
                if number > 501:
                    raise ValidationError("单次最多导入 500 行。")
                if None in row or any(v is None for v in row.values()):
                    raise ValidationError(f"第 {number} 行列数错误。")
                form = BookingForm(
                    {
                        "title": row["title"],
                        "date": row["date"],
                        "start": row["start"],
                        "end": row["end"],
                        "participants": 1,
                    }
                )
                if not row["room"].strip() or not form.is_valid():
                    raise ValidationError(
                        f"第 {number} 行数据格式错误，日期用 YYYY-MM-DD，时间用 HH:MM。"
                    )
                rows.append({"room": row["room"].strip(), **form.cleaned_data})
            if not rows:
                raise ValidationError("文件中没有课程数据。")
            return rows
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ValidationError("请上传 UTF-8 编码的 CSV 文件。") from exc


class StudentBookingForm(BookingForm):
    student_name = forms.CharField(label="学生姓名", max_length=60)
    student_id = forms.CharField(label="学号", max_length=40)
    student_department = forms.CharField(label="学院 / 班级", max_length=80)
    student_contact = forms.CharField(
        label="联系方式（手机或校内邮箱）", max_length=100
    )
    consent = forms.BooleanField(
        label="同意学校仅为预约审批处理上述信息，预约记录最多保留 60 天"
    )
