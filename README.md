# 知室 · 校园教室预约与管理

中文、手机适配的学校教室预约系统。Python 3.12 + Django 5.2 LTS + MySQL 8，支持 Docker 部署到腾讯云轻量应用服务器。

![知室首页（示例教室数据）](docs/images/overview.png)

## 已实现的流程

| 使用者 | 身份与权限 | 预约流程 |
| --- | --- | --- |
| 学生 | 无需账号，填写姓名、学号、学院/班级、联系方式 | 申请 → 管理员核验审批 → 凭随机查询凭证查看/取消 |
| 教师 | 管理员开通教师账号，登录使用 | 选时段 → 直接生效，无需审批 |
| 学院管理员 | `is_staff=true` 且部门名称与教室一致 | 审批/撤销本学院申请，导入课程占用、查看统计 |
| 系统管理员 | `is_superuser=true` | 管理全部教室、账号、权限、审批与数据 |

- 教室按校区、楼栋、楼层、人数、类型、日期和空闲时段筛选。
- 待审批、已批准、正式课程统一参与冲突检查；MySQL 事务锁定教室，防止同时提交导致重复占用。
- 默认开放 07:00–22:00，提前 30 天，单次最多 4 小时；学生和教师普通取消截止为开始前 30 分钟。
- CSV 教学占用支持预览、整批回滚、重复导入跳过。按具体日期导入，不含自动排课、节次/学期转换。
- 学生申请凭证使用 256 位随机数，数据库只保存哈希；公开课表不显示姓名、学号、联系信息或用途。
- 最近记录统计和汇总 CSV 导出；审批、导入和撤销有操作记录。
- 数据保留上限为创建后 60 天。生产模板采用在线 45 天 + 备份至多 14 天的组合，详见 [数据保留说明](docs/DATA_RETENTION.md)。

## 本地试用（无 MySQL）

SQLite 仅供单人本地开发；多用户部署必须使用 MySQL/PostgreSQL。

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DEBUG=true
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py seed_demo
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

在另一终端启动 `DEBUG=true .venv/bin/python manage.py run_retention`。示例数据仅有教室，没有默认账号或默认密码。系统管理员登录 `/admin/` 创建教师账号，将身份设为“教师”；学院管理员另外开启“工作人员状态”并设置部门。首次登录请修改初始密码。学生直接打开首页即可申请。

## 上传腾讯云部署

1. 在 GitHub 点击 **Code → Download ZIP**，解压后上传整个目录到服务器独立目录，例如 `/opt/classroom-booking`。
2. 按 [腾讯云部署手册](docs/TENCENT_DEPLOY.md) 配置 `.env`、独立 MySQL 数据库与账号、子域名 DNS、HTTPS 和新的 Nginx 虚拟主机。
3. 构建应用镜像、执行数据库迁移、创建管理员，启动 `web` 与 `retention` 两个服务。
4. 完成 [上线验收清单](docs/ACCEPTANCE.md) 后供学校使用。

**不要覆盖主站文件、Nginx 主配置或主站数据库；不要在已有站点服务器上重装/替换整套面板或反向代理。** 本项目的 Compose 只发布本机 `18080` 端口，不抢占主站 80/443，不公开数据库端口。

## 验证

```bash
export DEBUG=true
python manage.py collectstatic --noinput
python manage.py check
python manage.py test -v 2
```

SQLite 会明确跳过行锁并发测试。要执行全部测试，请安装 `requirements-mysql.txt`，并设置 `DB_ENGINE=mysql`、`DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD` 指向**专用测试数据库服务器**。Django 会创建/删除 `test_<DB_NAME>`，测试账号需对此测试库有权限。不要向生产账号增加全局权限来运行测试。GitHub Actions 使用独立 MySQL 8.4 测试服务。

## 目录

```text
booking/             业务、权限、事务服务、测试和管理命令
config/              Django 配置与路由
templates/ static/   中文界面，无外部字体/CDN依赖
deploy/              健康检查与 Nginx 示例
docs/                部署、保留策略、验收与开发说明
compose.yaml         使用已有 MySQL 的生产服务
compose.local-db.yaml 可选的独立 MySQL 容器
```

参考用户提供的《教室预约与管理网站_开源项目调研整合报告.pdf》中的业务与部署设计，未复制报告所列项目的代码；见 [参考与许可证](THIRD_PARTY.md)。本仓库采用 GPL-3.0-or-later。学校可自行部署、修改；对外分发需遵守许可证。

当前不含短信/邮件通知、校园单点登录、自动学籍验证、Excel 文件解析、自动排课或递归周次课表；学生通过查询凭证主动查询审批结果。
