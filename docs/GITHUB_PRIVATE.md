# 把 805113044/6 改为私有

本次未改变仓库可见性：GitHub 设置 API 的 HTTPS CONNECT 返回 403 Forbidden，当前无法访问设置 API；Git 推送仍可用。这不等于已确认你的 GitHub 账号没有管理员权限，也不需要在聊天中提供令牌。

1. 使用仓库所有者或拥有仓库 Admin 权限的 GitHub 账号，打开 https://github.com/805113044/6/settings 。
2. 在 General 页面滚动到底部 Danger Zone。
3. 找到 Change repository visibility，点击 Change visibility，选择 Make private（或 Change to private）。
4. 按 GitHub 确认窗口的要求勾选/输入 `805113044/6` 并提交。
5. 返回仓库主页确认显示 Private；用未登录窗口检查页面不再公开可见。

如果看不到 Settings/可见性按钮，检查是否用了正确账号、是否拥有 Admin 权限，以及是否存在组织可见性策略。改为私有后仍可登录 GitHub，使用 Code → Download ZIP 下载部署源码，不需要把数据库密码上传 GitHub。
