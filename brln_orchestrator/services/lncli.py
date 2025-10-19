from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any, Dict, List, Optional


class LncliService:
    """Wrapper around lncli to ensure JSON output."""

    def __init__(self, path: str = "lncli") -> None:
        self._raw_path = path.strip()

    def _build_command(self, base_args: Optional[List[str]] = None, *, include_format: bool = True) -> List[str]:
        parts = shlex.split(self._raw_path) or ["lncli"]
        args = list(parts)
        if base_args:
            args.extend(base_args)
        if include_format and "--format" not in args:
            args.extend(["--format", "json"])
        return args

    def _run(self, args: List[str]) -> str:
        try:
            proc = subprocess.run(args, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"lncli failed ({exc.returncode}): {exc.stderr.strip()}") from exc
        output = proc.stdout.strip()
        if not output:
            raise RuntimeError("lncli returned empty output for listchannels")
        return output

    def listchannels(self) -> Dict[str, Any]:
        args = self._build_command(["listchannels"])
        try:
            output = self._run(args)
        except RuntimeError as exc:
            message = str(exc).lower()
            if "flag provided but not defined" not in message:
                raise
            fallback_args = self._build_command(["listchannels"], include_format=False)
            output = self._run(fallback_args)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from lncli: {exc}") from exc
