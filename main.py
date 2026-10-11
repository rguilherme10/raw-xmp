"""Entry point:  python main.py <command> ...   (see docs/CLI.md)."""
import sys

try:
    from rawxmp.cli.main import main
except ModuleNotFoundError as exc:      # e.g. a terminal/venv where the dependencies were not installed
    sys.exit(f"[ERROR] Dependência ausente: {exc.name}.\n"
             f"        Instale com:  {sys.executable} -m pip install -r requirements.txt")

if __name__ == "__main__":
    sys.exit(main())
