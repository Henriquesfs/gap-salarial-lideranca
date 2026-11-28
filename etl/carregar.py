"""Carga das tabelas finais no PostgreSQL (Aiven) e, opcionalmente, no OpenSearch.

Credenciais vêm de variáveis de ambiente (arquivo .env local ou Secrets do GitHub):
  DATABASE_URL    postgres://usuario:senha@host:porta/defaultdb?sslmode=require
  OPENSEARCH_URI  https://usuario:senha@host:porta   (opcional)
"""
from __future__ import annotations

import io
import json
import logging
import os
import time

import pandas as pd
import psycopg
import psycopg.sql
import requests
from dotenv import load_dotenv

from . import config

log = logging.getLogger(__name__)

ORDEM = [
    "dim_uf", "dim_sexo", "dim_raca", "dim_grupo", "dim_faixa_etaria", "dim_escolaridade", "dim_nivel",
    "dim_secao_cnae", "dim_setor", "dim_motivo", "dim_competencia", "dim_ocupacao",
    "fato_rais_perfil", "fato_rais_setor", "fato_rais_ocupacao", "amostra_rais",
    "fato_caged_perfil", "fato_caged_motivo", "fato_caged_setor", "ref_serie_mensal",
]
LIMITE_BYTES = 1_000_000_000  # plano gratuito da Aiven


def conectar() -> psycopg.Connection:
    load_dotenv(config.RAIZ / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("defina DATABASE_URL no .env ou nas variáveis de ambiente")
    return psycopg.connect(url, connect_timeout=30)


def _copiar(cur: psycopg.Cursor, tabela: str, df: pd.DataFrame, lote: int = 200_000) -> None:
    colunas = ", ".join(df.columns)
    with cur.copy(f"COPY {tabela} ({colunas}) FROM STDIN WITH (FORMAT csv)") as cp:
        for i in range(0, len(df), lote):
            buf = io.StringIO()
            df.iloc[i:i + lote].to_csv(buf, index=False, header=False)
            cp.write(buf.getvalue())


def conceder_leitura(cur: psycopg.Cursor) -> bool:
    """Menor privilégio: o painel publicado só lê. O usuário é criado pelo Terraform
    (aiven_pg_user); enquanto ele não existir, a etapa é pulada com aviso."""
    leitor = os.environ.get("DASHBOARD_DB_USER")
    if not leitor:
        return False
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (leitor,))
    if cur.fetchone() is None:
        log.warning("usuário %s não existe; rode o Terraform e depois `python -m etl permissoes`", leitor)
        return False
    ident = psycopg.sql.Identifier(leitor)
    cur.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
    cur.execute(psycopg.sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(ident))
    log.info("permissão de leitura concedida a %s", leitor)
    return True


def permissoes() -> None:
    with conectar() as conn, conn.cursor() as cur:
        conceder_leitura(cur)


