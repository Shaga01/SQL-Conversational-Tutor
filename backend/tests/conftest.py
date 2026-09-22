import os
import tempfile

# Isolate test data from the developer's real data directory.
os.environ.setdefault("TUTOR_DATA_DIR", tempfile.mkdtemp(prefix="sqltutor-test-"))
