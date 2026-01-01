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

    def _run(self, args: List[str], *, allow_empty: bool = False) -> str:
        try:
            proc = subprocess.run(args, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"lncli failed ({exc.returncode}): {exc.stderr.strip()}") from exc
        output = proc.stdout.strip()
        if not output and not allow_empty:
            raise RuntimeError("lncli returned empty output")
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

    def updatechanpolicy(
        self,
        chan_point: str,
        fee_rate_ppm: int,
        *,
        base_fee_msat: int = 0,
        time_lock_delta: int = 144,
        inbound_fee_rate_ppm: Optional[int] = None,
        inbound_base_fee_msat: int = 0,
        dry_run: bool = False,
    ) -> Optional[str]:
        args = self._build_command(["updatechanpolicy"], include_format=False)
        args.extend(
            [
                "--base_fee_msat",
                str(int(base_fee_msat)),
                "--time_lock_delta",
                str(int(time_lock_delta)),
                "--fee_rate_ppm",
                str(max(0, int(fee_rate_ppm))),
                "--chan_point",
                chan_point,
            ]
        )
        if inbound_fee_rate_ppm is not None:
            args.extend(["--inbound_base_fee_msat", str(int(inbound_base_fee_msat))])
            args.extend(["--inbound_fee_rate_ppm", str(int(inbound_fee_rate_ppm))])
        if dry_run:
            return f"[dry-run] {' '.join(args)}"
        try:
            output = self._run(args, allow_empty=True)
        except RuntimeError as exc:
            message = str(exc).lower()
            if inbound_fee_rate_ppm is None:
                raise
            if "flag provided but not defined" not in message and "unknown flag" not in message:
                raise
            if "inbound" not in message:
                raise
            fallback_args = self._build_command(["updatechanpolicy"], include_format=False)
            fallback_args.extend(
                [
                    "--base_fee_msat",
                    str(int(base_fee_msat)),
                    "--time_lock_delta",
                    str(int(time_lock_delta)),
                    "--fee_rate_ppm",
                    str(max(0, int(fee_rate_ppm))),
                    "--chan_point",
                    chan_point,
                ]
            )
            output = self._run(fallback_args, allow_empty=True)
        return output or None
