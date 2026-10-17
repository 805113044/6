# 预约与桌椅登记保留规则

当前版本采用 R13 的 90 天规则，清理任务由 `retention` 容器持续执行。

## 教室预约

- 正常预约与教学占用以使用结束时间为保留期起点，保留 90 天。
- 管理员提前确认归还、取消或退回的记录，以处理时间为保留期起点，保留 90 天。
- 未结束的未来预约不会因为创建时间较早而提前删除。
- 预约删除时，借用人姓名、学号 / 工号、学院、联系方式、密码哈希及关联审计记录一并删除。

## 桌椅借用

- 桌椅登记以实际归还时间为保留期起点，归还后保留 90 天。
- 未归还记录不会因为创建已满 90 天自动删除。学校需要对长期未归还记录进行人工核查、催还和必要的例外处理。
- 清理登记时会删除其中的姓名、学号 / 工号、电话、用途、物品明细和密码哈希。

## 系统数据与清理任务

- `BOOKING_RETENTION_DAYS` 与 `FURNITURE_RETENTION_DAYS` 在当前版本固定为 90。
- `.env` 中的 `RETENTION_DAYS=21` 仍用于旧版兼容查询和限制提前预约窗口；它不覆盖上述两项 90 天规则。
- `retention` 容器分批物理删除超期记录，同时删除过期登录会话和一天前的限流计数。
- `TeachingRule` 保存正式教学计划，不含学生学号、联系方式或预约密码；过期学期规则按相同 90 天窗口清理。
- 删除以数据库行删除语义为准。存储介质空闲页、云快照和外部备份的生命周期由数据库及云平台策略决定。

## 独立数据库备份

B22 R3 备份程序只导出 `classroom_booking`，每天服务器本地时间 02:30 执行，生成压缩 SQL 与 SHA256 文件并保留 30 天。备份目录为 `/var/backups/classroom-booking/mysql`，磁盘剩余不足 1 GiB 时拒绝启动新备份。

该备份保存的是某一时点的完整数据库，因此可能包含仍在业务保留期内的个人信息。备份目录和凭据必须保持 root 专用权限，不得提交到 GitHub、打入应用镜像或放入公开下载目录。当前 B22 R3 配置适用于已确认的可信私有网络；其 MySQL 连接未启用 TLS，具体限制见 [`deploy/backup-b22-r3/README.txt`](../deploy/backup-b22-r3/README.txt)。

成功导出不等于完成恢复演练。应定期在隔离环境恢复备份，并根据学校的数据制度配置异机加密副本及其保留期。

## 排查与验证

```bash
docker compose ps retention
docker compose exec retention python deploy/check_retention.py
docker compose run --rm web python manage.py purge_expired
sudo python3 /home/ubuntu/classroom-booking-backup-b22-r3-onefile.zip --status
```

实际腾讯云备份、快照和日志期限仍需在云控制台核对；应用源码无法读取或修改云平台已有策略。
