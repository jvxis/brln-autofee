from __future__ import annotations

import shlex
import subprocess
from typing import Optional


class BosService:
    def __init__(self, path: str = "bos") -> None:
        self._raw_path = path.strip()

    def set_fee(self, pubkey: str, ppm: int, *, dry_run: bool = False) -> Optional[str]:
        args = shlex.split(self._raw_path) or ["bos"]
        args.extend(["fees", "--to", pubkey, "--set-fee-rate", str(int(ppm))])
        if dry_run:
            return f"[dry-run] {' '.join(args)}"
        try:
            proc = subprocess.run(args, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"bos failed ({exc.returncode}): {exc.stderr.strip()}") from exc
        return proc.stdout.strip() or None

