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

## Colocar em produção (recomendado: VPS de ~R$ 30/mês + Docker)

1. Copie o projeto para o servidor e crie o `.env` a partir do `.env.example` (gere uma `SECRET_KEY` longa).
2. `docker compose up -d --build`
3. Coloque um proxy com HTTPS na frente (o mais simples é o **Caddy**: `gestao.seudominio.com.br { reverse_proxy 127.0.0.1:8000 }`).
4. `docker compose exec web python manage.py createsuperuser` (um usuário para você, outro para sua esposa).
5. Ative a verificação em duas etapas em **Minha conta**.

Depois, conecte o WhatsApp pelo QR Code (seção abaixo). O serviço `agendador` roda a régua a cada 30 minutos e gera o backup diário às 02h em `dados/backups/`.
**Copie os backups para fora do servidor** (ex.: `rclone sync dados/backups gdrive:backups-gestao` no cron).
Para restaurar: extraia o `.tar.gz`, coloque o `db.sqlite3` e a pasta `arquivos/` em `dados/`.

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
