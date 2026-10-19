"""Isolate the module-level FastAPI application before pytest imports any test.

Individual tests also create their own temporary stores. This protects ordinary
library data from the eager application singleton and inherited subprocesses.
"""
import os
import tempfile

os.environ.setdefault('AI_LIBRARY_DATA_DIR', tempfile.mkdtemp(prefix='ai-library-pytest-runtime-'))
