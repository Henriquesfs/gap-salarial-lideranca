terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aiven = {
      source  = "aiven/aiven"
      version = "~> 4.0"
    }
  }
}

# O token nunca fica no código: vem de TF_VAR_aiven_api_token (variável de ambiente)
# ou de um terraform.tfvars local, que está no .gitignore.
provider "aiven" {
  api_token = var.aiven_api_token
}

data "aiven_project" "projeto" {
  project = var.aiven_project
}

# ---------------------------------------------------------------- banco relacional
# PostgreSQL no plano gratuito (1 CPU, 1 GB RAM, 1 GB de disco).
# Se a Aiven recusar criar o plano gratuito pela API, crie o serviço no Console com o
# mesmo nome e traga para o Terraform com:
#   terraform import aiven_pg.analytics <projeto>/<pg_service_name>
resource "aiven_pg" "analytics" {
  project      = data.aiven_project.projeto.project
  service_name = var.pg_service_name
  plan         = var.pg_plan
  cloud_name   = var.cloud_name

  pg_user_config {
    # Sem IP fixo no plano gratuito. A conexão exige SSL (sslmode=require) e senha forte.
    ip_filter_string = var.ip_permitidos

    pg {
      # Encerra transações esquecidas abertas (valor em milissegundos: 60000 = 1 minuto) e protege
      # o limite de 20 conexões do plano gratuito.
      idle_in_transaction_session_timeout = 60000
    }
  }

  lifecycle {
    # No plano gratuito a Aiven escolhe a nuvem; uma mudança feita por ela não deve forçar
    # alteração do serviço. O plano continua controlado aqui (var.pg_plan).
    ignore_changes = [cloud_name]
  }
}

resource "aiven_pg_database" "gap" {
  project       = aiven_pg.analytics.project
  service_name  = aiven_pg.analytics.service_name
  database_name = var.pg_database
}

# Usuário só de leitura para o painel. O ETL usa o usuário administrador (avnadmin);
# o dashboard publicado na internet usa este, com GRANT SELECT aplicado pelo ETL.
resource "aiven_pg_user" "dashboard" {
  project      = aiven_pg.analytics.project
  service_name = aiven_pg.analytics.service_name
  username     = var.pg_usuario_dashboard
}

# ---------------------------------------------------------------- banco não relacional (opcional)
# O OpenSearch gratuito só pode ser criado pelo Console da Aiven. O Terraform apenas lê o
# serviço existente para expor a URI de conexão ao ETL.
data "aiven_opensearch" "busca" {
  count        = var.opensearch_service_name == "" ? 0 : 1
  project      = data.aiven_project.projeto.project
  service_name = var.opensearch_service_name
}
