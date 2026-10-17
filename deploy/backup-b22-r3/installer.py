#!/usr/bin/env python3
"""Single-file installer for isolated classroom database backups. No website changes."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import zipfile

HOST, PORT, DB = '10.1.4.15', 3306, 'classroom_booking'
COMPOSE_FILE = Path('/opt/classroom-booking/app/compose.yaml')
RUNNER = Path('/usr/local/sbin/classroom-booking-db-backup')
CONF_DIR = Path('/etc/classroom-booking-db-backup')
CONF = CONF_DIR / 'credentials.cnf'
SERVICE = Path('/etc/systemd/system/classroom-booking-db-backup.service')
TIMER = Path('/etc/systemd/system/classroom-booking-db-backup.timer')
BACKUP_DIR = Path('/var/backups/classroom-booking/mysql')
STATE_DIR = Path('/var/lib/classroom-booking-db-backup')
STATE = STATE_DIR / 'state.json'
TITLE = 'Classroom Booking independent MySQL backup, B22 R3'

UNIT_SERVICE = '''[Unit]
Description=Independent classroom_booking MySQL logical backup
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=root
UMask=0077
ExecStart=/usr/local/sbin/classroom-booking-db-backup
TimeoutStartSec=15min
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/var/backups/classroom-booking/mysql
'''
UNIT_TIMER = '''[Unit]
Description=Daily independent classroom_booking backup (server local time)

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
AccuracySec=1min
Unit=classroom-booking-db-backup.service

[Install]
WantedBy=timers.target
'''


def run(args, *, capture=True, check=True, timeout=30):
    return subprocess.run(args, capture_output=capture, text=True, check=check, timeout=timeout)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def installed_files():
    return [RUNNER, CONF, SERVICE, TIMER]


def check_environment():
    print('检查：独立备份环境（只读）；B22 R3 从 R21 Docker 容器安全读取连接配置')
    if os.geteuid() != 0:
        raise RuntimeError('请用 sudo python3 ... --check 运行')
    for name in ('mysqldump', 'systemctl', 'docker'):
        p = shutil.which(name)
        if not p:
            raise RuntimeError(f'未找到 {name}')
        print(f'{name}: {p}')
    features = run([shutil.which('mysqldump'), '--help'], timeout=20).stdout
    if not all(x in features for x in ('--get-server-public-key', '--ssl-mode')):
        raise RuntimeError('mysqldump 不支持 B22 R2 所需的认证参数，请勿安装')
    print('兼容性参数检查通过：--get-server-public-key 和 --ssl-mode')
    if not COMPOSE_FILE.is_file() or COMPOSE_FILE.is_symlink():
        raise RuntimeError('找不到实际运行的网站 Compose 配置；拒绝猜测路径')
    try:
        with socket.create_connection((HOST, PORT), timeout=5):
            print(f'数据库端口连接正常：{HOST}:{PORT}')
    except OSError as exc:
        raise RuntimeError(f'数据库端口不可达：{exc}') from exc
    parent = BACKUP_DIR if BACKUP_DIR.exists() else Path('/var/backups')
    free = shutil.disk_usage(parent).free
    print(f'备份磁盘剩余空间：{free / 1024**3:.2f} GiB')
    if free < 1024**3:
        raise RuntimeError('剩余空间不足 1 GiB')
    for path in installed_files():
        if path.is_symlink():
            raise RuntimeError(f'拒绝操作符号链接：{path}')
    if STATE.exists():
        print('已发现本工具安装状态：请使用 --status 或 --rollback')
    elif any(p.exists() for p in installed_files()):
        raise RuntimeError('备份工具目标路径已存在其他文件：拒绝覆盖')
    else:
        print('目标文件路径未被占用；不会触碰 R21、mingxing 或 Nginx')
    print('检查完成；尚未读取网站连接凭据、未验证备份权限（仅在 --install 时执行）')


def write_owned(path, value, mode=0o600):
    if path.exists() or path.is_symlink():
        raise RuntimeError(f'文件已存在，不覆盖：{path}')
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(fd, 'wb') as f:
        f.write(value if isinstance(value, bytes) else value.encode('utf-8'))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, mode)


def quote_cnf(s):
    if any(x in s for x in ('\n', '\r', '\0')):
        raise ValueError('数据库账号或密码不能包含换行或 NUL')
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'



def get_site_credentials():
    """Read actual Django DB settings in running Web container, never print secrets.

    We intentionally obtain credentials from Django itself, not from a guessed .env
    file or application database. This requires root/docker rights on the host.
    The password is only returned in this process's memory and written to a
    root-only MySQL option file; it is never placed on a process command line.
    """
    code = (
        "import os,json;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
        "from django.conf import settings;"
        "d=settings.DATABASES['default'];"
        "print(json.dumps({'engine':d.get('ENGINE'), 'name':d.get('NAME'),"
        "'host':d.get('HOST'),'port':str(d.get('PORT') or 3306),"
        "'user':d.get('USER'),'password':d.get('PASSWORD')}, ensure_ascii=False))"
    )
    cmd = [shutil.which('docker'), 'compose', '-f', str(COMPOSE_FILE),
           'exec', '-T', 'web', 'python', '-c', code]
    try:
        result = run(cmd, check=False, timeout=35)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('读取网站数据库配置超时；未更改任何数据库或网站配置') from exc
    if result.returncode != 0:
        raise RuntimeError('无法从当前 Web 容器读取 Django 数据库配置；不显示潜在敏感日志，已取消安装')
    try:
        data = json.loads(result.stdout.strip())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError('Web 容器返回的数据库配置格式异常，已取消安装') from exc
    if not isinstance(data, dict) or data.get('engine') != 'django.db.backends.mysql':
        raise RuntimeError('网站当前不使用预期 MySQL 数据库，拒绝安装')
    if data.get('name') != DB or data.get('host') != HOST or str(data.get('port')) != str(PORT):
        raise RuntimeError('网站当前数据库连接目标与已确认的 classroom_booking@10.1.4.15:3306 不符，拒绝安装')
    user = data.get('user')
    password = data.get('password')
    if not isinstance(user, str) or not user or len(user) > 128:
        raise RuntimeError('网站 MySQL 账号为空或异常，拒绝安装')
    if not isinstance(password, str) or not password:
        raise RuntimeError('网站 MySQL 密码为空，拒绝安装')
    if any(ch in user + password for ch in ('\n', '\r', '\0')):
        raise RuntimeError('账号或密码含配置文件不支持的控制字符，拒绝安装')
    return user, password


def lock_installer():
    path = '/run/lock/classroom-booking-db-backup-b22r3.lock'
    f = open(path, 'a+', encoding='ascii')
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise RuntimeError('另一份备份安装或回退程序正在运行')
    return f

def deploy():
    check_environment()
    if STATE.exists():
        raise RuntimeError('已安装，不重复安装')
    print('即将读取现有 R21 Web 容器的 Django MySQL 连接配置；数据库密码不显示、不上传、不进入命令行。')
    user, password = get_site_credentials()
    print('已确认网站数据库账号：' + user + '；仅保存 root 可读的专用备份配置。')
    print('提醒：当前复用的是网站的数据库账号，不是独立最小权限备份账号。')
    print('安全提醒：此 MySQL 服务器当前不提供 TLS。R3 以 RSA 公钥交换完成密码认证，但后续 SQL 数据流未加密。')
    print('使用 --get-server-public-key 并未独立验证服务器身份；仅限明确可信的私有网络。')
    accept = input('确认了解此限制，并接受仅在可信私有网络使用？输入 ACCEPT_PRIVATE_NETWORK：').strip()
    if accept != 'ACCEPT_PRIVATE_NETWORK':
        print('已取消，未创建任何文件。推荐先为 MySQL 配置 TLS，再安装。')
        return
    agree = input('确认临时复用网站数据库账号，只备份 classroom_booking，不修改 R21 或主项目？输入 YES 继续：').strip()
    if agree != 'YES':
        print('已取消，未创建任何文件')
        return
    with zipfile.ZipFile(sys.argv[0]) as z:
        runner = z.read('backup_runner.py')
    manifest = {
        'version': 'b22-r3',
        'files': {str(RUNNER): sha(runner), str(SERVICE): sha(UNIT_SERVICE.encode()),
                  str(TIMER): sha(UNIT_TIMER.encode())},
        'db_host': HOST, 'db_name': DB,
        'backup_directory': str(BACKUP_DIR),
    }
    made = []
    try:
        CONF_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(CONF_DIR, 0o700)
        BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(BACKUP_DIR, 0o700)
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)
        content = ('[client]\nhost=' + HOST + '\nport=' + str(PORT) + '\n'
                   + 'user=' + quote_cnf(user) + '\npassword=' + quote_cnf(password)
                   + '\ndefault-character-set=utf8mb4\n')
        write_owned(CONF, content); made.append(CONF)
        # Erase variable references as soon as on-disk root-only configuration exists.
        password = None
        content = None
        # Test MySQL credentials and backup permissions WITHOUT writing SQL data.
        print('校验备份账号能否导出数据库结构（只读，不写生产数据库）...')
        exe = shutil.which('mysqldump')
        help_text = run([exe, '--help'], timeout=20).stdout
        cmd = [exe, f'--defaults-extra-file={CONF}', '--single-transaction', '--quick',
               '--no-data', '--skip-lock-tables', '--no-tablespaces', '--triggers']
        if '--set-gtid-purged' in help_text:
            cmd.append('--set-gtid-purged=OFF')
        if '--column-statistics' in help_text:
            cmd.append('--column-statistics=0')
        # MySQL 8 caching_sha2_password on a server without TLS: RSA password exchange.
        # IMPORTANT: RSA login does not encrypt subsequent SQL traffic.
        cmd.extend(['--ssl-mode=DISABLED', '--get-server-public-key'])
        cmd.append(DB)
        check = run(cmd, timeout=60, check=False)
        if check.returncode or 'CREATE TABLE' not in check.stdout:
            raise RuntimeError('数据库备份账号测试失败，未安装：' + check.stderr[:450])
        print('数据库账号导出表结构测试通过（认证已通过；连接仍未启用 TLS）')
        write_owned(RUNNER, runner, 0o700); made.append(RUNNER)
        write_owned(SERVICE, UNIT_SERVICE); made.append(SERVICE)
        write_owned(TIMER, UNIT_TIMER); made.append(TIMER)
        run(['systemctl', 'daemon-reload'])
        print('执行第一次真实备份并校验 gzip 和 SHA256...')
        test = run([str(RUNNER)], capture=True, check=False, timeout=600)
        if test.returncode:
            raise RuntimeError('首次数据库备份失败：' + test.stderr[:900])
        print(test.stdout.strip())
        run(['systemctl', 'enable', '--now', 'classroom-booking-db-backup.timer'])
        # Ensure timer loaded; a failed enable does not count as success.
        t = run(['systemctl', 'is-active', 'classroom-booking-db-backup.timer'])
        if t.stdout.strip() != 'active':
            raise RuntimeError('自动备份定时器未激活')
        write_owned(STATE, json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
        print('安装完成：仅 classroom_booking 每天 02:30 自动备份；保留 30 天，剩余磁盘低于 1 GiB 则停止备份。')
        print('注意：当前 MySQL 连接没有 TLS，备份数据在私网传输时未加密；建议后续升级服务端 TLS。')
        print('回退：sudo python3 <此zip路径> --rollback（保留已经生成的备份）')
    except Exception as exc:
        print('安装失败，执行自动清理以恢复原状...', file=sys.stderr)
        try:
            run(['systemctl', 'disable', '--now', 'classroom-booking-db-backup.timer'], check=False)
            for path in reversed(made):
                path.unlink(missing_ok=True)
            run(['systemctl', 'daemon-reload'], check=False)
        except Exception as cleanup_exc:
            print('清理警告：' + str(cleanup_exc), file=sys.stderr)
        # Keep backup dir and any successful backup file; never delete user data.
        raise RuntimeError(str(exc))


def status():
    if os.geteuid() != 0:
        raise RuntimeError('请使用 sudo')
    if not STATE.exists():
        print('尚未安装此工具')
        return
    record = json.loads(STATE.read_text('utf-8'))
    print('状态：已安装', record['version'])
    print('备份数据库：', record['db_name'])
    print('备份目录：', record['backup_directory'])
    x = run(['systemctl', 'is-active', 'classroom-booking-db-backup.timer'], check=False)
    print('定时任务状态：', x.stdout.strip())
    backups = sorted(BACKUP_DIR.glob('classroom-booking-*.sql.gz')) if BACKUP_DIR.is_dir() else []
    print('备份文件数量：', len(backups))
    if backups:
        print('最新备份：', backups[-1])


def rollback():
    if os.geteuid() != 0:
        raise RuntimeError('请使用 sudo')
    if not STATE.exists():
        raise RuntimeError('找不到本工具的安装标记，拒绝删除任何文件')
    record = json.loads(STATE.read_text('utf-8'))
    if record.get('version') != 'b22-r3':
        raise RuntimeError('不是此版本创建的状态，拒绝回退')
    expected = record['files']
    for path in (RUNNER, SERVICE, TIMER):
        if not path.is_file() or path.is_symlink() or sha(path.read_bytes()) != expected[str(path)]:
            raise RuntimeError(f'文件内容已被更改，拒绝自动回退：{path}')
    if not CONF.is_file() or CONF.is_symlink():
        raise RuntimeError('备份凭据异常，拒绝自动回退')
    run(['systemctl', 'disable', '--now', 'classroom-booking-db-backup.timer'])
    for path in [TIMER, SERVICE, RUNNER, CONF]:
        path.unlink()
    run(['systemctl', 'daemon-reload'])
    STATE.unlink()
    print('已回退：只移除本工具的定时器、服务、备份程序和凭据；备份文件仍完整保存在', BACKUP_DIR)
    print('主项目、R21 和数据库均未修改。')


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--check', action='store_true', help='只读环境检查')
    actions.add_argument('--install', action='store_true', help='从 Django 读取凭据；测试权限、首次备份并启用定时器')
    actions.add_argument('--status', action='store_true', help='查看本工具运行状态')
    actions.add_argument('--once', action='store_true', help='手动运行一次备份（不恢复任何数据）')
    actions.add_argument('--rollback', action='store_true', help='安全卸载定时器及脚本，保留备份文件')
    args = parser.parse_args()
    if args.check:
        check_environment()
    elif args.install:
        with lock_installer():
            deploy()
    elif args.status:
        status()
    elif args.once:
        if not STATE.exists():
            raise RuntimeError('请先安装')
        record = json.loads(STATE.read_text('utf-8'))
        if record.get('version') != 'b22-r3' or not RUNNER.is_file() or RUNNER.is_symlink() or sha(RUNNER.read_bytes()) != record['files'][str(RUNNER)]:
            raise RuntimeError('备份脚本与安装记录不一致，拒绝执行')
        run([str(RUNNER)], capture=False, timeout=600)
    elif args.rollback:
        with lock_installer():
            rollback()


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as e:
        print('错误：' + str(e), file=sys.stderr)
        sys.exit(1)
