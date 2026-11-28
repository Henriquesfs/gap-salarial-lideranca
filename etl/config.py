"""Parâmetros do pipeline. Tudo que muda o resultado das métricas fica aqui."""
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_DADOS = RAIZ / "data"
DIR_BRUTOS = DIR_DADOS / "raw"          # arquivos .7z baixados (apagados após o processamento)
DIR_STAGING = DIR_DADOS / "staging"     # agregados parciais por arquivo
DIR_FINAL = DIR_DADOS / "final"         # tabelas prontas para carga
DIR_QUALIDADE = DIR_DADOS / "qualidade" # relatórios JSON de qualidade

FTP_HOST = "ftp.mtps.gov.br"
FTP_RAIS = "/pdet/microdados/RAIS/{ano}"
FTP_CAGED = "/pdet/microdados/NOVO CAGED/{ano}/{competencia}"
FTP_LAYOUT_CAGED = "/pdet/microdados/NOVO CAGED/Layout Não-identificado Novo Caged Movimentação.xlsx"

ANOS_RAIS = [2023, 2024, 2025]
# O arquivo RAIS_VINC_PUB_NI (UF não identificada) fica de fora: sem UF não há como
# atribuir região, e ele representa menos de 0,01% dos vínculos.
ARQUIVOS_RAIS = [
    "RAIS_VINC_PUB_NORTE.7z",
    "RAIS_VINC_PUB_CENTRO_OESTE.7z",
    "RAIS_VINC_PUB_NORDESTE.7z",
    "RAIS_VINC_PUB_SUL.7z",
    "RAIS_VINC_PUB_MG_ES_RJ.7z",
    "RAIS_VINC_PUB_SP.7z",
]

CAGED_INICIO = 202501
CAGED_FIM = 202609

# Salário mínimo vigente em dezembro de cada ano (RAIS) e no ano da competência (CAGED).
# 2026: Decreto nº 12.797/2025.
SALARIO_MINIMO = {2023: 1320.00, 2024: 1412.00, 2025: 1518.00, 2026: 1621.00}

# Semanas por mês (52,14 / 12). Converte horas semanais contratadas em horas mensais.
SEMANAS_POR_MES = 4.345

# Faixa aceita para horas semanais contratadas.
HORAS_MIN, HORAS_MAX = 1, 60

# Salário-hora aceito, em múltiplos do salário mínimo-hora (SM / 220).
# Abaixo de 0,5 indica remuneração parcial ou erro de declaração.
# Acima de 300 (cerca de R$ 450 mil/mês em 2025) é tratado como outlier.
SAL_HORA_MIN_SM, SAL_HORA_MAX_SM = 0.5, 300.0

# Amostragem estratificada por nível hierárquico. Os níveis de liderança são raros,
# então recebem taxas maiores. Cada linha da amostra carrega peso = 1 / taxa.
TAXA_AMOSTRA = {1: 0.004, 2: 0.03, 3: 0.04, 4: 0.15}
SEMENTE = 2026

# Tamanho do bloco de leitura do CSV em streaming (bytes).
BLOCO_LEITURA = 64 * 1024 * 1024

# Grupos com menos registros que isso não exibem média no painel.
N_MINIMO_EXIBICAO = 30
