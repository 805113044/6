教室预约数据库独立备份 B22 R3（无须知道 MySQL 密码）

背景：R21 网站数据库是 10.1.4.15:3306 / classroom_booking / classroom_app。
安装器自动从正在运行的 Web 容器的 Django 设置读取密码，不回显，也不把密码放入进程参数。
凭据仅写入 root:root 的 /etc/classroom-booking-db-backup/credentials.cnf (0600)。
这是临时复用业务账号，不是独立只读账号；之后应由管理员建立更低权限的专用备份账号。

MySQL 当前 have_ssl=DISABLED；R3 延续 mysqldump --ssl-mode=DISABLED --get-server-public-key。
公钥获取只协助 caching_sha2_password 身份认证，不加密 SQL 传输，
也不独立验证服务器身份。仅在受访问控制的可信专用网络中临时使用。
如果不能确认网络可信，请停止安装，先配置 MySQL TLS。

命令（上传 ZIP 至 /home/ubuntu）：
 sudo python3 /home/ubuntu/classroom-booking-backup-b22-r3-onefile.zip --check
 sudo python3 /home/ubuntu/classroom-booking-backup-b22-r3-onefile.zip --install
 sudo python3 /home/ubuntu/classroom-booking-backup-b22-r3-onefile.zip --status
 sudo python3 /home/ubuntu/classroom-booking-backup-b22-r3-onefile.zip --once
 sudo python3 /home/ubuntu/classroom-booking-backup-b22-r3-onefile.zip --rollback

仅导出 classroom_booking：每天本地 02:30，保留 30 天，磁盘剩余 < 1GiB 拒绝备份。
备份写入 /var/backups/classroom-booking/mysql/*.sql.gz 和 *.sha256；
自动安装/卸载 systemd classroom-booking-db-backup.timer/.service；回退保留备份。
首次备份和 gzip+SHA256 检验通过才开启定时器。
不改 R21、Nginx、证书、MySQL 数据库、主项目备份任务。

注意：成功导出并非已经进行恢复演练；仍应定期隔离环境恢复和配置异机加密备份。
