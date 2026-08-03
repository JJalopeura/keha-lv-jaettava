"""Re-exports so existing `import ... services as svc; svc.xxx(...)` call sites
keep working unchanged after the Phase 2 split into core/jobs/intake/application.
See /workspace/plan.md Phase 2.
"""

from .core import *  # noqa: F401,F403
from .jobs import *  # noqa: F401,F403
from .intake import *  # noqa: F401,F403
from .application import *  # noqa: F401,F403
