"""Orquestrador do pipeline.

Uso (a partir da raiz do repositório):
  python -m etl baixar   [--anos 2023 2024 2025] [--inicio 202501] [--fim 202609]
  python -m etl rais     [--anos ...]      processa a RAIS (espera os downloads)
  python -m etl caged    [--inicio ...] [--fim ...]
  python -m etl modelar                    consolida o staging em data/final
  python -m etl carregar                   carrega no PostgreSQL e no OpenSearch (opcional)
  python -m etl tudo                       todas as etapas em sequência
  python -m etl incremental                busca meses novos do CAGED e soma ao banco
  python -m etl permissoes                 concede leitura ao usuário do painel
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading

from . import apis, baixar, caged, carregar, config, incremental, modelar, rais


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m etl", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("etapa", choices=["baixar", "rais", "caged", "modelar", "carregar", "tudo", "incremental",
                                     "permissoes"])
    p.add_argument("--anos", type=int, nargs="+", default=config.ANOS_RAIS)
    p.add_argument("--inicio", type=int, default=config.CAGED_INICIO)
    p.add_argument("--fim", type=int, default=config.CAGED_FIM)
    p.add_argument("--paralelo", type=int, default=4, help="downloads simultâneos")
    p.add_argument("--limite-gb", type=float, default=6.0, help="espaço máximo para arquivos brutos")
    p.add_argument("--manter-brutos", action="store_true", help="não apaga os .7z após processar")
    a = p.parse_args(argv)

    config.DIR_DADOS.joinpath("logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(config.DIR_DADOS / "logs" / "pipeline.log", encoding="utf-8")])
    config.CAGED_INICIO, config.CAGED_FIM = a.inicio, a.fim
    apagar = not a.manter_brutos

    if a.etapa in ("baixar", "tudo"):
        tarefas = baixar.tarefas_caged(a.inicio, a.fim) + baixar.tarefas_rais(a.anos)
        if a.etapa == "baixar":
            baixar.baixar_todos(tarefas, a.paralelo, a.limite_gb)
        else:
            # Download em segundo plano enquanto o processamento consome os arquivos.
            threading.Thread(target=baixar.baixar_todos, args=(tarefas, a.paralelo, a.limite_gb),
                             daemon=True).start()
    if a.etapa in ("caged", "rais", "tudo", "incremental"):
        # Limites do salário-hora calculados com o salário mínimo oficial (API do Banco Central).
        apis.atualizar_salario_minimo()
    if a.etapa in ("caged", "tudo"):
        caged.processar_todos(a.inicio, a.fim, apagar_brutos=apagar)
    if a.etapa in ("rais", "tudo"):
        rais.processar_todos(a.anos, apagar_brutos=apagar)
    if a.etapa in ("modelar", "tudo"):
        modelar.executar()
    if a.etapa in ("carregar", "tudo"):
        carregar.carregar_postgres()
        carregar.carregar_opensearch()
    if a.etapa == "incremental":
        incremental.executar()
    if a.etapa == "permissoes":
        carregar.permissoes()


if __name__ == "__main__":
    main()
