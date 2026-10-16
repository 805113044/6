# 免费软件与本地化部署

本项目采用 GPL-3.0-or-later，学校可自行部署使用，无软件授权费用。Django、Gunicorn、OpenPyXL、MySQL 等为安装在自己的服务器/容器中的开源软件。运行时只使用学校的应用、MySQL 与浏览器，不调用第三方 AI、预约平台、验证码、短信、邮箱、统计或其他付费/免费远程业务 API。没有外部 JS、CSS、字体、图片和 CDN；Excel 在服务器内解析，密码验证也在本机完成。

服务器、域名、已有腾讯云数据库和证书资源按自己的资源方案收费，不属于软件授权费用。域名 DNS、HTTPS 证书申请/续期是基础设施操作，不是预约业务的数据接口。可使用已有免费证书流程；不需要购买商业证书或额外 API。

## 源码部署与无公网安装

常规 `docker compose build` 会从软件仓库下载开源依赖，首次取得 `mysql:8.4` 需要镜像仓库。安装/构建完成后，平台业务不需要这些仓库。若要求腾讯云服务器运行与安装阶段都不访问外部软件仓库，可在另一台与服务器 CPU 架构相同、安装了 Docker 的可信机器上准备镜像：

```bash
# 在联网构建机，源码目录下：
docker compose build
docker pull mysql:8.4
# 使用自己的数据库服务时，只需保存应用镜像。
docker save -o classroom-images.tar classroom-booking:local mysql:8.4
```

将 `classroom-images.tar` 和源码目录传到腾讯云的独立目录。腾讯云机器需要事先安装 Docker/Compose，以及既有 Nginx；所有 OS 软件包也可由运维提前准备。加载镜像后按原部署手册填写 `.env`，以下为独立 MySQL 容器方案（已有自己的 MySQL 可省略第二个 `-f` 与 `db` 启动步骤）：

```bash
docker load -i classroom-images.tar
docker compose -f compose.yaml -f compose.local-db.yaml up -d --no-build --pull never db
docker compose -f compose.yaml -f compose.local-db.yaml run --rm --no-deps --pull never web python manage.py migrate --noinput
docker compose -f compose.yaml -f compose.local-db.yaml run --rm --no-deps --pull never web python manage.py createsuperuser
docker compose -f compose.yaml -f compose.local-db.yaml up -d --no-build --pull never web retention
```

先确认 db 为 healthy 再迁移。本方案没有公网数据库端口，不修改主站目录、数据库或容器；从备份恢复和离线镜像升级仍遵守数据保留期限。浏览器通过自己的子域名连接服务器，应用与自己的数据库之间必须保持可达；禁止公网出站可由运维在不影响主站和已有证书续期的范围内配置。

## 预约密码与查询索引

修改密码只用于对应借用记录，不是校园统一账号密码。查询“学号/工号 + 密码”只显示与这组凭据相同的记录，不会返回该学号使用其他密码提交的记录；也不验证真实学籍。

PBKDF2 哈希保存密码，HMAC 标签用于内部匹配加速，标签的密钥来自服务器 SECRET_KEY，数据库中没有该密钥。会话只保存已验证记录的编号、版本与 15 分钟到期时间。管理后台改身份/密码撤销旧验证。原始密码、HMAC 标签不得记录到访问日志或业务日志。SECRET_KEY 只在服务器 `.env` 配置，生产禁止使用默认值。

如轮换 SECRET_KEY，应在维护期停止 web / retention，用旧环境/数据库先执行下列索引清空（不删除预约或 PBKDF2 哈希），再更换密钥、重启；所有旧会话随密钥变化失效，下次验证自动回填新索引：

```bash
docker compose run --rm --no-deps web python manage.py shell -c 'from booking.models import Booking; Booking.objects.exclude(credential_tag="").update(credential_tag="")'
```

独立 MySQL 方案同样加两个 `-f` 参数。更换密钥后先测试查询和管理登录，再开放使用。
