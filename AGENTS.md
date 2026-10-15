# Repository guidance

Use the existing checkout in this isolated task; do not create Git worktrees unless explicitly requested. Read README.md and docs/DEVELOPMENT.md before changes.

- Only administrators have accounts. Other users book anonymously, providing name, school/employee ID, department, contact and a per-booking edit password. Old role fields are compatibility metadata only.
- First submission wins immediately; no student approvals. Public schedules show borrower names and time only. Other details require admin status or verification for that specific booking.
- Per-booking passwords must be hashed; never persist raw passwords in sessions/logs. A guest grant expires in 15 minutes and is bound to booking ID and credential_version. Check version again under the transaction lock on mutations.
- All occupancy writes use services.py/timetable.py. Lock rooms first, in primary-key order for moves. Any edit/import conflict rolls back. Admin returns release time; resubmission must reacquire it. Keep Django admin booking models read-only and use the custom edit flow.
- Timetable rules support semester weeks, odd/even parity, exclusions and rolling materialization. Preserve stable course_key exceptions so cancelled or edited occurrences do not regenerate.
- Retain concrete booking rows at most 60 days from creation; edits must not extend retention. Timetable configuration contains no student booking details. Production online retention is 45 days with backups at most 14 days.
- Do not commit .env, database files, backups, credentials or real user data.
- Validate with DEBUG=true: collectstatic, makemigrations --check --dry-run, and test. MySQL is required for SQL/transaction/concurrency changes; SQLite skips locking tests.
- Deployment remains a distinct Compose project, loopback-only app port, subdomain and database credentials. Preserve the existing main site.
