"""Ingestão incremental do Novo CAGED, pensada para rodar agendada no GitHub Actions.

1. Consulta no banco a última competência carregada.
2. Lista no FTP do PDET os meses publicados depois dela.
3. Baixa e trata só esses meses (MOV, FOR e EXC).
4. Soma os novos agregados aos existentes (upsert aditivo). Isso é necessário porque os
   arquivos FOR e EXC de um mês novo alteram competências antigas.

Se não houver mês novo, a execução só registra a verificação. Isso também mantém o
serviço gratuito da Aiven ativo, já que serviços sem uso podem ser desligados.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

from . import baixar, caged, carregar, cbo, config, extrair

log = logging.getLogger(__name__)

TABELAS = {
    "fato_caged_perfil": (caged.CHAVES_PERFIL, caged.MEDIDAS, "perfil"),
    "fato_caged_motivo": (caged.CHAVES_MOTIVO, ["n_mov"], "motivo"),
    "fato_caged_setor": (caged.CHAVES_SETOR, ["n_mov"], "setor"),
}


def meses_publicados(a_partir_de: int) -> list[int]:
    out = []
    for ano in range(a_partir_de // 100, a_partir_de // 100 + 2):
        try:
            nomes = extrair.listar(f"/pdet/microdados/NOVO CAGED/{ano}")
        except Exception:
            continue
        out += [int(n) for n in nomes if n.isdigit() and len(n) == 6 and int(n) > a_partir_de]
    return sorted(out)


def _upsert(cur, tabela: str, df: pd.DataFrame, chaves: list[str], medidas: list[str]) -> None:
    cur.execute(f"CREATE TEMP TABLE tmp (LIKE {tabela} INCLUDING DEFAULTS) ON COMMIT DROP")
    carregar._copiar(cur, "tmp", df[chaves + medidas])
    soma = ", ".join(f"{m} = {tabela}.{m} + EXCLUDED.{m}" for m in medidas)
    cur.execute(f"""
        INSERT INTO {tabela} ({', '.join(chaves + medidas)})
        SELECT {', '.join(chaves + medidas)} FROM tmp
        ON CONFLICT ({', '.join(chaves)}) DO UPDATE SET {soma}""")
    cur.execute("DROP TABLE tmp")


def executar() -> None:
    with carregar.conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(competencia), min(competencia) FROM dim_competencia")
        ultima, primeira = cur.fetchone()
        cur.execute("SELECT count(*) FROM fato_caged_perfil")
        log.info("banco ativo: competências %s a %s, %s linhas no fato CAGED", primeira, ultima, cur.fetchone()[0])

    novos = meses_publicados(ultima)
    if not novos:
        log.info("nenhuma competência nova no FTP após %s", ultima)
        return
    log.info("competências novas: %s", novos)
    config.CAGED_INICIO, config.CAGED_FIM = primeira, max(novos)

    tarefas = [t for c in novos for t in baixar.tarefas_caged(c, c)]
    baixar.baixar_todos(tarefas, paralelo=3, limite_gb=10)
    nivel_cbo = cbo.vetor_nivel(cbo.construir_dim_ocupacao())
    relatorios = [caged.processar_competencia(c, nivel_cbo) for c in novos]

    with carregar.conectar() as conn, conn.cursor() as cur:
        comps = pd.DataFrame({"competencia": novos})
        comps["ano"] = comps["competencia"] // 100
        comps["mes"] = comps["competencia"] % 100
        comps["trimestre"] = (comps["mes"] - 1) // 3 + 1
        comps["data"] = pd.to_datetime(comps["competencia"].astype(str) + "01", format="%Y%m%d").dt.date
        carregar._copiar(cur, "dim_competencia", comps)
        for tabela, (chaves, medidas, sufixo) in TABELAS.items():
            partes = [pd.read_parquet(config.DIR_STAGING / "caged" / f"{c}_{sufixo}.parquet") for c in novos
                      if (config.DIR_STAGING / "caged" / f"{c}_{sufixo}.parquet").exists()]
            if partes:
                df = pd.concat(partes).groupby(chaves, as_index=False)[medidas].sum()
                _upsert(cur, tabela, df, chaves, medidas)
                log.info("%s: %d linhas somadas", tabela, len(df))
        for r in relatorios:
            cur.execute("INSERT INTO etl_execucao (fonte, referencia, relatorio) VALUES (%s, %s, %s)",
                        (r["fonte"], str(r["competencia_publicacao"]), json.dumps(r, ensure_ascii=False)))
        conn.commit()
    log.info("carga incremental concluída")
