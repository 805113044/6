#!/usr/bin/env python3
"""First installation with an existing Tencent Cloud MySQL 8 private endpoint.

Run as root on the Ubuntu host after loading classroom-booking:local. The
database administrator password is read from the terminal and never stored.
"""

import getpass
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys


APP = Path("/opt/classroom-booking/app")
DOMAIN = "jy.zlmmxwl.com"
DB_HOST = "10.1.4.15"
DB_PORT = 3306
DB_NAME = "classroom_booking"
DB_USER = "classroom_app"

DB_SETUP_CODE = r'''
import json
import MySQLdb
import sys

p = json.load(sys.stdin)
conn = MySQLdb.connect(host=p["host"], port=p["port"], user=p["admin_user"],
                       passwd=p["admin_password"], connect_timeout=10,
                       charset="utf8mb4")
cursor = conn.cursor()
cursor.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=%s",
               (p["db_name"],))
if cursor.fetchone():
    raise SystemExit("独立库已存在；为保护现有数据，脚本已停止。")
cursor.execute("CREATE USER %s@%s IDENTIFIED BY %s",
               (p["db_user"], p["source_ip"], p["app_password"]))
cursor.execute("CREATE DATABASE `classroom_booking` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
cursor.execute("GRANT ALL PRIVILEGES ON `classroom_booking`.* TO %s@%s",
               (p["db_user"], p["source_ip"]))
conn.close()
check = MySQLdb.connect(host=p["host"], port=p["port"], user=p["db_user"],
                        passwd=p["app_password"], db=p["db_name"],
                        connect_timeout=10, charset="utf8mb4")
check.close()
print("独立数据库和专用账号已创建，连接正常。")
'''

ADMIN_CODE = r'''
import sys
from django.contrib.auth import get_user_model

password = sys.stdin.read().rstrip("\n")
User = get_user_model()
if User.objects.filter(username="admin").exists():
    raise SystemExit("管理员 admin 已存在；未覆盖原密码。")
User.objects.create_superuser(username="admin", email="",
                              password=password, display_name="系统管理员")
print("管理员账号已创建。")
'''


def run(command, *, input_text=None):
    return subprocess.run(command, cwd=APP, input=input_text, text=True, check=True)


def main():
    if os.geteuid() != 0:
        raise SystemExit("请用 sudo python3 运行本脚本。")
    if not (APP / "compose.yaml").is_file() or not (APP / ".env.example").is_file():
        raise SystemExit("没有找到 /opt/classroom-booking/app 中的项目文件。")
    if (APP / ".env").exists():
        raise SystemExit(".env 已存在；为避免覆盖原配置，脚本已停止。")
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 18080)) == 0:
            raise SystemExit("18080 端口已被占用；脚本已停止。")
    subprocess.run(["docker", "image", "inspect", "classroom-booking:local"],
                   stdout=subprocess.DEVNULL, check=True)

    host = DB_HOST
    port = DB_PORT
    print(f"数据库内网地址：{host}:{port}")
    admin_user = input("数据库管理账号 [root]：").strip() or "root"
    admin_password = getpass.getpass("数据库管理账号密码（输入时不显示）：")
    if not host or not admin_user or not admin_password:
        raise SystemExit("连接信息不能为空。")
    try:
        address = ipaddress.IPv4Address(socket.gethostbyname(host))
    except (ValueError, OSError) as exc:
        raise SystemExit(f"数据库地址或端口不正确：{exc}") from exc
    private_ranges = [ipaddress.ip_network(net) for net in
                      ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if not any(address in net for net in private_ranges):
        raise SystemExit("需要腾讯云数据库的内网地址；脚本不会经公网发送管理密码。")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((str(address), port))
        source_ip = sock.getsockname()[0]
    print(f"本机数据库连接来源 IP：{source_ip}")
    print("将只创建 classroom_booking 库及其专用账号。")

    app_password = secrets.token_urlsafe(36)
    secret_key = secrets.token_urlsafe(64)
    admin_login_password = secrets.token_urlsafe(24)
    db_input = json.dumps({"host": host, "port": port, "admin_user": admin_user,
                           "admin_password": admin_password, "db_name": DB_NAME,
                           "db_user": DB_USER, "app_password": app_password,
                           "source_ip": source_ip})
    admin_password = None
    run(["docker", "run", "--rm", "-i", "--network", "bridge",
         "classroom-booking:local", "python", "-c", DB_SETUP_CODE],
        input_text=db_input)
    db_input = None

    content = (APP / ".env.example").read_text()
    replacements = {
        "booking.example.com": DOMAIN,
        "REPLACE_WITH_A_RANDOM_SECRET": secret_key,
        "your-private-mysql-host": host,
        "DB_PORT=3306": f"DB_PORT={port}",
        "REPLACE_WITH_DATABASE_PASSWORD": app_password,
    }
    for old, new in replacements.items():
        if old not in content:
            raise SystemExit(f"环境模板缺少 {old}；请勿继续。")
        content = content.replace(old, new)
    target = APP / ".env"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(content)
    app_stat = APP.stat()
    os.chown(target, app_stat.st_uid, app_stat.st_gid)

    run(["docker", "compose", "config", "--quiet"])
    run(["docker", "compose", "run", "--rm", "--no-deps", "--pull", "never",
         "-T", "web", "python", "manage.py", "migrate", "--noinput"])
    run(["docker", "compose", "run", "--rm", "--no-deps", "--pull", "never",
         "-T", "web", "python", "manage.py", "shell", "-c", ADMIN_CODE],
        input_text=admin_login_password + "\n")
    print("\n管理员账号：admin")
    print(f"管理员初始密码：{admin_login_password}")
    print("请单独保存初始密码，不要把这一行发到聊天里。\n")
    run(["docker", "compose", "up", "-d", "--no-build", "--pull", "never",
         "web", "retention"])
    run(["docker", "compose", "ps"])
    print("容器已启动；接下来仍需配置子域名 DNS、证书和 Nginx。")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"步骤失败，退出码 {exc.returncode}；上方错误可用于诊断。") from exc
