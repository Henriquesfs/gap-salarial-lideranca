"""Download em paralelo dos microdados, com limite de espaço em disco.

Os arquivos são baixados em data/raw/. O processamento apaga cada .7z depois de lido, e
o download espera sempre que a pasta passa do limite configurado.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, extrair

log = logging.getLogger(__name__)


def competencias(inicio: int, fim: int) -> list[int]:
    out, a, m = [], inicio // 100, inicio % 100
    while a * 100 + m <= fim:
        out.append(a * 100 + m)
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return out


def tarefas_rais(anos: list[int]) -> list[tuple[str, Path]]:
    t = []
    for ano in sorted(anos, reverse=True):
        for arq in config.ARQUIVOS_RAIS:
            remoto = f"{config.FTP_RAIS.format(ano=ano)}/{arq}"
            t.append((remoto, config.DIR_BRUTOS / "rais" / str(ano) / arq))
    return t


def tarefas_caged(inicio: int, fim: int) -> list[tuple[str, Path]]:
    t = []
    for comp in competencias(inicio, fim):
        pasta = config.FTP_CAGED.format(ano=comp // 100, competencia=comp)
        for tipo in ("MOV", "FOR", "EXC"):
            nome = f"CAGED{tipo}{comp}.7z"
            t.append((f"{pasta}/{nome}", config.DIR_BRUTOS / "caged" / nome))
    return t


def _ocupado() -> int:
    if not config.DIR_BRUTOS.exists():
        return 0
    return sum(p.stat().st_size for p in config.DIR_BRUTOS.rglob("*") if p.is_file())


def marcador(destino: Path) -> Path:
    """Arquivos já processados ficam marcados com .ok no staging e não são baixados de novo."""
    return config.DIR_STAGING / "concluidos" / f"{destino.parent.name}_{destino.name}.ok"


def _processado(destino: Path) -> bool:
    return marcador(destino).exists()


def baixar_todos(tarefas: list[tuple[str, Path]], paralelo: int = 4, limite_gb: float = 6.0) -> None:
    limite = limite_gb * 1e9

    def um(t):
        remoto, destino = t
        if _processado(destino) or destino.exists():
            return
        while _ocupado() > limite:
            time.sleep(15)
        try:
            extrair.baixar(remoto, destino)
        except Exception as exc:  # arquivo FOR/EXC pode não existir em algum mês
            log.warning("ignorado %s: %s", remoto, exc)

    with ThreadPoolExecutor(paralelo) as ex:
        list(ex.map(um, tarefas))
