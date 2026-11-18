# Registro de Requisitos — Sistema de Gestão de Suporte e Cobrança

> Documento vivo. Atualizado a cada etapa da análise. Status: **FASE DE DESCOBERTA** (arquitetura ainda não definida).

## Contexto do negócio
- Atividade: suporte a sistemas comerciais, atendimento a clientes, manutenção e informática.
- Sociedade com 1 sócio. Clientes pagam mensalidade (sistema + suporte). Não há receitas avulsas.
- Possui CNPJ.
- Usuários do sistema: o titular (admin) e a funcionária/esposa (operadora). O sócio NÃO usa o sistema.

## Fatos levantados (rodada 2)
- F-01 Boletos emitidos por **bancos diferentes**; o titular já possui os PDFs.
- F-02 Envio hoje é manual pelo **WhatsApp pessoal**.
- F-03 A parte do titular **varia conforme o valor da mensalidade** de cada cliente.
- F-04 O sócio faz **PIX de valor livre**, agrupando várias mensalidades pagas, sem valor fixo e sem padrão.

## Fatos levantados (rodada 3)
- F-05 Boletos caem na **conta do sócio**; o titular NÃO tem acesso a essa conta.
- F-06 O sócio envia os boletos **do ano inteiro** de cada cliente (carnê anual); o titular envia mês a mês.
- F-07 Cliente inadimplente fica **bloqueado** no sistema comercial; a liberação é feita pelo titular em um
  **portal de licenças**. É nesse momento que o titular "descobre" o pagamento.
- F-08 Esposa/funcionária tem **acesso total**.

## Fatos levantados (rodada 4)
- F-09 Pagamento chega ao conhecimento do titular por: (a) comprovante enviado pelo cliente no WhatsApp,
  ou (b) aviso do sócio quando o boleto compensa.
- F-10 **Sem integração com o portal de licenças** (decisão do titular). Liberação/bloqueio continuam manuais.
- F-11 **Regra de repasse**: `parte_titular = (mensalidade − custo_do_sistema) × 50%`.
  Exemplo: 200 − 55 = 145 → 72,50 para cada sócio. O custo do sistema varia por cliente (módulos contratados).

## Requisitos funcionais
### RF-01 Clientes
- Cadastro com telefone, WhatsApp, valor da mensalidade, dia de vencimento, banco emissor do boleto.
- Regra de repasse por cliente: custo do sistema (R$) + percentual do titular sobre o líquido (padrão 50%).
- Histórico de alterações de mensalidade/custo com data de vigência (mudança não altera meses passados).
- Cada cobrança "congela" os valores (mensalidade, custo, parte titular, parte sócio) no momento da geração.

### RF-02 Boletos (multi-banco, via PDF)
- Upload em lote de PDFs (arrastar vários de uma vez) — **carnê anual**: um upload por cliente por ano
  gera as 12 cobranças e agenda todos os envios do ano.
- Separação automática de PDFs com várias páginas/boletos em boletos individuais.
- **Leitura automática do PDF**: extrair linha digitável, valor, vencimento, pagador (CPF/CNPJ) e
  vincular automaticamente ao cliente e à competência. Conferência manual só em caso de divergência.
- Validação da linha digitável (dígitos verificadores, valor e fator de vencimento) para evitar enviar boleto errado.

### RF-03 Régua de cobrança via WhatsApp (API oficial)
- D-2: template com PDF anexado. D+4: lembrete de atraso.
- Checagem de status "pago" imediatamente antes de cada disparo.
- Envio manual sob demanda; histórico com status (enviado/entregue/lido/falha).
- Respostas dos clientes centralizadas (caixa de entrada no sistema).

### RF-04 Confirmação de pagamento (multi-banco)
- Baixa só por fonte confiável: arquivo de retorno/extrato do banco (OFX/CNAB/CSV) ou API bancária.
- Conciliação automática por linha digitável/nosso número + valor + data.
- Declaração do cliente gera apenas "pagamento informado — aguardando confirmação".
- Comprovante recebido no WhatsApp é anexado automaticamente à cobrança do cliente (identificado pelo telefone),
  com leitura de valor/data quando possível, e entra na fila de conferência do sócio.
- Status de cobrança: agendada → enviada → vencida → pagamento informado → **pago (confirmado)** / cancelada.
- Confirmação registra quem confirmou, quando e a evidência (auditoria).

