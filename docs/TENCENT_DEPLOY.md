# 腾讯云部署手册：已有主站旁边新增独立子域名

本手册使用示例 `booking.example.com`。将它替换成学校实际子域名；不能直接复制示例作为真实域名。适用于已有 Docker Engine + Compose v2 的 Linux 服务器。先确认 `docker version`、`docker compose version` 可用；若未安装，按 Docker 官方安装文档与服务器现有面板要求安装，保留原站环境。

## 1. 部署边界与前置检查

选择的方案：**同一服务器、独立容器组/网络/目录、独立数据库与账号**。

| 项目 | 配置 |
| --- | --- |
| 应用目录 | `/opt/classroom-booking`，不放进主站根目录 |
| Compose 项目 | `classroom-booking`，不要与现有项目重名 |
| 应用进程 | 非 root、只读文件系统、独立网络、CPU/内存/PID 限制 |
| 入口端口 | 仅 `127.0.0.1:18080`，公网不放行 |
| HTTPS | 主机现有 Nginx 新增一个 `server_name booking.example.com` |
| 数据库 | MySQL 8.0/8.4 新建 `classroom_booking`，新建专用账号 |
| Cookie | `__Host-` 前缀、无 Domain、Secure、HttpOnly 会话；不共享主站登录 |
| 存储 | 既有腾讯云 MySQL 独立库，或可选独立容器卷 |

容器和子域名不能隔离宿主机内核、磁盘耗尽、root 管理员权限或整机故障。如果需要这些隔离能力，把相同应用搬到独立轻量服务器；不要把同机方案称为物理隔离。

部署前记录主站域名、健康状态、Nginx 配置位置和备份位置；检查 `ss -lnt` 中 `18080` 未被使用，`docker compose ls` 中无同名项目。建议已有数据库情况下，额外预留至少 1 GB 内存、1 CPU 和 3 GB 磁盘；若还运行本地 MySQL，额外建议预留 2 GB 内存。检查实际余量，避免挤占主站。镜像构建比运行占用更多内存，可在其他同架构 Linux 主机构建后 `docker save`/`docker load` 上传，避免在小服务器上同时构建多个项目。

现有反向代理如果运行在容器中，容器的 `127.0.0.1` 不是宿主机；本示例针对**宿主机 Nginx**。可保留主站代理，在它的部署机制下增加子域名，并仅让该代理访问预约应用的专用网络。不要为了适配示例删除主站代理容器。

## 2. 子域名、备案、HTTPS

1. 在域名 DNS 管理中新建一条 `booking` 的 A 记录，指向轻量服务器公网 IPv4；不修改已有主域名的记录。如果未配置 IPv6，不添加 AAAA。
2. 确认中国大陆服务器上的域名备案与接入要求已满足，可在腾讯云控制台检查。开通本身不由代码执行。
3. 为**这个子域名**申请证书。使用腾讯云 SSL 证书或现有 ACME 流程。优先 DNS 验证，不停止现有 Nginx 去运行抢占 80 端口的独立签发服务。
4. 保存证书到新的子目录，配置自动续期，保留主域名证书与续期任务。
5. 轻量防火墙与系统防火墙只需允许用户访问 80/443；不公开 18080 或 3306。

## 3. MySQL 8 独立数据库及账号

在腾讯云数据库控制台通过管理员操作新建 UTF-8 数据库和专用账号。示意 SQL：

```sql
CREATE DATABASE classroom_booking CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'classroom_app'@'应用来源地址' IDENTIFIED BY '在控制台生成的独立强密码';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
  ON classroom_booking.* TO 'classroom_app'@'应用来源地址';
```

“应用来源地址”用实际连接来源或平台允许的最窄网段，不要直接改成 `%`。以上账号权限只在新库生效，没有 `GRANT OPTION`、主站库权限或全局权限。迁移后若按学校策略收紧 DDL 权限，可改用独立迁移账号，在升级时临时授予需要的权限。不要复用主站数据库管理员、root 账号或主站连接串。

优先使用腾讯云内网地址，确认轻量服务器与数据库网络互通并按平台文档配置白名单。若不能走内网，通过腾讯云支持的方式连接，不要为整个公网开放数据库。跨不可信网络必须验证 TLS：下载数据库官方 CA，放在独立目录，并使用附加 Compose 文件把只读 CA 挂载给 `web` 和 `retention`，例如：

```yaml
services:
  web:
    volumes:
      - /opt/classroom-booking-certs:/run/db-ca:ro
  retention:
    volumes:
      - /opt/classroom-booking-certs:/run/db-ca:ro
```

然后 `.env` 设置 `DB_SSL_CA=/run/db-ca/ca.pem`，用与证书匹配的数据库主机名。每次运行 Compose 时使用相同附加文件；不要关闭证书或主机名验证。

## 4. 上传代码并设置环境

把 GitHub 下载的 ZIP 解压到独立目录，进入**包含 `compose.yaml` 的那一层**：

```bash
cd /opt/classroom-booking
cp .env.example .env
chmod 600 .env
```

编辑 `.env`：

- `DEBUG=false`。
- `SECRET_KEY` 用 `python3 -c 'import secrets; print(secrets.token_urlsafe(64))'` 生成，妥善保管，不写进 GitHub。
- `ALLOWED_HOSTS=实际子域名`，不要写协议、端口或通配符。
- `CSRF_TRUSTED_ORIGINS=https://实际子域名`，不要填主域名或 `https://*.example.com`。
- 数据库填写新库、新账号、内网地址和密码。Docker `.env` 中含 `$`、`#` 等的密码建议用单引号包裹，避免变量插值；独立随机 URL-safe 密码最容易配置。
- 默认 `RETENTION_DAYS=45`，同时确认数据库备份/日志/云快照保留不超过 14 天。详见 [数据保留](DATA_RETENTION.md)。

