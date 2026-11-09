"""Vendored IFEval instruction registry (Apache-2.0).

Source: josejg/instruction_following_eval @ 0c495b2f95155e8b10acb919ae283bfb4d5be6e2,
the mirror pinned in ``evals/data/sources.json``. Only the registry modules are
vendored; the upstream ``evaluation.py`` CLI (typer/rich) is not needed because
``evals/run_eval.py`` drives the strict check itself.
"""

__all__ = ["instructions", "instructions_registry", "instructions_util"]
