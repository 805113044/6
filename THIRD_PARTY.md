# 参考资料与第三方组件

## 设计参考

用户提供的《教室预约与管理网站_开源项目调研整合报告.pdf》（2026-10-15）用于明确功能和部署边界。采用其中的预约管理、教学占用、事务防冲突、独立子域名/容器/数据库设计思路。

报告列举的参考项目包括：

- [classroombookings](https://github.com/classroombookings/classroombookings)
- [MRBS](https://github.com/meeting-room-booking-system/mrbs-code)
- [RESMAN](https://github.com/krichdevs/RESMAN)
- [RoomFlow](https://github.com/IWKMS99/RoomFlow)

本次查阅了 classroombookings 与 MRBS 的 README，参考学校年课表、按区域/房间管理的组织方式。本仓库没有复制、合并这些项目的源码、图片、商标或数据库。报告内建议不是对本系统功能的完整承诺，具体已实现范围以 README 与测试为准。若以后引入上游代码，应先核查其实际版本和许可证，并保留相应版权声明。

## 实际运行依赖

依赖版本记录在 `requirements*.txt`。源代码与许可证由包管理器随官方发行包获取：

| 组件 | 上游 | 许可证 |
| --- | --- | --- |
| Django / asgiref | https://www.djangoproject.com/ / https://github.com/django/asgiref | BSD-3-Clause |
| sqlparse | https://github.com/andialbrecht/sqlparse | BSD-3-Clause |
| Gunicorn | https://gunicorn.org/ | MIT |
| WhiteNoise | https://github.com/evansd/whitenoise | MIT |
| packaging | https://github.com/pypa/packaging | Apache-2.0 OR BSD-2-Clause |
| mysqlclient | https://github.com/PyMySQL/mysqlclient | GPL-2.0-or-later |
| psycopg（可选） | https://www.psycopg.org/ | LGPL-3.0-or-later（附上游例外） |
| openpyxl / et_xmlfile | https://openpyxl.readthedocs.io/ | MIT |
| defusedxml | https://github.com/tiran/defusedxml | PSF-2.0 |
| typing_extensions（可选） | https://github.com/python/typing_extensions | PSF-2.0 |

Python、Debian 基础镜像、MariaDB 客户端库、可选 MySQL 服务镜像另有各自组件许可证。分发容器镜像时，保留其许可证及适用的源码获取义务；本仓库的源码许可证不替代第三方许可证。仓库自有源码采用 GPL-3.0-or-later。
