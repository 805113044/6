import csv
import io
from datetime import date, datetime, time, timedelta
import zipfile
from xml.etree.ElementTree import ParseError
from defusedxml.common import DefusedXmlException
from openpyxl.utils.exceptions import InvalidFileException
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
        label="使用日期",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    start = forms.TimeField(
        label="开始时间",
        widget=forms.TimeInput(
            format="%H:%M", attrs={"type": "time", "min": "07:00", "max": "22:00"}
        ),
    )
    end = forms.TimeField(
        label="结束时间",
        widget=forms.TimeInput(
            format="%H:%M", attrs={"type": "time", "min": "07:00", "max": "22:00"}
        ),
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
        label="课表文件（CSV / XLSX）",
        widget=forms.FileInput(attrs={"accept": ".csv,.xlsx"}),
    )
    confirm = forms.BooleanField(
        label="确认写入（不勾选仅预览验证，不保存）", required=False
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 1024 * 1024:
            raise ValidationError("文件不能超过 1 MB。")
        try:
            raw = upload.read()
            if upload.name.lower().endswith(".xlsx"):
                from openpyxl import load_workbook

                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    if (
                        len(archive.infolist()) > 2000
                        or sum(x.file_size for x in archive.infolist())
                        > 10 * 1024 * 1024
                    ):
                        raise ValidationError("XLSX 解压内容过大。")
                workbook = load_workbook(
                    io.BytesIO(raw), read_only=True, data_only=True, keep_links=False
                )
                try:
                    values = workbook.active.iter_rows(values_only=True)
                    header = [str(v).strip() for v in next(values, ())]
                    records = []
                    for index, values_row in enumerate(values):
                        if index >= 500:
                            raise ValidationError("单次最多导入 500 行。")
                        if not any(v is not None for v in values_row):
                            continue

                        def cell(v):
                            if isinstance(v, datetime):
                                return (
                                    v.date().isoformat()
                                    if v.time() == time.min
                                    else v.isoformat()
                                )
                            if isinstance(v, (date, time)):
                                return v.isoformat()
                            return "" if v is None else str(v).strip()

                        records.append(dict(zip(header, [cell(v) for v in values_row])))
                    reader = records
                finally:
                    workbook.close()
            else:
                reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
                header = reader.fieldnames
            dated = ["room", "title", "date", "start", "end"]
            recurring = [
                "room",
                "title",
                "teacher",
                "term_start",
                "week_start",
                "week_end",
                "week_pattern",
                "weekday",
                "start",
                "end",
                "excluded_dates",
            ]
            if header not in (dated, dated + ["teacher"], recurring):
                raise ValidationError(
                    "表头与日期模板或学期单双周模板不一致，请先下载模板。"
                )
            rows = []
            for number, row in enumerate(reader, 2):
                if number > 501:
                    raise ValidationError("单次最多导入 500 行。")
                if None in row or any(v is None for v in row.values()):
                    raise ValidationError(f"第 {number} 行列数错误。")
                if header == recurring:
                    try:
                        rule = {k: row[k].strip() for k in recurring}
                        rule["term_start"] = date.fromisoformat(rule["term_start"])
                        for key in ("week_start", "week_end", "weekday"):
                            rule[key] = int(rule[key])
                        rule["week_pattern"] = {
                            "单周": "odd",
                            "双周": "even",
                            "每周": "all",
                        }.get(rule["week_pattern"], rule["week_pattern"])
                        rule["start"], rule["end"] = (
                            time.fromisoformat(rule["start"]),
                            time.fromisoformat(rule["end"]),
                        )
                        rule["excluded_dates"] = [
                            date.fromisoformat(x.strip()).isoformat()
                            for x in rule["excluded_dates"].split(";")
                            if x.strip()
                        ]
                        if (
                            rule["term_start"].weekday() != 0
                            or not 1 <= rule["week_start"] <= rule["week_end"] <= 30
                            or not 1 <= rule["weekday"] <= 7
                            or rule["week_pattern"] not in ("all", "odd", "even")
                            or not time(7) <= rule["start"] < rule["end"] <= time(22)
                            or not rule["room"]
                            or not 1 <= len(rule["title"]) <= 100
                            or not 1 <= len(rule["teacher"]) <= 60
                        ):
                            raise ValueError
                        if rule["term_start"] > timezone.localdate() + timedelta(
                            days=365
                        ):
                            raise ValueError
                        rule.pop("room")
                        rows.append({"room": row["room"].strip(), "rule": rule})
                        continue
                    except (ValueError, KeyError, TypeError) as exc:
                        raise ValidationError(
                            f"第 {number} 行学期格式错误：首周从周一开始，周次 1–30，星期 1–7，模式 all/odd/even 或 每周/单周/双周。"
                        ) from exc
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
                rows.append(
                    {
                        "room": row["room"].strip(),
                        "teacher": row.get("teacher", "")[:60],
                        **form.cleaned_data,
                    }
                )
            if not rows:
                raise ValidationError("文件中没有课程数据。")
            return rows
        except (
            UnicodeDecodeError,
            csv.Error,
            zipfile.BadZipFile,
            KeyError,
            OSError,
            ValueError,
            ParseError,
            DefusedXmlException,
            InvalidFileException,
        ) as exc:
            raise ValidationError(
                "文件损坏或格式错误，请使用 UTF-8 CSV 或标准 XLSX。"
            ) from exc


class StudentBookingForm(BookingForm):
    student_name = forms.CharField(label="借用人姓名（公开显示）", max_length=60)
    student_id = forms.CharField(label="学号 / 工号（不公开）", max_length=40)
    student_department = forms.CharField(label="学院 / 班级", max_length=80)
    student_contact = forms.CharField(
        label="联系方式（手机或校内邮箱）", max_length=100
    )
    consent = forms.BooleanField(
        label="同意公开借用人姓名，其余信息仅本人及管理员可见；预约记录最多保留 60 天"
    )
    edit_password = forms.CharField(
        label="本次预约的修改密码",
        min_length=10,
        max_length=128,
        widget=forms.PasswordInput,
        help_text="不是校园账号密码；每笔可不同，修改/取消时使用，请自行保存。",
    )
    edit_password_confirm = forms.CharField(
        label="再次输入本次密码", widget=forms.PasswordInput, max_length=128
    )

    def clean(self):
        data = super().clean()
        if data.get("edit_password") != data.get("edit_password_confirm"):
            self.add_error("edit_password_confirm", "两次密码不一致。")
        return data


class BookingEditForm(BookingForm):
    room = forms.ModelChoiceField(label="教室", queryset=None)
    student_name = forms.CharField(
        label="借用人姓名（公开）", max_length=60, required=False
    )
    student_id = forms.CharField(label="学号 / 工号", max_length=40, required=False)
    student_department = forms.CharField(
        label="学院 / 班级", max_length=80, required=False
    )
    student_contact = forms.CharField(label="联系方式", max_length=100, required=False)
    edit_password = forms.CharField(
        label="更换本次修改密码（留空保留）",
        required=False,
        min_length=10,
        max_length=128,
        widget=forms.PasswordInput,
    )
    reason = forms.CharField(label="管理员修改原因", required=False, max_length=300)

    def __init__(self, *args, rooms, admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["room"].queryset = rooms
        self.fields["reason"].required = admin
        if not admin:
            self.fields.pop("reason")
