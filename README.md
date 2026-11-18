# Gestão de Suporte e Cobrança

Sistema para automatizar o envio mensal de boletos pelo **WhatsApp**, controlar pagamentos e
acompanhar a **conta corrente com o sócio**.

O WhatsApp pode ser conectado de duas formas (troca em `WHATSAPP_PROVEDOR` no `.env`):

| | **QR Code** (padrão) | **API oficial da Meta** |
|---|---|---|
| Custo | Zero | Cobrança por mensagem |
| Como conecta | Escaneia o QR Code em *Configurações* | Cadastro na Meta + templates aprovados |
| Risco | **Não oficial: a Meta pode bloquear o número** | Sem risco de bloqueio |

## O que o sistema faz

| Área | Funcionalidades |
|---|---|
| **Boletos** | Importa PDFs (inclusive o **carnê do ano inteiro**), lê linha digitável, valor e vencimento, confere os dígitos verificadores, identifica o cliente pelo CPF/CNPJ e recorta páginas com vários boletos |
| **Régua automática** | Envia o boleto (PDF anexado) **2 dias antes** do vencimento e um lembrete **4 dias depois**, se não estiver pago. Respeita horário comercial, nunca duplica e **nunca cobra boleto pago ou cancelado** (rechecagem com bloqueio no banco imediatamente antes do envio) |
| **Pagamentos** | Comprovante do cliente (recebido automaticamente pelo WhatsApp) = "pagamento informado": pausa os lembretes, mas **não dá baixa**. A baixa só acontece por confirmação do sócio (link de conferência) ou registrada por você, sempre com origem e auditoria |
| **Repasse** | `Sua parte = (mensalidade − custo do sistema) × %`, configurável por cliente. Os valores ficam congelados em cada cobrança, então alterar o cadastro não muda o passado |
| **Conta com o sócio** | Crédito automático da sua parte a cada pagamento confirmado; registro dos PIX (qualquer valor); saldo sempre atualizado; distribuição FIFO mostrando quais mensalidades já foram repassadas; extrato para enviar ao sócio |
| **Portal de licenças** | Sem integração: o sistema cria as tarefas "liberar cliente X" (pagamento confirmado de cliente bloqueado) e "avaliar bloqueio" (atraso acima do limite) |
| **Painel e relatórios** | Indicadores do mês, vencidos, a vencer, saldo do sócio, tarefas, gráfico de 12 meses, relatório mensal (com CSV para Excel) e relatório por cliente |
| **Segurança** | Login com bloqueio após 5 tentativas, verificação em duas etapas (TOTP), arquivos acessíveis só com login, webhook com assinatura conferida, trilha de auditoria imutável, backup automático diário |

## Rodar no seu computador (teste)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
echo "DEBUG=true" > .env && echo "SECRET_KEY=dev" >> .env
python manage.py migrate
python manage.py createsuperuser          # cria seu usuário (repita para a funcionária)
python manage.py dados_demo               # opcional: dados fictícios para conhecer
python manage.py runserver
```

Acesse http://localhost:8000. Sem o gateway configurado, o WhatsApp fica em **modo simulação**: tudo é
registrado, mas nada é enviado. Para testar o QR Code localmente, em outro terminal:

```bash
cd gateway && npm install
GATEWAY_TOKEN=um-token-com-16-ou-mais WEBHOOK_URL=http://localhost:8000/webhook/gateway/ node index.mjs
```

e no `.env` do sistema: `GATEWAY_URL=http://localhost:3000` e o mesmo `GATEWAY_TOKEN`.

Para rodar a régua manualmente: `python manage.py processar_cobrancas`
Para rodar os testes: `python manage.py test core`

## Colocar no ar 100% grátis

O sistema precisa de um computador **ligado 24 horas** (para enviar nos horários certos e manter o
WhatsApp conectado). Custo zero com:

| Peça | Serviço gratuito | Observação |
|---|---|---|
| Servidor | **Oracle Cloud – Always Free** | Máquina virtual gratuita para sempre. Pede cartão só para verificar identidade; contas *Always Free* não são cobradas |
| Banco de dados | **Supabase – Free** (opcional) | 500 MB, sobra para anos de uso. Sem ele, o banco fica no próprio servidor (SQLite) |
| HTTPS / endereço | **sslip.io + Let's Encrypt** | Endereço `https://SEU-IP.sslip.io` com certificado automático |

### 1. Criar o servidor gratuito (Oracle Cloud)

1. Crie a conta em **cloud.oracle.com** → *Sign up* (escolha a região **Brazil East (São Paulo)**).
2. *Compute → Instances → Create instance*:
   - **Image:** Canonical Ubuntu 24.04
   - **Shape:** *Ampere* `VM.Standard.A1.Flex` com 1 OCPU e 6 GB (gratuito). Se não houver vaga, use
     `VM.Standard.E2.1.Micro` (também gratuito; o instalador cria memória swap).
   - Baixe a chave SSH oferecida.
3. Na instância criada: *Subnet → Security List → Add Ingress Rules*: origem `0.0.0.0/0`, TCP, portas
   **80** e **443**.
4. Conecte: `ssh -i chave.key ubuntu@IP-DO-SERVIDOR`.

### 2. (Opcional) Criar o banco no Supabase

1. Em **supabase.com**, *New project* (região São Paulo) e guarde a senha do banco.
2. Clique em **Connect → Session pooler** e copie a connection string, trocando `[YOUR-PASSWORD]` pela senha.
   (Use o *Session pooler*: a conexão direta do plano gratuito é só IPv6.)

