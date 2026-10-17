"""Non-invasive security audit; never displays usernames, hashes, secrets or student data."""
import ipaddress
import os
import stat
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.contrib.auth.hashers import make_password,check_password

class Command(BaseCommand):
    help = 'Read-only configuration/database privilege audit, without printing credentials.'
    def handle(self,*args,**kwargs):
        issues=[]
        def flag(test, msg):
            if test:issues.append(msg)
        flag(settings.DEBUG,'DEBUG 已启用')
        flag(not settings.SESSION_COOKIE_SECURE,'会话 Cookie 未强制 HTTPS')
        flag(not settings.CSRF_COOKIE_SECURE,'CSRF Cookie 未强制 HTTPS')
        flag(not settings.SESSION_COOKIE_HTTPONLY,'会话 Cookie 允许 JavaScript 读取')
        flag(not settings.SECURE_SSL_REDIRECT,'未强制 HTTPS 重定向')
        flag(not settings.SECRET_KEY or len(settings.SECRET_KEY)<40,'SECRET_KEY 强度不足')
        plaintext='r14-audit-only-not-real-secret'
        encoded=make_password(plaintext)
        if (encoded==plaintext or not check_password(plaintext,encoded)
            or not encoded.startswith('pbkdf2_sha256_peppered_v1$')):
            raise RuntimeError('Django 密码哈希自检失败')
        self.stdout.write('Django 加盐单向密码哈希自检：通过（不输出哈希）')
        default=settings.DATABASES['default']
        vendor=default.get('ENGINE','')
        if 'mysql' in vendor or 'postgresql' in vendor:
            host=default.get('HOST','')
            try:
                if not ipaddress.ip_address(host).is_private:
                    issues.append('数据库目标不是私网地址；请核实公网访问与白名单')
            except ValueError:
                if host not in ('localhost',''):
                    issues.append('数据库使用域名；请确认解析到私网并关闭公网访问')
            if 'mysql' in vendor:
                if not default.get('OPTIONS',{}).get('ssl'):
                    issues.append('未配置 DB_SSL_CA；请核实数据库链路 TLS 与私网隔离')
                try:
                    with connection.cursor() as cursor:
                        cursor.execute('SHOW GRANTS FOR CURRENT_USER')
                        grants=' '.join(str(row[0]).upper() for row in cursor.fetchall())
                    if any(word in grants for word in ('ALL PRIVILEGES','GRANT OPTION',' SUPER ',' FILE ')):
                        issues.append('数据库应用账户可能具备过宽权限；需人工检查授权范围')
                except Exception:
                    issues.append('无法读取数据库自身授权信息，请人工审计')
        env=Path(settings.BASE_DIR)/'.env'
        if env.exists() and not env.is_symlink():
            flag(bool(stat.S_IMODE(env.stat().st_mode)&0o077),'.env 允许同组/其他用户访问')
        if issues:
            for issue in issues:self.stdout.write('需核实：'+issue)
        else:self.stdout.write('本次本机检查未发现所覆盖项目中的明显弱配置')
        self.stdout.write('提醒：本审计不能证明数据库或主机绝对不会被盗；须额外检查腾讯云安全组、数据库公网开关及备份权限。')