def carregar_postgres() -> dict:
    sql_dir = config.RAIZ / "etl" / "sql"
    with conectar() as conn, conn.cursor() as cur:
        log.info("recriando esquema")
        cur.execute((sql_dir / "schema.sql").read_text(encoding="utf-8"))
        for tabela in ORDEM:
            t0 = time.time()
            df = pd.read_parquet(config.DIR_FINAL / f"{tabela}.parquet")
            _copiar(cur, tabela, df)
            log.info("%-22s %10s linhas em %.0f s", tabela, f"{len(df):,}", time.time() - t0)
        cur.execute("CREATE INDEX ix_amostra_filtros ON amostra_rais (ano, uf, nivel)")
        cur.execute("CREATE INDEX ix_caged_ano ON fato_caged_perfil (competencia, nivel)")
        cur.execute((sql_dir / "views.sql").read_text(encoding="utf-8"))

        conceder_leitura(cur)

        relatorios = json.loads((config.DIR_FINAL / "etl_qualidade.json").read_text(encoding="utf-8"))
        cur.execute("TRUNCATE etl_execucao")
        for r in relatorios:
            ref = r.get("arquivo") or str(r.get("competencia_publicacao"))
            cur.execute("INSERT INTO etl_execucao (fonte, referencia, relatorio) VALUES (%s, %s, %s)",
                        (r["fonte"], f"{r.get('ano', '')} {ref}".strip(), json.dumps(r, ensure_ascii=False)))

        # Respostas brutas das APIs (IBGE e Banco Central), guardadas como documentos JSON.
        coletas_arq = config.DIR_FINAL / "api_coleta.json"
        if coletas_arq.exists():
            for c in json.loads(coletas_arq.read_text(encoding="utf-8")):
                cur.execute("INSERT INTO api_coleta (url, coletado, status, resposta) VALUES (%s, %s, %s, %s)",
                            (c["url"], c["coletado"], c["status"], json.dumps(c["resposta"], ensure_ascii=False)))
        conn.commit()

        conn.autocommit = True
        cur.execute("ANALYZE")
        cur.execute("""
            SELECT relname, pg_total_relation_size(relid)
            FROM pg_catalog.pg_statio_user_tables ORDER BY 2 DESC""")
        tamanhos = {nome: int(b) for nome, b in cur.fetchall()}
        cur.execute("SELECT pg_database_size(current_database())")
        total = int(cur.fetchone()[0])
    for nome, b in tamanhos.items():
        log.info("  %-22s %8.1f MB", nome, b / 1e6)
    log.info("tamanho total do banco: %.1f MB (limite %.0f MB)", total / 1e6, LIMITE_BYTES / 1e6)
    if total > 0.9 * LIMITE_BYTES:
        log.warning("banco acima de 90%% do limite do plano gratuito")
    return {"tabelas": tamanhos, "total_bytes": total}


def carregar_opensearch() -> None:
    """Indexa o catálogo de ocupações (texto) e os relatórios de qualidade (JSON)."""
    load_dotenv(config.RAIZ / ".env")
    uri = os.environ.get("OPENSEARCH_URI")
    if not uri:
        log.info("OPENSEARCH_URI não definido; etapa não relacional ignorada")
        return
    uri = uri.rstrip("/")
    ocup = pd.read_parquet(config.DIR_FINAL / "dim_ocupacao.parquet")
    niveis = pd.read_parquet(config.DIR_FINAL / "dim_nivel.parquet").set_index("nivel")["nome"]
    requests.delete(f"{uri}/cbo_ocupacoes", timeout=60)
    requests.put(f"{uri}/cbo_ocupacoes", timeout=60, json={
        "settings": {"analysis": {"analyzer": {"pt": {"type": "standard", "stopwords": "_brazilian_"}}}},
        "mappings": {"properties": {
            "cbo": {"type": "integer"}, "titulo": {"type": "text", "analyzer": "pt"},
            "nivel": {"type": "keyword"}, "regra_nivel": {"type": "keyword"},
        }},
    }).raise_for_status()
    linhas = []
    for r in ocup.itertuples():
        linhas.append(json.dumps({"index": {"_index": "cbo_ocupacoes", "_id": int(r.cbo)}}))
        linhas.append(json.dumps({"cbo": int(r.cbo), "titulo": r.titulo, "nivel": niveis[r.nivel],
                                  "regra_nivel": r.regra_nivel}, ensure_ascii=False))
    relatorios = json.loads((config.DIR_FINAL / "etl_qualidade.json").read_text(encoding="utf-8"))
    for i, rel in enumerate(relatorios):
        # Nomes de coluna originais têm pontos ("CNAE 2.0"), que o OpenSearch leria como
        # caminho de objeto. Viram uma lista de pares.
        if "colunas_lidas" in rel:
            rel = {**rel, "colunas_lidas": [{"original": k, "padronizado": v} for k, v in rel["colunas_lidas"].items()]}
        linhas.append(json.dumps({"index": {"_index": "etl_qualidade", "_id": i}}))
        linhas.append(json.dumps(rel, ensure_ascii=False))
    resp = requests.post(f"{uri}/_bulk", data=("\n".join(linhas) + "\n").encode("utf-8"),
                         headers={"Content-Type": "application/x-ndjson"}, timeout=300)
    resp.raise_for_status()
    log.info("OpenSearch: %d documentos indexados (erros: %s)", len(linhas) // 2, resp.json().get("errors"))
