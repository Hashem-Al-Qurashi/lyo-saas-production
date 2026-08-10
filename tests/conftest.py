"""
Conftest: ensure deployed-live/bot is on sys.path FIRST.

The root-level business_context.py and deployed-live/bot/business_context.py co-exist.
Root version is used by test_business_context / test_operator_booking (they import
business_context directly). Bot version is needed by salon_bot_with_booking.

Strategy: put BOT_DIR first, then pre-import salon_bot_with_booking here so its
business_context dependency resolves to BOT_DIR before other tests cache root version.
"""
import sys
import os

BOT_DIR = os.path.join(os.path.dirname(__file__), '..', 'deployed-live', 'bot')
BOT_DIR = os.path.abspath(BOT_DIR)

if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

os.environ.setdefault("OPENAI_API_KEY", "test-key")

# Pre-import so salon_bot_with_booking caches the BOT_DIR business_context.
# Must happen before test_business_context.py runs and caches root business_context.
import importlib.util as _util
import importlib as _il

_bc_path = os.path.join(BOT_DIR, 'business_context.py')
_spec = _util.spec_from_file_location('business_context', _bc_path)
_bc_mod = _util.module_from_spec(_spec)
sys.modules['business_context'] = _bc_mod
_spec.loader.exec_module(_bc_mod)

import salon_bot_with_booking  # noqa: F401  — registers in sys.modules with BOT_DIR bc
