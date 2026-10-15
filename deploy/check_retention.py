import time
from pathlib import Path

assert time.time() - Path("/tmp/classroom-retention-heartbeat").stat().st_mtime < 150
