# Repository guidance

Use the existing checkout in this isolated task; do not create Git worktrees unless explicitly requested. Read README.md and docs/DEVELOPMENT.md before changes.

- Only administrators have accounts. Other users book anonymously, providing name, school/employee ID, department, contact and a per-booking edit password. Old role fields are compatibility metadata only.
- First submission wins immediately; no student approvals. Public schedules show borrower names and time only. Other details require admin status or verification for that specific booking.
- Per-booking passwords must be hashed; never persist raw passwords in sessions/logs. A guest grant expires in 15 minutes and is bound to booking ID and credential_version. Check version again under the transaction lock on mutations.
- All occupancy writes use services.py/timetable.py. Lock rooms first, in primary-key order for moves. Any edit/import conflict rolls back. Admin returns release time; resubmission must reacquire it. Keep Django admin booking models read-only and use the custom edit flow.
- Timetable rules support semester weeks, odd/even parity, exclusions and rolling materialization. Preserve stable course_key exceptions so cancelled or edited occurrences do not regenerate.
- Retain concrete booking rows at most 30 days from creation; edits must not extend retention. Timetable configuration contains no student booking details. Production online retention is 21 days with backups at most 7 days.
- Do not commit .env, database files, backups, credentials or real user data.
- Validate with DEBUG=true: collectstatic, makemigrations --check --dry-run, and test. MySQL is required for SQL/transaction/concurrency changes; SQLite skips locking tests.
- Deployment remains a distinct Compose project, loopback-only app port, subdomain and database credentials. Preserve the existing main site.

- AcademicTerm controls dates, first week and the borrowing switch. BorrowPeriod has five fixed periods; guests must never submit custom clocks. Courses can have exact times and block every overlapping fixed period.
- Lock the term before room locks on create/edit/import. School configuration updates use the same term lock. Do not move existing future bookings by silently changing period clocks.
- Excel import must create missing rooms atomically, including preview rollback. Keep unknown capacity/floor NULL. Read the first worksheet and accept supported Chinese column aliases.
- Single-date official courses also need durable teaching configuration; never let reservation TTL drop future course occupancy. Include ongoing classes when materializing.
- Desktop and phone share the same day/week table with fixed headers, period clocks and horizontal/vertical scrolling. Ongoing free periods can be booked immediately; owners can edit/cancel until the period ends.
