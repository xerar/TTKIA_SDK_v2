"""
Aislamiento del entorno para toda la suite.

MOTIVO: TTKIAClient resuelve credenciales en cascada —argumentos explícitos,
luego variables de entorno, luego ~/.ttkia/config.json—. Esa cascada es
correcta en producción y veneno en un test: en la máquina de cualquiera que
haya ejecutado `ttkia config` alguna vez, los tests que comprueban que el
cliente REVIENTA sin credenciales encuentran las del desarrollador y pasan de
largo. `test_no_auth_raises` y `test_no_url_raises` fallaban exactamente así,
y solo fuera de CI, que es la peor forma de fallar: verde donde nadie mira,
rojo en el portátil de quien acaba de tocar otra cosa.

La fixture es `autouse`: ningún test debe leer el $HOME de nadie, y quien
añada uno nuevo hereda el aislamiento sin acordarse de pedirlo.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_client_config(monkeypatch, tmp_path):
    """Corta las dos vías de configuración implícita: fichero y entorno."""
    # Se apunta a una ruta que no existe en lugar de borrar el atributo: el
    # código hace `_CONFIG_FILE.exists()`, así que necesita un Path válido.
    monkeypatch.setattr(
        "ttkia_sdk.client._CONFIG_FILE",
        tmp_path / "no-such-config.json",
    )
    for var in ("TTKIA_URL", "TTKIA_API_KEY", "TTKIA_TOKEN"):
        monkeypatch.delenv(var, raising=False)
