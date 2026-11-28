variable "aiven_api_token" {
  description = "Token pessoal da Aiven (Console > User information > Tokens). Defina via TF_VAR_aiven_api_token."
  type        = string
  sensitive   = true
}

variable "aiven_project" {
  description = "Nome do projeto na Aiven (criado junto com a conta)."
  type        = string
}

variable "pg_service_name" {
  description = "Nome do serviço PostgreSQL."
  type        = string
  default     = "pg-gap-salarial"
}

variable "pg_plan" {
  description = "Plano do PostgreSQL. Confira o nome do plano gratuito no Console ou com: avn service plans --service-type pg"
  type        = string
  default     = "free-1-1gb"
}

variable "cloud_name" {
  description = "Nuvem/região. No plano gratuito a Aiven escolhe a nuvem; null deixa a escolha com ela."
  type        = string
  default     = null
}

variable "pg_database" {
  description = "Banco de dados analítico criado dentro do serviço."
  type        = string
  default     = "gap_salarial"
}

variable "pg_usuario_dashboard" {
  description = "Usuário somente leitura usado pelo painel Streamlit."
  type        = string
  default     = "leitor_dashboard"
}

variable "ip_permitidos" {
  description = "Faixas de IP autorizadas. O Streamlit Cloud e o GitHub Actions não têm IP fixo, por isso o padrão é aberto, com SSL obrigatório."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "opensearch_service_name" {
  description = "Nome do serviço OpenSearch criado no Console (opcional). Vazio desativa."
  type        = string
  default     = ""
}
