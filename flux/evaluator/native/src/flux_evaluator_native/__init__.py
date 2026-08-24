"""Native, in-repo roofline evaluator backed by the real `flux-core` Rust crate (docs/decisions.md D75)."""

from __future__ import annotations

from .adapter import NativeEvaluator
from .build import NativeBuildError, ensure_native_extension
from .errors import NotExpressibleError

__all__ = ["NativeEvaluator", "NativeBuildError", "ensure_native_extension", "NotExpressibleError"]