Vantagem: os dados ficam fora do servidor, com backups do próprio Supabase. Projetos gratuitos sem
atividade por 7 dias são pausados; como o sistema consulta o banco a cada 30 minutos, ele se mantém ativo.

### 3. Instalar (automático)

No servidor:

```bash
sudo apt-get update && sudo apt-get install -y git gh
```
```bash
gh auth login
```
```bash
gh repo clone dinhoof32/gestao-cobranca && cd gestao-cobranca
```
```bash
sudo bash instalar.sh
```

O instalador instala o Docker, libera o firewall, gera senhas aleatórias, pergunta a connection string
do Supabase (ENTER para pular), sobe tudo com HTTPS e cria seu usuário. No final mostra o endereço de acesso.

**Atualizar depois:** `git pull && sudo docker compose up -d --build`

**Backups:** diários às 02h em `dados/backups/` (banco + PDFs + comprovantes). Com Supabase, o banco
também tem o backup do próprio Supabase. Para copiar os backups para o Google Drive gratuito, use `rclone`.
Restauração: `db.sql` com `psql`, ou `db.json` com `python manage.py loaddata db.json`.

> Alternativa sem nuvem: qualquer computador seu que fique ligado (Linux, ou Windows com Docker Desktop)
> roda o mesmo `docker compose up -d`.

## Conectar o WhatsApp pelo QR Code (sem custo)

1. No `.env`, deixe `WHATSAPP_PROVEDOR=qrcode` e defina um `GATEWAY_TOKEN` longo (`openssl rand -hex 32`).
2. Suba tudo com `docker compose up -d --build`. O serviço `gateway` mantém a conexão com o WhatsApp.
3. No sistema, abra **Configurações**. Vai aparecer um **QR Code**.
4. No celular: **WhatsApp → ⋮ → Dispositivos conectados → Conectar dispositivo** e aponte para o QR Code.
5. Pronto: o status muda para **Conectado**. A sessão fica salva em `dados/whatsapp-sessao/`, então não é
   preciso escanear de novo, nem depois de reiniciar o servidor.

O celular **não precisa ficar ligado**: o WhatsApp funciona como "dispositivo conectado". Se a sessão for
encerrada (por exemplo, removida pelo celular), o painel mostra o alerta e um novo QR Code aparece em
Configurações. Enquanto estiver desconectado, a régua **não envia e não perde nada**: retoma sozinha
quando o número for reconectado.

**Comprovantes:** quando o cliente envia uma foto ou PDF, o sistema anexa à cobrança em aberto, pausa o
lembrete e cria a tarefa "conferir com o sócio". Não dá baixa sozinho.

### ⚠ Risco de bloqueio e como o sistema reduz esse risco

A conexão por QR Code usa o WhatsApp Web, que **não é um canal oficial para automação**. A Meta pode
suspender números que considerar automatizados, e um bloqueio não tem como ser revertido pelo sistema. Proteções aplicadas:

- intervalo aleatório de **20 a 45 s** entre mensagens (`INTERVALO_MIN_SEG` / `INTERVALO_MAX_SEG`);
- simula "digitando…" antes de cada envio;
- só envia em horário comercial e em dias úteis (configurável);
- só envia para clientes cadastrados, no máximo 1 boleto + 1 lembrete por mês;
- confere se o número tem WhatsApp antes de enviar.

**Recomendação:** use um **chip dedicado às cobranças**, não o seu número pessoal. Peça aos clientes para
salvarem o contato: mensagens para quem tem o número salvo e responde têm risco bem menor.

Os textos das mensagens são editáveis em **Configurações** (variáveis `{nome}`, `{competencia}`,
`{valor}`, `{vencimento}`, `{linha}`, `{empresa}`).

## (Opcional) API oficial da Meta

Para trocar depois para o canal oficial, sem risco de bloqueio:

1. Crie uma conta em **business.facebook.com** com o CNPJ e verifique a empresa.
2. Em **developers.facebook.com**, crie um app do tipo *Business* e adicione o produto **WhatsApp**.
3. Gere um **token permanente** (usuário do sistema) com `whatsapp_business_messaging` e `whatsapp_business_management`.
4. No `.env`: `WHATSAPP_PROVEDOR=oficial`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`.
5. Webhook: `https://SEU-DOMINIO/webhook/whatsapp/`, campo **messages**.
6. Crie os templates `envio_boleto` e `lembrete_atraso` (categoria Utilidade, cabeçalho Documento, variáveis
   {{1}} nome, {{2}} competência, {{3}} valor, {{4}} vencimento, {{5}} linha digitável).

## Rotina mensal com o sistema

1. **1x por ano por cliente:** importe o carnê que o sócio envia → os 12 envios ficam agendados.
2. **Automático:** D-2 boleto, D+4 lembrete; comprovantes chegam e ficam anexados.
3. **Sócio:** abre o link de conferência no celular e toca em "Compensou" (ou você marca quando ele avisar).
4. **Você:** registra o PIX recebido do sócio → o saldo se atualiza; cumpre as tarefas de liberar/bloquear no portal.

## Estrutura

```
gestao/            configurações do Django
core/models.py     banco de dados (clientes, cobranças, mensagens, conta do sócio, tarefas, auditoria)
core/services/     regras de negócio: leitura de PDF, linha digitável, WhatsApp, régua, financeiro
core/views/        telas
core/templates/    HTML (responsivo, tema claro/escuro)
core/management/   comandos: processar_cobrancas, agendador, backup, dados_demo
gateway/           conexão do WhatsApp por QR Code (Node.js + Baileys)
```
