# Gestão de Suporte e Cobrança

Sistema para automatizar o envio mensal de boletos pelo **WhatsApp (API oficial da Meta)**, controlar
pagamentos e acompanhar a **conta corrente com o sócio**.

## O que o sistema faz

| Área | Funcionalidades |
|---|---|
| **Boletos** | Importa PDFs (inclusive o **carnê do ano inteiro**), lê linha digitável, valor e vencimento, confere os dígitos verificadores, identifica o cliente pelo CPF/CNPJ e recorta páginas com vários boletos |
| **Régua automática** | Envia o boleto **2 dias antes** do vencimento e um lembrete **4 dias depois**, se não estiver pago. Respeita horário comercial, nunca duplica e **nunca cobra boleto pago ou cancelado** (rechecagem com bloqueio no banco imediatamente antes do envio) |
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

Acesse http://localhost:8000. Sem credenciais da Meta, o WhatsApp fica em **modo simulação**: tudo é
registrado, mas nada é enviado.

Para rodar a régua manualmente: `python manage.py processar_cobrancas`
Para rodar os testes: `python manage.py test core`

## Colocar em produção (recomendado: VPS de ~R$ 30/mês + Docker)

1. Copie o projeto para o servidor e crie o `.env` a partir do `.env.example` (gere uma `SECRET_KEY` longa).
2. `docker compose up -d --build`
3. Coloque um proxy com HTTPS na frente (o mais simples é o **Caddy**: `gestao.seudominio.com.br { reverse_proxy 127.0.0.1:8000 }`).
4. `docker compose exec web python manage.py createsuperuser` (um usuário para você, outro para sua esposa).
5. Ative a verificação em duas etapas em **Minha conta**.

O serviço `agendador` roda a régua a cada 30 minutos e gera o backup diário às 02h em `dados/backups/`.
**Copie os backups para fora do servidor** (ex.: `rclone sync dados/backups gdrive:backups-gestao` no cron).
Para restaurar: extraia o `.tar.gz`, coloque o `db.sqlite3` e a pasta `arquivos/` em `dados/`.

## Configurar o WhatsApp oficial (Cloud API)

1. Crie uma conta em **business.facebook.com** com o CNPJ e verifique a empresa.
2. Em **developers.facebook.com**, crie um app do tipo *Business* e adicione o produto **WhatsApp**.
3. Adicione o número:
   - **Manter o número atual:** converta-o primeiro para o app *WhatsApp Business* e conecte pelo
     fluxo de "coexistência" (o app continua funcionando no celular). Confira os requisitos atuais da Meta na hora de fazer.
   - **Número novo:** um chip dedicado à empresa (mais simples).
4. Crie um **usuário do sistema** no Business Manager e gere um **token permanente** com as permissões
   `whatsapp_business_messaging` e `whatsapp_business_management`.
5. Preencha no `.env`: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET` (em
   *Configurações do app → Básico*) e invente um `WHATSAPP_VERIFY_TOKEN`.
6. Em *WhatsApp → Configuração*, cadastre o webhook `https://SEU-DOMINIO/webhook/whatsapp/` com o mesmo
   verify token e assine os campos **messages**.
7. Em *WhatsApp Manager → Modelos de mensagem*, crie os dois templates (categoria **Utilidade**,
   idioma **Português (BR)**, cabeçalho **Documento**):

**`envio_boleto`**
> Olá {{1}}, segue o boleto da mensalidade de {{2}} no valor de R$ {{3}}, com vencimento em {{4}}. Linha digitável: {{5}}. Qualquer dúvida, responda esta mensagem.

**`lembrete_atraso`**
> Olá {{1}}, identificamos que o boleto da mensalidade de {{2}}, no valor de R$ {{3}}, venceu em {{4}} e ainda consta em aberto. Segue o boleto novamente. Linha digitável: {{5}}. Se já realizou o pagamento, envie o comprovante por aqui e desconsidere esta mensagem.

Os nomes dos templates podem ser alterados em **Configurações**. A Meta cobra por mensagem de template
(categoria utilidade é das mais baratas); respostas dentro de 24h após o cliente escrever não são cobradas como template.

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
```
