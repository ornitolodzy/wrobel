# PRAWDOWID na AWS

Trzy maszyny EC2 w `eu-north-1`, publiczny HTTP na Elastic IP:

| Host | Typ | Serwisy | Wejście |
|---|---|---|---|
| edge | t3.small | nginx :80, frontend, api-gateway, discord-bot | 80 z internetu |
| app | t3.large | verification-pipeline, page-scrapper, ocr, context-graph, tunel SSH do GPU | 8001-8003 tylko z edge |
| data | t3.medium | Postgres 16 + pgvector, Neo4j | 5432 z edge/app, 7687 z app |

SSH jest zamknięte, dostęp jest przez SSM. Obrazy budujesz na laptopie i trafiają do ECR. Konfiguracja (compose, `nginx.conf`, seedy, `.env`) idzie do prywatnego bucketu S3.

Koszt to około 130 $/mies. przy maszynach włączonych 24/7. Po `make aws-stop` zostaje około 15 $/mies. za dyski i dwa adresy EIP.

## Pierwsze wdrożenie

Potrzebujesz `terraform`, `aws` (z poświadczeniami) oraz `docker` + `docker-compose`.

```bash
cp deploy/aws/terraform/terraform.tfvars.example deploy/aws/terraform/terraform.tfvars   # serwer GPU
make aws-init
make aws-up        # ~2 min, na końcu wypisuje public_url i app_egress_ip
make aws-deploy    # build + push (pierwszy raz kilkanaście minut), potem start data -> app -> edge
```

Plik `.env` z katalogu głównego (klucze API, `PRAWDOWID_*`, `DISCORD_TOKEN`) jedzie bez zmian. Hasła do Postgresa i Neo4j oraz `ADMIN_API_KEY` generuje Terraform. Klucz admina odczytasz tak: `terraform -chdir=deploy/aws/terraform output -raw admin_api_key`.

## Na co dzień

```bash
make aws-deploy                     # nowa wersja kodu
make aws-config                     # zmiana .env / nginx.conf, bez przebudowy obrazów
HOSTS=edge make aws-deploy          # tylko wybrane hosty
make aws-ps H=app
make aws-logs H=app S=verification-pipeline
./deploy/aws/remote.sh app 'systemctl status wrobel-gpu-tunnel --no-pager'
make aws-stop / make aws-start      # po starcie kontenery i tunel wstają same
make aws-down                       # usuwa wszystko, z bazą włącznie
```

## Gdy coś nie działa

- **Tunel GPU** zestawia SSH z hosta app do serwera GPU (`-L 172.17.0.1:11435` i `:8081`). Kontenery widzą go jako `host.docker.internal`, czyli tak samo jak lokalnie. Klucz musi być bez hasła. Jeśli serwer ma whitelistę IP, dopisz do niej `app_egress_ip`.
- **Logi bootstrapu** są w `/var/log/wrobel-bootstrap.log`: `./deploy/aws/remote.sh app 'tail -50 /var/log/wrobel-bootstrap.log'`.
- **Baza i graf** żyją w wolumenach Dockera na dysku hosta data. Seedy wczytują się tylko na pustym wolumenie. `terraform apply` nie podmienia maszyn przy nowym AMI (`ignore_changes`), więc baza nie zniknie przypadkiem.
