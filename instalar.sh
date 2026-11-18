#!/usr/bin/env bash
# =====================================================================================
#  Instalador automático — Gestão de Suporte e Cobrança
#  Para um servidor Ubuntu 22.04/24.04 novo (ex.: Oracle Cloud "Always Free").
#
#  Uso (dentro da pasta do projeto):   sudo bash instalar.sh
#
#  O que faz:
#   1. instala Docker e cria memória swap se o servidor tiver pouca RAM;
#   2. libera as portas 80/443 no firewall do Ubuntu;
#   3. gera o .env com senhas aleatórias e endereço HTTPS gratuito (<seu-ip>.sslip.io);
#   4. pergunta (opcional) a conexão do Supabase; sem ela usa SQLite local;
#   5. sobe o sistema, o agendador, o gateway do WhatsApp e o HTTPS;
#   6. cria seu usuário de acesso.
#  Pode ser executado de novo sem perder dados (o .env existente é mantido).
# =====================================================================================
set -euo pipefail

VERDE='\033[0;32m'; AMARELO='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${VERDE}✔ $*${NC}"; }
info() { echo -e "${AMARELO}➜ $*${NC}"; }

if [[ $EUID -ne 0 ]]; then echo "Execute com sudo: sudo bash instalar.sh"; exit 1; fi
cd "$(dirname "$0")"
[[ -f docker-compose.yml && -f manage.py ]] || { echo "Execute dentro da pasta do projeto."; exit 1; }

# ---------------------------------------------------------------- 1. Docker
if ! command -v docker >/dev/null 2>&1; then
  info "Instalando Docker..."
  apt-get update -qq
  apt-get install -y -qq docker.io docker-compose-v2 >/dev/null 2>&1 || {
    apt-get install -y -qq curl >/dev/null
    curl -fsSL https://get.docker.com | sh
  }
  systemctl enable --now docker
fi
ok "Docker pronto"

# swap para servidores com pouca memória
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [[ $MEM_MB -lt 3000 && ! -f /swapfile ]]; then
  info "Servidor com ${MEM_MB} MB de RAM: criando 2 GB de swap..."
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ok "Swap criado"
fi

# ---------------------------------------------------------------- 2. Firewall
# As imagens Ubuntu da Oracle bloqueiam tudo além da porta 22 via iptables.
if command -v iptables >/dev/null 2>&1; then
  for porta in 80 443; do
    iptables -C INPUT -p tcp --dport $porta -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport $porta -j ACCEPT
  done
  if command -v netfilter-persistent >/dev/null 2>&1; then netfilter-persistent save >/dev/null 2>&1 || true; fi
  ok "Portas 80 e 443 liberadas no Ubuntu"
fi

# ---------------------------------------------------------------- 3. .env
aleatorio() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "$1"; }

if [[ ! -f .env ]]; then
  IP=$(curl -fsS4 --max-time 10 https://api.ipify.org || curl -fsS4 --max-time 10 https://ifconfig.me || true)
  [[ -n "$IP" ]] || { read -rp "Não consegui descobrir o IP público. Digite o IP do servidor: " IP; }
  DOMINIO_PADRAO="${IP//./-}.sslip.io"
  echo
  echo "Endereço do sistema. Se não tiver domínio próprio, aperte ENTER para usar o gratuito:"
  read -rp "  Domínio [${DOMINIO_PADRAO}]: " DOMINIO
  DOMINIO=${DOMINIO:-$DOMINIO_PADRAO}

  echo
  echo "Banco de dados. Para usar o Supabase gratuito, cole a connection string do"
  echo "'Session pooler' (Supabase → Connect → Session pooler), já com a senha."
  echo "Para usar o banco local (SQLite), apenas aperte ENTER."
  read -rp "  DATABASE_URL: " DATABASE_URL

  cat > .env <<EOF
DEBUG=false
SECRET_KEY=$(aleatorio 60)
DOMINIO=${DOMINIO}
ALLOWED_HOSTS=${DOMINIO},web,localhost
CSRF_TRUSTED_ORIGINS=https://${DOMINIO}
SITE_URL=https://${DOMINIO}
TIME_ZONE=America/Sao_Paulo
DATABASE_URL=${DATABASE_URL}
BACKUP_RETENCAO_DIAS=30
WHATSAPP_PROVEDOR=qrcode
GATEWAY_TOKEN=$(aleatorio 48)
INTERVALO_MIN_SEG=20
INTERVALO_MAX_SEG=45
EOF
  chmod 600 .env
  ok ".env criado (senhas aleatórias geradas)"
else
  ok ".env existente mantido"
fi
DOMINIO=$(grep '^DOMINIO=' .env | cut -d= -f2)

# ---------------------------------------------------------------- 4. Subir
mkdir -p dados/whatsapp-sessao dados/caddy
info "Construindo e iniciando os serviços (a primeira vez leva alguns minutos)..."
docker compose up -d --build
ok "Serviços iniciados"

info "Aguardando o sistema responder..."
for _ in $(seq 1 60); do
  if docker compose exec -T web python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/entrar/')" >/dev/null 2>&1; then break; fi
  sleep 3
done

# ---------------------------------------------------------------- 5. Usuário
if [[ $(docker compose exec -T web python manage.py shell -c "from django.contrib.auth.models import User;print(User.objects.count())" 2>/dev/null | tail -1) == "0" ]]; then
  echo
  info "Crie o seu usuário de acesso:"
  docker compose exec web python manage.py createsuperuser
fi

echo
ok "Instalação concluída!"
echo
echo "  Acesse:  https://${DOMINIO}"
echo "  Depois:  Configurações → escaneie o QR Code com o WhatsApp do chip de cobranças."
echo
echo "  Para criar o usuário da sua funcionária:"
echo "     sudo docker compose exec web python manage.py createsuperuser"
echo "  Para atualizar o sistema depois:  git pull && sudo docker compose up -d --build"
echo
echo "  ⚠ Na Oracle Cloud, libere também as portas 80 e 443 na 'Security List' da rede (veja o README)."
