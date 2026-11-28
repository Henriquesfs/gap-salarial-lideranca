-- Esquema do banco analítico (PostgreSQL / Aiven).
-- Modelo em constelação: várias tabelas fato, em grãos diferentes, compartilhando as
-- mesmas dimensões. Todos os fatos já chegam agregados para caber no limite de 1 GB.

DROP VIEW IF EXISTS vw_rotatividade, vw_gap_interseccional, vw_funil_lideranca, vw_rais_grupo CASCADE;
DROP TABLE IF EXISTS
    fato_rais_perfil, fato_rais_setor, fato_rais_ocupacao, amostra_rais,
    fato_caged_perfil, fato_caged_motivo, fato_caged_setor, ref_serie_mensal, api_coleta,
    dim_ocupacao, dim_grupo, dim_uf, dim_sexo, dim_raca, dim_faixa_etaria, dim_escolaridade,
    dim_nivel, dim_secao_cnae, dim_setor, dim_motivo, dim_competencia
    CASCADE;

-- ------------------------------------------------------------------ dimensões
CREATE TABLE dim_uf (
    uf           smallint PRIMARY KEY,
    sigla        char(2)  NOT NULL UNIQUE,
    nome         text     NOT NULL,
    regiao       smallint NOT NULL,
    regiao_nome  text     NOT NULL
);
CREATE TABLE dim_sexo         (sexo smallint PRIMARY KEY, nome text NOT NULL);
CREATE TABLE dim_raca         (raca smallint PRIMARY KEY, nome text NOT NULL, grupo text NOT NULL);
-- Cruzamento sexo x raça/cor com o rótulo usado nos indicadores ("Mulher negra" etc.).
CREATE TABLE dim_grupo (
    sexo                 smallint NOT NULL REFERENCES dim_sexo,
    raca                 smallint NOT NULL REFERENCES dim_raca,
    grupo_racial         text     NOT NULL,
    grupo_interseccional text     NOT NULL,
    PRIMARY KEY (sexo, raca)
);
CREATE TABLE dim_faixa_etaria (faixa_etaria smallint PRIMARY KEY, nome text NOT NULL);
CREATE TABLE dim_escolaridade (escolaridade smallint PRIMARY KEY, nome text NOT NULL);
CREATE TABLE dim_nivel        (nivel smallint PRIMARY KEY, nome text NOT NULL, lideranca boolean NOT NULL);
CREATE TABLE dim_secao_cnae   (cnae_secao char(1) PRIMARY KEY, nome text NOT NULL);
CREATE TABLE dim_setor        (setor smallint PRIMARY KEY, nome text NOT NULL);
CREATE TABLE dim_motivo       (motivo smallint PRIMARY KEY, nome text NOT NULL);
CREATE TABLE dim_competencia (
    competencia integer  PRIMARY KEY,   -- AAAAMM
    ano         smallint NOT NULL,
    mes         smallint NOT NULL CHECK (mes BETWEEN 1 AND 12),
    trimestre   smallint NOT NULL,
    data        date     NOT NULL
);
CREATE TABLE dim_ocupacao (
    cbo          integer  PRIMARY KEY,
    titulo       text     NOT NULL,
    familia      smallint NOT NULL,
    grande_grupo smallint NOT NULL,
    nivel        smallint NOT NULL REFERENCES dim_nivel,
    regra_nivel  text     NOT NULL,
    desativada   boolean  NOT NULL
);

-- ------------------------------------------------------------------ fatos RAIS (estoque em 31/12)
-- Medidas aditivas: médias e desvios são calculados na consulta a partir das somas.
--   salário-hora médio = soma_salario_hora / n_salario
--   desvio-padrão      = sqrt(soma_salario_hora2 / n_salario - média²)
CREATE TABLE fato_rais_perfil (
    ano                 smallint NOT NULL,
    uf                  smallint NOT NULL REFERENCES dim_uf,
    sexo                smallint NOT NULL REFERENCES dim_sexo,
    raca                smallint NOT NULL REFERENCES dim_raca,
    faixa_etaria        smallint NOT NULL REFERENCES dim_faixa_etaria,
    escolaridade        smallint NOT NULL REFERENCES dim_escolaridade,
    nivel               smallint NOT NULL REFERENCES dim_nivel,
    setor               smallint NOT NULL REFERENCES dim_setor,
    n_vinculos          integer  NOT NULL CHECK (n_vinculos >= 0),
    n_salario           integer  NOT NULL CHECK (n_salario >= 0),
    soma_remuneracao    double precision NOT NULL,
    soma_salario_hora   double precision NOT NULL,
    soma_salario_hora2  double precision NOT NULL,
    soma_horas          double precision NOT NULL,
    PRIMARY KEY (ano, uf, sexo, raca, faixa_etaria, escolaridade, nivel, setor)
);

CREATE TABLE fato_rais_setor (
    ano                 smallint NOT NULL,
    uf                  smallint NOT NULL REFERENCES dim_uf,
    sexo                smallint NOT NULL REFERENCES dim_sexo,
    raca                smallint NOT NULL REFERENCES dim_raca,
    nivel               smallint NOT NULL REFERENCES dim_nivel,
    cnae_secao          char(1)  NOT NULL REFERENCES dim_secao_cnae,
    setor               smallint NOT NULL REFERENCES dim_setor,
    n_vinculos          integer  NOT NULL,
    n_salario           integer  NOT NULL,
    soma_remuneracao    double precision NOT NULL,
    soma_salario_hora   double precision NOT NULL,
    soma_salario_hora2  double precision NOT NULL,
    soma_horas          double precision NOT NULL,
    PRIMARY KEY (ano, uf, sexo, raca, nivel, cnae_secao, setor)
);

