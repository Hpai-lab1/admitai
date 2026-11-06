"""Module C tests. Pure-logic tests always run; database tests need TEST_DATABASE_URL and
TEST_ADMIN_DATABASE_URL (a throw-away database) and are skipped otherwise, e.g. in CI."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault(
    "APP_ENV", "development"
)  # tests use the shared-secret, development set-up
# Tests are hermetic: the developer's real keys (local.env.sh) must not change how tokens are checked.
for _k in (
    "JWT_PRIVATE_KEY_FILE",
    "JWT_PUBLIC_KEY_FILE",
    "VOICE_TOKEN_SECRET",
    "KEY_OPS",
    "KEY_C_TO_A",
    "KEY_B_TO_A",
    "KEY_C_TO_B",
    "KEY_A_TO_B",
    "KEY_B_TO_C",
    "KEY_A_TO_C",
    "KB_EXPERIENCE_API_URL",
):
    os.environ.pop(_k, None)
