#!/usr/bin/env python3
"""Independent logical backup for classroom_booking. Do not run against any other DB."""
import datetime as dt
import fcntl
import gzip
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

DATA = pathlib.Path('/var/backups/classroom-booking/mysql')
CONF = pathlib.Path('/etc/classroom-booking-db-backup/credentials.cnf')
DB = 'classroom_booking'
KEEP_DAYS = 30
MIN_FREE_BYTES = 1024 * 1024 * 1024  # Minimum free space before starting.
NAME_RX = re.compile(r'^classroom-booking-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}\.sql\.gz$')


def command():
    exe = shutil.which('mysqldump')
    if not exe:
        raise RuntimeError('mysqldump 不存在，无法备份')
    result = subprocess.run([exe, '--help'], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError('mysqldump --help 失败')
    cmd = [exe, f'--defaults-extra-file={CONF}', '--single-transaction',
           '--quick', '--skip-lock-tables', '--no-tablespaces', '--hex-blob',
           '--triggers', '--default-character-set=utf8mb4']
    if '--set-gtid-purged' in result.stdout:
        cmd.append('--set-gtid-purged=OFF')
    if '--column-statistics' in result.stdout:
        cmd.append('--column-statistics=0')
    # MySQL 8 caching_sha2_password without server TLS. This protects the
    # authentication exchange but does not encrypt the SQL dump stream.
    cmd.extend(['--ssl-mode=DISABLED', '--get-server-public-key'])
    cmd.append(DB)
    return cmd


def prune():
    cutoff = time.time() - KEEP_DAYS * 86400
    for candidate in DATA.iterdir():
        if not candidate.is_file() or candidate.is_symlink():
            continue
        if NAME_RX.fullmatch(candidate.name) and candidate.stat().st_mtime < cutoff:
            sha = candidate.with_name(candidate.name + '.sha256')
            candidate.unlink()
            if sha.is_file() and not sha.is_symlink():
                sha.unlink()


def main():
    if os.geteuid() != 0:
        raise RuntimeError('此备份程序必须以 root 运行')
    if not CONF.is_file() or CONF.stat().st_mode & 0o077:
        raise RuntimeError('数据库备份凭据缺失或权限不安全')
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(DATA, 0o700)
    with (DATA / '.run.lock').open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一份教室数据库备份正在运行，已取消重叠执行')
        if shutil.disk_usage(DATA).free < MIN_FREE_BYTES:
            raise RuntimeError('剩余空间不足 1 GiB，拒绝启动备份以保护网站')
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        suffix = os.urandom(4).hex()
        name = f'classroom-booking-{stamp}-{suffix}.sql.gz'
        final = DATA / name
        fd, temp_name = tempfile.mkstemp(prefix='.classroom-backup-', suffix='.part', dir=DATA)
        temp = pathlib.Path(temp_name)
        os.close(fd)
        stderr_fd, stderr_name = tempfile.mkstemp(prefix='.backup-stderr-', dir=DATA)
        os.close(stderr_fd)
        stderr_path = pathlib.Path(stderr_name)
        try:
            with stderr_path.open('wb') as stderr_file:
                proc = subprocess.Popen(command(), stdout=subprocess.PIPE, stderr=stderr_file)
                try:
                    with temp.open('wb') as dest:
                        with gzip.GzipFile(fileobj=dest, mode='wb', compresslevel=6, mtime=0) as gz:
                            shutil.copyfileobj(proc.stdout, gz, length=512 * 1024)
                    proc.stdout.close()
                    exit_code = proc.wait(timeout=600)
                except Exception:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=10)
                    if proc.stdout:
                        proc.stdout.close()
                    raise
            if exit_code:
                err = stderr_path.read_text('utf-8', errors='replace')[:1000]
                raise RuntimeError('mysqldump 失败，未保存不完整文件；错误信息: ' + err)
            # Validate the entire compressed stream; this does not execute SQL.
            total_bytes, schema_seen = 0, False
            with gzip.open(temp, 'rb') as f:
                while True:
                    chunk = f.read(512 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if b'CREATE TABLE' in chunk or b'CREATE VIEW' in chunk:
                        schema_seen = True
            if total_bytes < 200 or not schema_seen:
                raise RuntimeError('SQL 内容异常或缺少表结构，拒绝保存')
            h = hashlib.sha256()
            with temp.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(chunk)
            os.chmod(temp, 0o600)
            if final.exists():
                raise RuntimeError('目标文件意外存在，拒绝覆盖')
            temp.replace(final)
            sha_tmp = final.with_name(final.name + '.sha256.tmp')
            sha_file = final.with_name(final.name + '.sha256')
            try:
                sha_tmp.write_text(f'{h.hexdigest()}  {name}\n', encoding='ascii')
                os.chmod(sha_tmp, 0o600)
                sha_tmp.replace(sha_file)
            finally:
                sha_tmp.unlink(missing_ok=True)
            prune()
            print(f'BACKUP=PASS FILE={final} COMPRESSED_BYTES={final.stat().st_size} SHA256={h.hexdigest()}')
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        finally:
            stderr_path.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'BACKUP=FAIL {exc}', file=sys.stderr)
        sys.exit(1)
