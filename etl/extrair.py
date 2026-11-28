"""Ingestão: download dos microdados no FTP do PDET e leitura em streaming dos arquivos .7z.

Os arquivos da RAIS chegam a 7 GB descompactados. Em vez de extrair para o disco, o
descompactador escreve o CSV na saída padrão e o PyArrow lê em blocos a partir do pipe.
Assim o disco só precisa guardar o .7z.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from ftplib import FTP, error_perm
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.csv as pacsv

from . import config

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ FTP
def _conectar() -> FTP:
    ftp = FTP(config.FTP_HOST, timeout=120)
    ftp.login()  # acesso anônimo, dados abertos
    ftp.encoding = "latin-1"
    return ftp


def listar(diretorio: str) -> list[str]:
    ftp = _conectar()
    try:
        ftp.cwd(diretorio)
        return ftp.nlst()
    finally:
        ftp.quit()


def baixar(caminho_remoto: str, destino: Path, tentativas: int = 5) -> Path:
    """Baixa um arquivo do FTP com retomada (REST) em caso de queda de conexão."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".part")
    for tentativa in range(1, tentativas + 1):
        try:
            ftp = _conectar()
            ftp.voidcmd("TYPE I")
            tamanho = ftp.size(caminho_remoto)
            if destino.exists() and destino.stat().st_size == tamanho:
                ftp.quit()
                return destino
            inicio = parcial.stat().st_size if parcial.exists() else 0
            t0 = time.time()
            with open(parcial, "ab") as fh:
                ftp.retrbinary(f"RETR {caminho_remoto}", fh.write, blocksize=1 << 20,
                               rest=inicio or None)
            ftp.quit()
            if parcial.stat().st_size != tamanho:
                raise IOError(f"tamanho divergente: {parcial.stat().st_size} != {tamanho}")
            parcial.replace(destino)
            mb = tamanho / 1e6
            log.info("baixado %s (%.0f MB em %.0f s)", destino.name, mb, time.time() - t0)
            return destino
        except error_perm:
            raise
        except Exception as exc:  # queda de rede, timeout
            log.warning("falha ao baixar %s (tentativa %d): %s", caminho_remoto, tentativa, exc)
            time.sleep(10 * tentativa)
    raise RuntimeError(f"não foi possível baixar {caminho_remoto}")


# ------------------------------------------------------------------ descompactação em streaming
def _comando_descompactar(arquivo: Path) -> list[str]:
    for nome in ("7zz", "7z", "7za"):
        exe = shutil.which(nome)
        if exe:
            return [exe, "e", "-so", str(arquivo)]
    tar_windows = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if tar_windows.exists():  # bsdtar do Windows 10/11 lê .7z
        return [str(tar_windows), "-xOf", str(arquivo)]
    bsdtar = shutil.which("bsdtar")
    if bsdtar:
        return [bsdtar, "-xOf", str(arquivo)]
    raise RuntimeError("instale o 7-Zip (7z) ou o bsdtar para ler arquivos .7z")


def ler_7z_em_blocos(arquivo: Path, colunas: list[str], sep: str, encoding: str,
                      bloco: int = config.BLOCO_LEITURA) -> Iterator[pa.RecordBatch]:
    """Gera RecordBatches do CSV compactado, lendo só as colunas pedidas, todas como texto.

    Ler como texto evita que um valor fora do padrão derrube a leitura. A conversão de
    tipos é feita depois, de forma controlada, na etapa de tratamento.
    """
    cmd = _comando_descompactar(arquivo)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 20)
    try:
        leitor = pacsv.open_csv(
            proc.stdout,
            read_options=pacsv.ReadOptions(block_size=bloco, encoding=encoding),
            parse_options=pacsv.ParseOptions(delimiter=sep, quote_char='"'),
            convert_options=pacsv.ConvertOptions(
                include_columns=colunas,
                column_types={c: pa.string() for c in colunas},
                strings_can_be_null=True,
            ),
        )
        for lote in leitor:
            yield lote
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def _primeira_linha(arquivo: Path) -> bytes:
    cmd = _comando_descompactar(arquivo)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        return proc.stdout.readline()
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def cabecalho_7z(arquivo: Path, encoding: str | None = None) -> tuple[str, str]:
    """Primeira linha do CSV compactado e a codificação detectada.

    O PDET não é consistente: no Novo CAGED, os arquivos MOV vêm em Latin-1 e os FOR/EXC
    em UTF-8. Se a linha decodifica como UTF-8, usamos UTF-8; senão, Latin-1.
    """
    bruto = _primeira_linha(arquivo)
    if encoding is None:
        try:
            bruto.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "latin-1"
    return bruto.decode(encoding), encoding
