"""Run as a separate process, and dies at the worst moment of an import.

The data is written but not committed, and os._exit gives Python no chance to clean up.
That's the same as Render killing the instance.
"""

import os

from app.services.ingestion import handlers
from app.services.jobs import run_next

handlers.before_import_commit = lambda: os._exit(9)
run_next("worker-that-crashes")
