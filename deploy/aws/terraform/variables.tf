variable "project" {
  description = "Prefiks nazw zasobów."
  type        = string
  default     = "wrobel"
}

variable "region" {
  description = "Region AWS (UE: dane zostają w EOG)."
  type        = string
  default     = "eu-north-1"
}

# --- Rozmiary maszyn (x86_64: PaddleOCR i torch CPU bez niespodzianek) ---

variable "edge_instance_type" {
  description = "EDGE: nginx, frontend SSR, api-gateway, bot Discord."
  type        = string
  default     = "t3.small"
}

variable "app_instance_type" {
  description = "APP: verification-pipeline (reranker CPU jako fallback), page-scrapper (chromium), OCR, context-graph."
  type        = string
  default     = "t3.large"
}

variable "data_instance_type" {
  description = "DATA: Postgres 16 + pgvector, Neo4j."
  type        = string
  default     = "t3.medium"
}

variable "edge_disk_gb" {
  type    = number
  default = 20
}

variable "app_disk_gb" {
  description = "Obrazy pipeline (torch) + OCR + chromium to kilka GB."
  type        = number
  default     = 40
}

variable "data_disk_gb" {
  type    = number
  default = 30
}

# --- Tunel SSH do istniejącego serwera GPU (Bielik, bge-m3, rerank-server) ---

variable "gpu_ssh_target" {
  description = "user@host serwera GPU. Puste = bez tunelu (pipeline użyje tego, co jest w .env, np. API Claude)."
  type        = string
  default     = ""
}

variable "gpu_ssh_port" {
  type    = number
  default = 22
}

variable "gpu_ssh_private_key_path" {
  description = "Lokalna ścieżka do klucza prywatnego (bez hasła) do serwera GPU. Trafia do prywatnego bucketu S3."
  type        = string
  default     = ""
}

variable "tailscale_auth_key" {
  description = "Auth key Tailscale (tskey-auth-...), gdy serwer GPU jest w tailnecie (adres 100.x). Host app dołącza do tailnetu. Puste = bez Tailscale."
  type        = string
  default     = ""
  sensitive   = true
}

variable "gpu_tunnel_forwards" {
  description = "Argumenty -L. 172.17.0.1 = docker0, kontenery widzą to jako host.docker.internal (jak lokalnie)."
  type        = list(string)
  default = [
    "172.17.0.1:11435:127.0.0.1:11434", # Ollama: Bielik + bge-m3
    "172.17.0.1:8081:127.0.0.1:8081",   # rerank-server (GPU)
  ]
}
