"""Ingestão por APIs públicas de dados abertos (respostas em JSON).

- IBGE, API de Localidades: lista de UFs com a região aninhada em cada registro. Alimenta a
  dimensão dim_uf.
- Banco Central, SGS (Sistema Gerenciador de Séries Temporais): série 1619 (salário mínimo)
  e série 433 (IPCA, variação mensal). O salário mínimo define os limites de validade do
  salário-hora no ETL; o IPCA fica disponível para converter valores nominais em reais.

As respostas brutas são guardadas como recebidas (JSON) em data/referencias e na tabela
api_coleta. Se uma API estiver fora do ar, o pipeline segue com os valores de referência
de config.py e dominios.py e registra o aviso no log.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from . import config, dominios as d

log = logging.getLogger(__name__)

URL_IBGE_UFS = "https://servicodados.ibge.gov.br/api/v1/localidades/estados"
URL_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
SERIES_SGS = {"salario_minimo": 1619, "ipca_mensal": 433}

COLETAS: list[dict] = []  # respostas brutas desta execução, carregadas em api_coleta


def _get_json(url: str, params: dict | None = None, tentativas: int = 3):
    for i in range(1, tentativas + 1):
        try:
            r = requests.get(url, params=params, timeout=60, headers={"Accept": "application/json"})
            r.raise_for_status()
            dados = r.json()
            COLETAS.append({"url": r.url, "coletado": datetime.now(timezone.utc).isoformat(),
                            "status": r.status_code, "resposta": dados})
            return dados
        except Exception as exc:
            log.warning("falha em %s (tentativa %d): %s", url, i, exc)
            time.sleep(5 * i)
    raise RuntimeError(f"API indisponível: {url}")


def _salvar(nome: str, dados) -> None:
    destino = config.DIR_DADOS / "referencias" / f"{nome}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def ufs_ibge() -> pd.DataFrame:
    """dim_uf a partir do IBGE. Achata o objeto aninhado `regiao` em colunas."""
    try:
        dados = _get_json(URL_IBGE_UFS)
        _salvar("ibge_estados", dados)
        df = pd.json_normalize(dados)
        df = df.rename(columns={"id": "uf", "regiao.id": "regiao", "regiao.nome": "regiao_nome"})
        df = df[["uf", "sigla", "nome", "regiao", "regiao_nome"]].astype({"uf": "int16", "regiao": "int16"})
        df = df.drop_duplicates("uf").sort_values("uf").reset_index(drop=True)
        divergentes = set(df["uf"]) ^ set(d.UF_SIGLA)
        if divergentes:
            log.warning("UFs do IBGE diferentes da tabela interna: %s", sorted(divergentes))
        return df
    except RuntimeError:
        log.warning("usando a tabela interna de UFs")
        return pd.DataFrame({
            "uf": list(d.UF_SIGLA), "sigla": list(d.UF_SIGLA.values()),
            "nome": [d.UF_NOME[u] for u in d.UF_SIGLA], "regiao": [u // 10 for u in d.UF_SIGLA],
            "regiao_nome": [d.REGIAO_NOME[u // 10] for u in d.UF_SIGLA],
        })


def serie_sgs(nome: str, inicio: str = "01/01/2023", fim: str | None = None) -> pd.DataFrame:
    """Série mensal do SGS em formato longo: serie, competencia (AAAAMM), valor."""
    fim = fim or datetime.now().strftime("%d/%m/%Y")
    dados = _get_json(URL_SGS.format(codigo=SERIES_SGS[nome]),
                      {"formato": "json", "dataInicial": inicio, "dataFinal": fim})
    _salvar(f"sgs_{nome}", dados)
    df = pd.DataFrame(dados)
    data = pd.to_datetime(df["data"], format="%d/%m/%Y")
    return pd.DataFrame({
        "serie": nome,
        "competencia": (data.dt.year * 100 + data.dt.month).astype("int32"),
        "valor": pd.to_numeric(df["valor"], errors="coerce"),
    }).dropna().drop_duplicates(["serie", "competencia"])


def atualizar_salario_minimo() -> dict[int, float]:
    """Troca os valores fixos de config.SALARIO_MINIMO pelo valor vigente em dezembro de
    cada ano (ou no último mês publicado do ano corrente), vindo do Banco Central."""
    try:
        sm = serie_sgs("salario_minimo")
    except RuntimeError:
        log.warning("salário mínimo do Banco Central indisponível; usando config.py")
        return config.SALARIO_MINIMO
    ultimo = sm.sort_values("competencia").groupby(sm["competencia"] // 100)["valor"].last()
    novo = {int(a): float(v) for a, v in ultimo.items()}
    for ano, valor in novo.items():
        anterior = config.SALARIO_MINIMO.get(ano)
        if anterior is not None and abs(anterior - valor) > 0.01:
            log.warning("salário mínimo %d: config.py tem %.2f, Banco Central tem %.2f", ano, anterior, valor)
    config.SALARIO_MINIMO.update(novo)
    log.info("salário mínimo (Banco Central): %s", novo)
    return config.SALARIO_MINIMO


def series_referencia() -> pd.DataFrame:
    """Salário mínimo e IPCA mensal, para a tabela ref_serie_mensal."""
    partes = []
    for nome in SERIES_SGS:
        try:
            partes.append(serie_sgs(nome))
        except RuntimeError:
            log.warning("série %s indisponível", nome)
    if not partes:
        return pd.DataFrame(columns=["serie", "competencia", "valor"])
    return pd.concat(partes, ignore_index=True)
