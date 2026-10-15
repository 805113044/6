# Repository guidance

This is a Django 5.2 school classroom booking app. Use the existing checkout in this isolated task environment; do not create Git worktrees unless explicitly requested.

- Read README.md and docs/DEVELOPMENT.md before changing booking rules.
- Guests are students: collect their details, require approval, and show a random lookup capability once. Never expose student details in public schedules, URLs, request logs, or aggregate exports.
- Teachers use administrator-created accounts and book without approval. Staff permissions are scoped by room department; only superusers administer users and global/shared rooms.
- All occupancy writes must use booking/services.py and its room-first transaction locking protocol. Keep Django admin bookings read-only. Pending and approved bookings both reserve time.
- Production uses MySQL 8. SQLite is for local single-user development and skips the concurrency test.
- Keep data retention bounded at 60 days from creation; preserve scheduled deletion and audit cascade behavior. Production templates use 45 online days with at most 14 backup days.
- Do not commit .env, database files, credentials, backups, or real student data.
- To validate local changes: set DEBUG=true; run `python manage.py collectstatic --noinput`, `python manage.py makemigrations --check --dry-run`, and `python manage.py test`. Run the MySQL suite for changes involving transactions, queries or concurrency. See CI for isolated MySQL setup.
- Deployment must remain a separate Compose project and subdomain, with loopback-only app port and dedicated database credentials. Preserve the existing main site.
