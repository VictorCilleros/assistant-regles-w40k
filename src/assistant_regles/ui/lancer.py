"""Commande ``uv run interface-regles`` : lance l'interface Streamlit.

Se place à la racine du repo avant de lancer Streamlit, qui y lit
``.streamlit/config.toml`` (thème). Les options supplémentaires sont transmises
à ``streamlit run`` (ex. ``uv run interface-regles --server.port 8502``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from assistant_regles.ingest.config import trouver_racine


def main() -> None:
    from streamlit.web import cli as stcli

    os.chdir(trouver_racine())
    sys.argv = ["streamlit", "run", str(Path(__file__).with_name("app.py")), *sys.argv[1:]]
    sys.exit(stcli.main())