### RF-05 Conta corrente com o sócio (ledger)
- Crédito automático da parte do titular a cada mensalidade confirmada como paga.
- Débito a cada PIX recebido do sócio (lançado ou importado do extrato do titular).
- Saldo a receber sempre atualizado; alocação FIFO dos PIX às mensalidades mais antigas
  (com opção de ajuste manual).
- Extrato de conferência gerado para enviar ao sócio.
- Nenhuma transferência é feita pelo sistema (somente registro/conciliação).

### RF-06 Financeiro e painel
- (sem alteração) Totais do mês, recebido, pendente, inadimplentes, saldo com o sócio, histórico de PIX,
  relatórios mensal e por cliente, próximas tarefas.

### RF-07 Usuários e permissões
- Dois usuários com acesso total (titular e funcionária), cada um com login próprio (auditoria por pessoa).
- Perfis mantidos na arquitetura para futuras contratações.

### RF-08 Bloqueio/liberação no portal de licenças (manual, sem integração)
- Campo de situação da licença por cliente (liberado/bloqueado), atualizado pelo usuário.
- Tarefa automática "liberar cliente X no portal" quando um pagamento de cliente bloqueado for confirmado.
- Lista de sugestão de bloqueio para clientes vencidos além do prazo definido.

## Requisitos não funcionais / regras
- RNF-01 Sistema funcional real, responsivo, hospedável.
- RNF-02 WhatsApp apenas via API oficial (Cloud API). Número: ver decisão D-01.
- RNF-03 Autenticação (com 2FA), auditoria, backups, LGPD.
- RNF-04 Nenhum PIX/pagamento automático.
- RNF-05 Baixo custo de manutenção.

## Decisões em aberto
- D-01 Número do WhatsApp: (a) converter o número pessoal para o WhatsApp Business app e conectar à
  API em modo "coexistência" (mantém número e conversas; validar requisitos na implantação), ou
  (b) usar um número novo dedicado à empresa.
- D-02 Fonte confiável de confirmação = **o sócio** (dono da conta). Proposta: link de conferência para o sócio
  e/ou confirmação registrada pelo titular com a evidência "sócio avisou". Portal descartado.
- D-03 Comprovante do cliente deve pausar o lembrete D+4? (sugestão: sim, pausa por até X dias, sem marcar pago).

## Oportunidades identificadas
- OP-01 Leitura automática dos PDFs (elimina digitação e vinculação manual).
- OP-02 Conciliação automática do PIX do sócio pelo extrato da conta do titular.
- OP-03 Extrato de repasse mensal para o sócio.
- OP-04 Reajuste anual com aviso automático.
- OP-05 Mensagem de agradecimento/recibo ao confirmar pagamento.
- OP-06 Escalonamento de inadimplência (D+10, D+20 → alerta interno).
- OP-07 Avaliar, no futuro, centralizar a emissão em um único banco/intermediador com API (baixa automática por webhook).

## Decisões de implementação (v1 — 18/11/2026)
Padrões adotados para perguntas ainda não respondidas (todos ajustáveis em Configurações/cadastro):
- Juros/multa: por padrão NÃO entram na divisão (base = valor do boleto). Opção "valor efetivamente pago" disponível.
- Percentual de divisão: 50% padrão, editável por cliente.
- Comprovante do cliente pausa o lembrete por 5 dias e vira tarefa de conferência; não dá baixa.
- Confirmação: (a) link de conferência do sócio, ou (b) usuário registra "sócio confirmou" com aceite explícito.
- Sugestão de bloqueio no portal após 10 dias de atraso. Envios de seg a sex, 8h–18h.
- Stack: Python/Django, SQLite (PostgreSQL opcional), Docker, WhatsApp Cloud API oficial.

## Próximas etapas sugeridas
- Rotina de suporte (chamados, atendimentos, visitas) — ainda não levantada.
- Importação de extrato OFX da conta do titular para reconhecer PIX do sócio automaticamente.
- Reajuste anual de mensalidades com aviso ao cliente.

## Mudança de decisão (18/11/2026) — WhatsApp por QR Code
- O titular decidiu NÃO pagar API e conectar o WhatsApp por QR Code (substitui RNF-02).
- Risco aceito pelo titular: canal não oficial, possibilidade de bloqueio do número pela Meta.
- Mitigações: chip dedicado (recomendado), intervalo aleatório 20–45 s, simulação de digitação,
  horário comercial, apenas clientes cadastrados, verificação do número antes do envio.
- API oficial mantida como opção (WHATSAPP_PROVEDOR=oficial).
