import os
from pathlib import Path


def load_env_file(path):
    """
    Minimaler .env-Loader (KEY=VALUE pro Zeile, # = Kommentar).
    Bereits gesetzte Umgebungsvariablen werden NICHT überschrieben.
    """
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
