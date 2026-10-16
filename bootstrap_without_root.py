#!/usr/bin/env python3
"""Install classroom booking using only a temporary CREATE grant on its own DB user.

Stages: prepare -> create-db -> finish. Run with sudo on the Tencent host.
The Tencent console creates the account and changes its grants between stages.
"""

import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
from datetime import datetime


APP = Path("/opt/classroom-booking/app")
PACKAGES = Path("/opt/classroom-booking/packages")
STATE = PACKAGES / ".bootstrap-without-root.json"
HOST = "10.1.4.15"
PORT = 3306
DATABASE = "classroom_booking"
USER = "classroom_app"
DOMAIN = "jy.zlmmxwl.com"
IMAGE = "classroom-booking:local"

DB_CODE = r'''
import json
import MySQLdb
import re
import sys

p = json.load(sys.stdin)
connection = MySQLdb.connect(host=p["host"], port=p["port"], user=p["user"],
                             passwd=p["password"], connect_timeout=10,
                             charset="utf8mb4")
cur = connection.cursor()
cur.execute("SELECT CURRENT_USER()")
principal = cur.fetchone()[0]
if not principal.startswith("classroom_app@"):
    raise SystemExit("登录的不是课堂预约专用账号，已停止。")
if p["action"] == "create-db":
    cur.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=%s",
                ("classroom_booking",))
    if cur.fetchone():
        raise SystemExit("classroom_booking 已存在，已停止以保护现有数据。")
    cur.execute("CREATE DATABASE `classroom_booking` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    print("已创建独立数据库 classroom_booking。")
elif p["action"] == "check-grants":
    cur.execute("SHOW GRANTS")
    grants = [row[0] for row in cur.fetchall()]
    for grant in grants:
        if " ON *.* " in grant and re.search(r"\b(?:ALL PRIVILEGES|CREATE)\b", grant.split(" ON *.* ")[0]):
            raise SystemExit("全局 CREATE 权限仍在；请先在控制台取消它。")
    cur.execute("USE `classroom_booking`")
    cur.execute("CREATE TABLE `__booking_setup_probe` (`id` INT PRIMARY KEY)")
    try:
        cur.execute("INSERT INTO `__booking_setup_probe` VALUES (1)")
        cur.execute("SELECT id FROM `__booking_setup_probe` WHERE id=1")
        if cur.fetchone() != (1,):
            raise SystemExit("独立库读写检查失败。")
        cur.execute("UPDATE `__booking_setup_probe` SET id=2 WHERE id=1")
        cur.execute("DELETE FROM `__booking_setup_probe` WHERE id=2")
    finally:
        cur.execute("DROP TABLE `__booking_setup_probe`")
    print("已确认全局建库权限收回，独立库读写及建表权限正常。")
connection.close()
'''

ADMIN_CODE = r'''
import sys
from django.contrib.auth import get_user_model

password = sys.stdin.read().rstrip("\n")
User = get_user_model()
if not User.objects.filter(username="admin").exists():
    User.objects.create_superuser(username="admin", email="",
                                  password=password, display_name="系统管理员")
    print("已创建管理员 admin。")
else:
    print("管理员 admin 已存在，未改动其密码。")
'''


def run(command, *, input_text=None):
    return subprocess.run(command, cwd=APP, input=input_text, text=True, check=True)


