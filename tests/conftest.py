import importlib.util

# The core suite runs without Django installed; the Django suite needs it.
collect_ignore_glob = [] if importlib.util.find_spec("django") else ["django/*"]