`.env` 含敏感信息，只在服务器配置，不上传仓库、不打进 ZIP。若服务器上已有 `.env`，升级时保留它，不用示例覆盖。

## 5. 构建、迁移、创建账号、启动

本节使用已有腾讯云 MySQL：

```bash
cd /opt/classroom-booking
docker compose build
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py check --deploy --fail-level ERROR
docker compose run --rm web python manage.py createsuperuser
docker compose up -d web retention
docker compose ps
docker compose exec web python deploy/check_health.py
docker compose exec retention python deploy/check_retention.py
```

健康检查包含实际数据库查询。不要把“容器运行中”作为应用就绪的唯一依据。生产首次不运行 `seed_demo`，管理员之后在后台录入学校真实教室。`createsuperuser` 会交互询问用户名、邮箱、密码，不提供默认账号。

部署检查预期保留 `security.W005` 与 `security.W021` 两条建议：本项目只对当前预约域名设置 HSTS，不设置 `includeSubDomains` 或申请整个域名树的浏览器预加载。它们不阻止部署；其他警告须逐项排查，不应一概忽略。

如果不使用既有数据库，而使用独立 MySQL 容器：`.env` 设 `DB_HOST=db`，设置独立 `MYSQL_ROOT_PASSWORD`，所有上述 Compose 命令统一增加 `-f compose.yaml -f compose.local-db.yaml`。先运行 `docker compose -f compose.yaml -f compose.local-db.yaml up -d db` 等待 healthy，再迁移/创建管理员/启动。数据库没有发布宿主机端口，使用独立命名数据卷，不挂载主站数据库目录。不要运行带 `-v` 的 `down`，它会删除此项目的数据卷。

## 6. 新增 Nginx 虚拟主机

将 `deploy/nginx-booking.conf.example` 复制到**现有 Nginx 读取的新增配置文件位置**，仅替换其中子域名和证书路径。不同系统、宝塔或腾讯镜像的位置不同，以现有主站配置为准。

```bash
sudo nginx -t
# 只有检查通过才平滑重载，保留正在服务的主站。
sudo systemctl reload nginx
```

示例 `proxy_pass http://127.0.0.1:18080` 指向独立应用。应用只信任此反向代理覆盖后的 `X-Forwarded-Proto` 和 `X-Real-IP`；**不要把应用的 8000/18080 暴露到公网**。如果经过 CDN，按 CDN 官方 IP 范围正确配置 `real_ip`，不盲目信任客户端传来的转发头。

配置失败先撤回新增的子域名文件，再检查并重载，不改主域名配置。证书续期后需平滑重载 Nginx。

## 7. 配置学校数据

1. 用新管理员登录子域名 `/admin/`。
2. 录入教室编号、楼层、类型、容量、设备和开放状态。部门现在仅作资料记录，不再区分学院管理员权限；所有开放教室可供普通用户预约。
3. 只创建管理员账号，不创建学生或教师普通账号。新增管理员须开启“工作人员状态”；创建全功能系统管理账号使用 `createsuperuser`。普通用户不能通过账号入口登录。
4. 打开“教学占用”，下载 CSV 或 Excel 日期/单双周模板。`term_start` 是第一教学周的周一，`week_pattern` 为每周/单周/双周，`weekday` 为周一至周日的 1–7；排除日期使用分号分隔。先预览再确认写入。规则配置按学期保存；后台每小时展开未来 30 天占用，预约提交也会独立检查正式课表。
5. 普通用户填写学号/工号和本次密码后直接预约，姓名公开显示；其他资料仅本人验证后及管理员可见。不自动验证学籍，不发送短信或邮件。
6. 管理员可以从课表或“预约管理”查看详情，编辑、退回或撤销。退回释放时段，本人改完重新提交重新检查冲突。

### 从第一版升级

先备份新系统自己的数据库，停止 `web retention`，构建新版后执行 `migrate`；新增预约密码字段和学期规则表。迁移会将旧的待审批预约转为已批准，并停用所有非管理员账号，符合“只有管理员登录”的新规则。既有借用人姓名仍会展示；旧的随机查询凭证不再用于修改。旧预约没有本次密码，需管理员核实后在“编辑预约”设置新密码（仅限原本免账号的记录）；旧教师账号关联的记录由管理员处理。不要回滚到仍允许旧权限的应用而不恢复匹配的数据库备份。

## 8. 日常维护与升级

- 每日检查 `docker compose ps`：`web` 和 `retention` 均须 healthy。Docker 不会仅因 unhealthy 自动重启进程，监控需对 unhealthy/进程重启告警；清理命令报错会退出，重启策略会尝试恢复。
- 升级前保留当前源码版本/镜像，按学校备份策略备份**新数据库**。上传新代码时保留 `.env`。
- 先运行 `docker compose build`；数据库迁移前 `docker compose stop web retention` 进入维护，再用 `docker compose run --rm --no-deps web python manage.py migrate --noinput`，然后 `docker compose up -d web retention`。
- 数据库迁移可能不支持回滚。失败时不要对主站库操作；停止新系统，按已演练的新系统备份恢复流程操作。恢复后立刻执行 `purge_expired`，再开放入口，防止过期数据重新出现。
- 日志只包含进程错误，不记录请求正文/预约密码。避免在生产开启 DEBUG、请求转储或数据库通用查询日志。对独立 Nginx 错误日志配置不超过 14 天的轮转。
- 如需停止：在项目目录执行 `docker compose stop web retention`；不停止主站服务，不删除主站网络/卷。

交付环境仅测试代码与容器，不会自动修改腾讯云 DNS、证书、线上数据库或主站。实际子域名/数据库地址/密码须由部署人员在服务器配置。
