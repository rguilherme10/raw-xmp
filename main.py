"""Entry point:  python main.py <command> ...   (see docs/CLI.md)."""
import os
import sys

for _v in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):   # before numpy is imported (also by spawned workers)
    os.environ.setdefault(_v, "1")

try:
    from rawxmp.cli.main import main
except ModuleNotFoundError as exc:      # e.g. a terminal/venv where the dependencies were not installed
    sys.exit(f"[ERROR] Dependência ausente: {exc.name}.\n"
             f"        Instale com:  {sys.executable} -m pip install -r requirements.txt")

if __name__ == "__main__":
    sys.exit(main())