def save_state(data):
    temporary = STATE.with_name(STATE.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump(data, file)
    os.replace(temporary, STATE)


def load_state():
    if not STATE.is_file():
        raise SystemExit("请先运行 prepare。")
    return json.loads(STATE.read_text())


def source_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((HOST, PORT))
        return sock.getsockname()[0]


def call_db(action, state):
    payload = json.dumps({"host": HOST, "port": PORT, "user": USER,
                          "password": state["db_password"], "action": action})
    run(["docker", "run", "--rm", "-i", "--network", "bridge", IMAGE,
         "python", "-c", DB_CODE], input_text=payload)


def prepare():
    if STATE.exists():
        state = load_state()
    else:
        if not (APP / "compose.yaml").is_file() or not (APP / ".env.example").is_file():
            raise SystemExit("项目文件不在预期目录。")
        subprocess.run(["docker", "image", "inspect", IMAGE],
                       stdout=subprocess.DEVNULL, check=True)
        if subprocess.run(["docker", "ps", "-aq", "--filter",
                           "label=com.docker.compose.project=classroom-booking"],
                          capture_output=True, text=True, check=True).stdout.strip():
            raise SystemExit("该 Compose 项目已有容器，已停止以保护现有部署。")
        state = {"db_password": secrets.token_urlsafe(32),
                 "secret_key": secrets.token_urlsafe(64),
                 "admin_password": secrets.token_urlsafe(24),
                 "source_ip": source_ip(), "db_created": False}
        save_state(state)
    print(f"专用数据库账号：{USER}")
    print(f"账号的主机：{state['source_ip']}")
    print(f"账号密码（复制到腾讯云创建账号窗口，勿发到聊天中）：{state['db_password']}")
    print("创建账号后，只勾选它的全局 CREATE 权限；然后运行 create-db。")


def create_db():
    state = load_state()
    if state["db_created"]:
        print("本脚本已创建独立数据库，无需重复运行。")
        return
    if source_ip() != state["source_ip"]:
        raise SystemExit("服务器连接数据库的来源 IP 已变化，请勿继续。")
    call_db("create-db", state)
    state["db_created"] = True
    save_state(state)
    print("现在请在腾讯云取消该账号的全局 CREATE，改为只对 classroom_booking 授予权限。")


def write_env(state):
    target = APP / ".env"
    if target.exists():
        old = dict(re.findall(r"^([A-Z_]+)=(.*)$", target.read_text(), re.M))
        if old.get("DB_PASSWORD") == state["db_password"] and old.get("DB_HOST") == HOST:
            return
        if old.get("DB_PASSWORD") and not old["DB_PASSWORD"].startswith("REPLACE_"):
            raise SystemExit(".env 已有另一个数据库密码；为保护原配置，脚本已停止。")
        backup = PACKAGES / ("env-before-no-root-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        if backup.exists():
            raise SystemExit("配置备份文件名冲突，已停止。")
        os.replace(target, backup)
        os.chmod(backup, 0o600)
        print(f"旧的不完整配置已备份：{backup}")
    content = (APP / ".env.example").read_text()
    replacements = {"booking.example.com": DOMAIN,
                    "REPLACE_WITH_A_RANDOM_SECRET": state["secret_key"],
                    "your-private-mysql-host": HOST,
                    "REPLACE_WITH_DATABASE_PASSWORD": state["db_password"]}
    for old, new in replacements.items():
        if old not in content:
            raise SystemExit(f"环境模板缺少 {old}；已停止。")
        content = content.replace(old, new)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(content)
    app_stat = APP.stat()
    os.chown(target, app_stat.st_uid, app_stat.st_gid)


def finish():
    state = load_state()
    if not state["db_created"]:
        raise SystemExit("请先运行 create-db。")
    call_db("check-grants", state)
    write_env(state)
    run(["docker", "compose", "config", "--quiet"])
    run(["docker", "compose", "run", "--rm", "--no-deps", "--pull", "never",
         "-T", "web", "python", "manage.py", "migrate", "--noinput"])
    run(["docker", "compose", "run", "--rm", "--no-deps", "--pull", "never",
         "-T", "web", "python", "manage.py", "shell", "-c", ADMIN_CODE],
        input_text=state["admin_password"] + "\n")
    print("\n管理员账号：admin")
    print(f"管理员初始密码：{state['admin_password']}")
    print("请把初始密码单独保存，不要发到聊天中。\n")
    run(["docker", "compose", "up", "-d", "--no-build", "--pull", "never",
         "web", "retention"])
    run(["docker", "compose", "ps"])
    run(["docker", "compose", "exec", "-T", "web", "python",
         "deploy/check_health.py"])
    run(["docker", "compose", "exec", "-T", "retention", "python",
         "deploy/check_retention.py"])
    STATE.unlink()
    print("独立应用容器已启动；接下来配置子域名 DNS、证书和 Nginx。")


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("请使用 sudo python3 运行。")
    if len(sys.argv) != 2 or sys.argv[1] not in ("prepare", "create-db", "finish"):
        raise SystemExit("用法：sudo python3 bootstrap_without_root.py prepare|create-db|finish")
    try:
        {"prepare": prepare, "create-db": create_db, "finish": finish}[sys.argv[1]]()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"步骤失败，退出码 {exc.returncode}；请发错误信息，但不要发送密码。") from exc