CREATE TABLE fato_rais_ocupacao (
    ano                 smallint NOT NULL,
    regiao              smallint NOT NULL,
    sexo                smallint NOT NULL REFERENCES dim_sexo,
    raca                smallint NOT NULL REFERENCES dim_raca,
    setor               smallint NOT NULL REFERENCES dim_setor,
    cbo                 integer  NOT NULL REFERENCES dim_ocupacao,
    n_vinculos          integer  NOT NULL,
    n_salario           integer  NOT NULL,
    soma_remuneracao    double precision NOT NULL,
    soma_salario_hora   double precision NOT NULL,
    soma_salario_hora2  double precision NOT NULL,
    soma_horas          double precision NOT NULL,
    PRIMARY KEY (ano, regiao, sexo, raca, setor, cbo)
);

-- Amostra estratificada de vínculos (sem identificação). Usada para medianas e
-- distribuições, que não podem ser obtidas de somas. peso = 1 / taxa de amostragem.
CREATE TABLE amostra_rais (
    ano           smallint NOT NULL,
    uf            smallint NOT NULL,
    sexo          smallint NOT NULL,
    raca          smallint NOT NULL,
    faixa_etaria  smallint NOT NULL,
    escolaridade  smallint NOT NULL,
    nivel         smallint NOT NULL,
    cnae_secao    char(1)  NOT NULL,
    setor         smallint NOT NULL,
    cbo           integer  NOT NULL,
    horas         real     NOT NULL,
    remuneracao   real     NOT NULL,
    salario_hora  real     NOT NULL,
    peso          real     NOT NULL
);

-- ------------------------------------------------------------------ fatos Novo CAGED (fluxo mensal)
-- tipo: 1 = admissão, -1 = desligamento. n_mov já desconta as exclusões (EXC).
CREATE TABLE fato_caged_perfil (
    competencia        integer  NOT NULL REFERENCES dim_competencia,
    uf                 smallint NOT NULL REFERENCES dim_uf,
    sexo               smallint NOT NULL REFERENCES dim_sexo,
    raca               smallint NOT NULL REFERENCES dim_raca,
    faixa_etaria       smallint NOT NULL REFERENCES dim_faixa_etaria,
    nivel              smallint NOT NULL REFERENCES dim_nivel,
    tipo               smallint NOT NULL CHECK (tipo IN (1, -1)),
    n_mov              integer  NOT NULL,
    n_salario          integer  NOT NULL,
    soma_salario       double precision NOT NULL,
    soma_salario_hora  double precision NOT NULL,
    PRIMARY KEY (competencia, uf, sexo, raca, faixa_etaria, nivel, tipo)
);

CREATE TABLE fato_caged_motivo (
    competencia  integer  NOT NULL REFERENCES dim_competencia,
    regiao       smallint NOT NULL,
    sexo         smallint NOT NULL REFERENCES dim_sexo,
    raca         smallint NOT NULL REFERENCES dim_raca,
    nivel        smallint NOT NULL REFERENCES dim_nivel,
    motivo       smallint NOT NULL REFERENCES dim_motivo,
    n_mov        integer  NOT NULL,
    PRIMARY KEY (competencia, regiao, sexo, raca, nivel, motivo)
);

CREATE TABLE fato_caged_setor (
    competencia  integer  NOT NULL REFERENCES dim_competencia,
    uf           smallint NOT NULL REFERENCES dim_uf,
    sexo         smallint NOT NULL REFERENCES dim_sexo,
    raca         smallint NOT NULL REFERENCES dim_raca,
    nivel        smallint NOT NULL REFERENCES dim_nivel,
    cnae_secao   char(1)  NOT NULL REFERENCES dim_secao_cnae,
    tipo         smallint NOT NULL CHECK (tipo IN (1, -1)),
    n_mov        integer  NOT NULL,
    PRIMARY KEY (competencia, uf, sexo, raca, nivel, cnae_secao, tipo)
);

-- ------------------------------------------------------------------ séries de referência (API do Banco Central)
-- serie: 'salario_minimo' (SGS 1619, R$) ou 'ipca_mensal' (SGS 433, variação % no mês).
CREATE TABLE ref_serie_mensal (
    serie        text     NOT NULL,
    competencia  integer  NOT NULL,
    valor        double precision NOT NULL,
    PRIMARY KEY (serie, competencia)
);

-- ------------------------------------------------------------------ metadados (semiestruturado)
-- Respostas brutas das APIs públicas (IBGE e Banco Central), como vieram.
CREATE TABLE api_coleta (
    id        bigserial PRIMARY KEY,
    url       text NOT NULL,
    coletado  timestamptz NOT NULL,
    status    smallint NOT NULL,
    resposta  jsonb NOT NULL
);

-- Relatórios de qualidade de cada arquivo processado, guardados como documentos JSON.
CREATE TABLE IF NOT EXISTS etl_execucao (
    id          bigserial PRIMARY KEY,
    carregado   timestamptz NOT NULL DEFAULT now(),
    fonte       text NOT NULL,
    referencia  text NOT NULL,
    relatorio   jsonb NOT NULL
);
