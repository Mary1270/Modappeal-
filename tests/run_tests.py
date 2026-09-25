import sys
import os
import importlib
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stub_genlayer  # noqa: E402  installs sys.modules["genlayer"]

TEST_MODULES = [
    "test_appeal",
    "test_commit_reveal",
    "test_escalation",
    "test_deadlock",
    "test_economics",
    "test_evidence_freeze",
    "test_accounting_invariant",
    "test_auto_verdict",
]


def main():
    passed = 0
    failed = 0
    for mod_name in TEST_MODULES:
        module = importlib.import_module(mod_name)
        for name in dir(module):
            if name.startswith("test_"):
                fn = getattr(module, name)
                if callable(fn):
                    try:
                        fn()
                        print(f"PASS  {mod_name}.{name}")
                        passed += 1
                    except Exception:
                        print(f"FAIL  {mod_name}.{name}")
                        traceback.print_exc()
                        failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
