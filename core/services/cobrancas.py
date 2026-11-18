"""
Regras de negócio das cobranças: importação de boletos, envio pelo WhatsApp, régua automática,
comprovantes e confirmação de pagamento.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.models import (Cliente, Cobranca, Comprovante, Configuracao, LancamentoSocio, Mensagem,
                         Tarefa)

from . import whatsapp
from .auditoria import registrar
from .boleto_pdf import ler_pdf

log = logging.getLogger(__name__)

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro"]
STATUS_MSG_OK = [Mensagem.ST_ENVIADA, Mensagem.ST_ENTREGUE, Mensagem.ST_LIDA, Mensagem.ST_SIMULADA]
MAX_TENTATIVAS = 3


def competencia_de(d: date) -> date:
    return d.replace(day=1)


def nome_competencia(d: date) -> str:
    return f"{MESES[d.month - 1]}/{d.year}"


def brl(valor) -> str:
    v = f"{Decimal(valor):,.2f}"
    return v.replace(",", "X").replace(".", ",").replace("X", ".")


# ======================================================================================
# Importação de boletos (PDF)
# ======================================================================================
@dataclass
class ItemImportado:
    arquivo: str
    pagina: int
    status: str  # criada | atualizada | ignorada | erro
    mensagem: str
    cobranca: Cobranca | None = None


def _identificar_cliente(documentos: set, clientes_por_doc: dict):
    achados = {clientes_por_doc[d] for d in documentos if d in clientes_por_doc}
    return achados.pop() if len(achados) == 1 else None


@transaction.atomic
def importar_pdf(conteudo: bytes, nome_arquivo: str, cliente: Cliente | None = None,
                 substituir: bool = False, usuario=None) -> list[ItemImportado]:
    try:
        boletos = ler_pdf(conteudo)
    except Exception as exc:  # noqa: BLE001
        return [ItemImportado(nome_arquivo, 0, "erro", f"Não foi possível ler o PDF: {exc}")]

    clientes_por_doc = {c.documento_numeros: c for c in Cliente.objects.exclude(documento="") if c.documento_numeros}
    itens = []
    for b in boletos:
        alvo = cliente or _identificar_cliente(b.documentos, clientes_por_doc)
        if alvo is None:
            itens.append(ItemImportado(nome_arquivo, b.pagina, "erro",
                                       "Cliente não identificado pelo CPF/CNPJ. Importe selecionando o cliente."))
            continue
        if b.linha is None or b.linha.vencimento is None:
            itens.append(ItemImportado(nome_arquivo, b.pagina, "erro",
                                       "; ".join(b.alertas) or "Vencimento não identificado. Cadastre manualmente."))
            continue

        ld = b.linha
        valor = ld.valor or alvo.mensalidade
        alertas = list(b.alertas)
        if ld.valor is None:
            alertas.append("Boleto sem valor na linha digitável; usado o valor da mensalidade.")
        elif ld.valor != alvo.mensalidade:
            alertas.append(f"Valor do boleto (R$ {brl(ld.valor)}) difere da mensalidade cadastrada "
                           f"(R$ {brl(alvo.mensalidade)}).")

        comp = competencia_de(ld.vencimento)
        cob = Cobranca.objects.filter(cliente=alvo, competencia=comp).first()
        if cob and cob.status in (Cobranca.PAGA, Cobranca.CANCELADA):
            itens.append(ItemImportado(nome_arquivo, b.pagina, "ignorada",
                                       f"{nome_competencia(comp)} já está {cob.get_status_display().lower()}.", cob))
            continue
        if cob and cob.pdf and not substituir:
            itens.append(ItemImportado(nome_arquivo, b.pagina, "ignorada",
                                       f"{alvo} — {nome_competencia(comp)} já possui boleto (marque 'substituir').", cob))
            continue

        novo = cob is None
        if novo:
            cob = Cobranca(cliente=alvo, competencia=comp)
        cob.vencimento = ld.vencimento
        cob.valor = valor
        cob.linha_digitavel = ld.formatada
        cob.leitura_alerta = " ".join(alertas)[:300]
        if novo or cob.status == Cobranca.AGENDADA:
            cob.congelar_valores()
        else:
            cob.recalcular_partes()
        cob.pdf_nome_original = nome_arquivo[:200]
        cob.pdf.save("boleto.pdf", ContentFile(b.pdf_bytes), save=False)
        cob.save()
        if alertas:
            Tarefa.criar_unica(Tarefa.REVISAR_PDF, f"Revisar boleto de {alvo} ({nome_competencia(comp)})",
                               cliente=alvo, cobranca=cob, prazo=ld.vencimento - timedelta(days=3))
        Tarefa.objects.filter(tipo=Tarefa.BOLETO_FALTANDO, cliente=alvo, concluida=False,
                              titulo__icontains=nome_competencia(comp)).update(
            concluida=True, concluida_em=timezone.now())
        itens.append(ItemImportado(
            nome_arquivo, b.pagina, "criada" if novo else "atualizada",
            f"{alvo} — {nome_competencia(comp)} — venc. {ld.vencimento:%d/%m/%Y} — R$ {brl(valor)}"
            + (f" ⚠ {cob.leitura_alerta}" if alertas else ""), cob))

    registrar("boletos.importar", nome_arquivo,
              {"itens": [(i.status, i.mensagem) for i in itens]}, usuario=usuario)
    return itens


# ======================================================================================
# Envio de mensagens
# ======================================================================================
def parametros_template(cob: Cobranca):
    return [
        cob.cliente.primeiro_nome or cob.cliente.nome,
        nome_competencia(cob.competencia),
        brl(cob.valor),
        cob.vencimento.strftime("%d/%m/%Y"),
        cob.linha_digitavel or "consulte o PDF anexo",
    ]


def texto_previsto(cob: Cobranca, tipo: str) -> str:
    nome, comp, valor, venc, linha = parametros_template(cob)
    cfg = Configuracao.get()
    modelo = cfg.texto_atraso if tipo == Mensagem.TIPO_ATRASO else cfg.texto_boleto
    dados = {"nome": nome, "competencia": comp, "valor": valor, "vencimento": venc, "linha": linha,
             "empresa": cfg.nome_empresa}
    try:
        return modelo.format(**dados)
    except (KeyError, IndexError, ValueError):
        return modelo


def ja_enviada(cob: Cobranca, tipo: str) -> bool:
    return cob.mensagens.filter(tipo=tipo, status__in=STATUS_MSG_OK).exists()


def tentativas_falhas(cob: Cobranca, tipo: str) -> int:
    return cob.mensagens.filter(tipo=tipo, status=Mensagem.ST_FALHA).count()


class EnvioBloqueado(Exception):
    pass


def enviar_cobranca(cob_id: int, tipo: str, usuario=None, automatica=False, forcar=False) -> Mensagem:
    """
    Envia o boleto (tipo=boleto) ou o lembrete de atraso (tipo=atraso), com o PDF anexado.

    Três fases, para não segurar o banco durante o envio (o gateway espaça as mensagens):
      1. reserva: confere status (pago/cancelado NUNCA é cobrado) e registra a mensagem como pendente;
      2. envio pelo WhatsApp, fora de transação;
      3. resultado: grava o status da mensagem.
    A mensagem pendente da fase 1 impede envio duplicado em paralelo.
    """
    cfg = Configuracao.get()
    with transaction.atomic():
        cob = Cobranca.objects.select_for_update().select_related("cliente").get(pk=cob_id)
        if cob.status in (Cobranca.PAGA, Cobranca.CANCELADA):
            raise EnvioBloqueado(f"Cobrança está {cob.get_status_display().lower()}: envio cancelado.")
        if not cob.cliente.whatsapp_e164:
            raise EnvioBloqueado("Cliente sem número de WhatsApp cadastrado.")
        if cob.mensagens.filter(tipo=tipo, status=Mensagem.ST_PENDENTE,
                                criado_em__gte=timezone.now() - timedelta(minutes=15)).exists():
            raise EnvioBloqueado("Já existe um envio desta mensagem em andamento.")
        if automatica and not forcar and ja_enviada(cob, tipo):
            raise EnvioBloqueado("Mensagem já enviada anteriormente.")
        texto = texto_previsto(cob, tipo)
        msg = Mensagem.objects.create(
            cliente=cob.cliente, cobranca=cob, tipo=tipo, automatica=automatica,
            telefone=cob.cliente.whatsapp_e164, conteudo=texto, enviada_por=usuario,
        )

    template = cfg.template_atraso if tipo == Mensagem.TIPO_ATRASO else cfg.template_boleto
    pdf = None
    if cob.pdf:
        with cob.pdf.open("rb") as f:
            pdf = f.read()
    try:
        msg.wa_message_id = whatsapp.enviar_cobranca(
            cob.cliente.whatsapp_e164, texto, pdf, _nome_pdf(cob), template, cfg.template_idioma,
            parametros_template(cob))
        msg.status = Mensagem.ST_SIMULADA if whatsapp.simulacao() else Mensagem.ST_ENVIADA
    except whatsapp.WhatsAppIndisponivel as exc:
        # não é falha da mensagem: o número está desconectado. Será tentado na próxima execução.
        msg.delete()
        avisar_desconectado()
        raise EnvioBloqueado(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        msg.status = Mensagem.ST_FALHA
        msg.erro = str(exc)[:2000]
        log.warning("Falha no envio da cobrança %s: %s", cob.pk, exc)

    with transaction.atomic():
        msg.save()
        if msg.status != Mensagem.ST_FALHA:
            Cobranca.objects.filter(pk=cob.pk, status=Cobranca.AGENDADA).update(status=Cobranca.ENVIADA)

    if msg.status == Mensagem.ST_FALHA:
        if not automatica or tentativas_falhas(cob, tipo) >= MAX_TENTATIVAS:
            Tarefa.criar_unica(Tarefa.FALHA_ENVIO, f"Falha ao enviar WhatsApp para {cob.cliente}",
                               cliente=cob.cliente, cobranca=cob, prazo=timezone.localdate())
    registrar("whatsapp.enviar", cob, {"tipo": tipo, "automatica": automatica, "status": msg.status,
                                       "erro": msg.erro}, usuario=usuario)
    return msg


def avisar_desconectado():
    Tarefa.criar_unica(Tarefa.FALHA_ENVIO,
                       "WhatsApp desconectado: escaneie o QR Code em Configurações para retomar os envios",
                       prazo=timezone.localdate())


def _nome_pdf(cob):
    return f"boleto-{cob.competencia:%Y-%m}.pdf"


# ======================================================================================
# Régua automática (executada periodicamente pelo agendador)
# ======================================================================================
def dentro_da_janela(cfg: Configuracao, agora=None) -> bool:
    agora = timezone.localtime(agora)
    if not cfg.enviar_fim_de_semana and agora.weekday() >= 5:
        return False
    return cfg.hora_inicio_envio <= agora.hour < cfg.hora_fim_envio


def processar_regua(agora=None, enviar=True) -> dict:
    cfg = Configuracao.get()
    agora = agora or timezone.now()
    hoje = timezone.localtime(agora).date()
    pode_enviar = enviar and cfg.envio_automatico_ativo and dentro_da_janela(cfg, agora)
    if pode_enviar:
        if whatsapp.conectado():
            Tarefa.objects.filter(tipo=Tarefa.FALHA_ENVIO, cliente=None, cobranca=None, concluida=False,
                                  titulo__startswith="WhatsApp desconectado").update(
                concluida=True, concluida_em=timezone.now())
        else:
            pode_enviar = False
            avisar_desconectado()
    res = {"vencidas": 0, "boletos": 0, "lembretes": 0, "falhas": 0, "bloqueios": 0, "faltando": 0,
           "enviando": pode_enviar}

    # 1) atualiza status de vencidas
    res["vencidas"] = Cobranca.objects.filter(
        status__in=[Cobranca.AGENDADA, Cobranca.ENVIADA], vencimento__lt=hoje).update(status=Cobranca.VENCIDA)

    abertas = (Cobranca.objects.filter(status__in=Cobranca.STATUS_ABERTOS, cliente__ativo=True)
               .select_related("cliente"))
    for cob in abertas:
        cli = cob.cliente
        # 2) sugestão de bloqueio no portal
        if (cob.vencimento + timedelta(days=cfg.dias_sugerir_bloqueio) <= hoje
                and cli.situacao_licenca == Cliente.LIC_LIBERADO and cob.status != Cobranca.INFORMADO):
            if not Tarefa.objects.filter(tipo=Tarefa.BLOQUEAR, cobranca=cob, concluida=False).exists():
                Tarefa.objects.create(tipo=Tarefa.BLOQUEAR, cliente=cli, cobranca=cob, prazo=hoje,
                                      titulo=f"Avaliar bloqueio de {cli} no portal (vencido em {cob.vencimento:%d/%m})")
                res["bloqueios"] += 1

        if not (pode_enviar and cli.envio_automatico):
            continue
        tipo = None
        # 3) D-N: envio do boleto (janela: de N dias antes até o dia do vencimento)
        if (cob.vencimento - timedelta(days=cfg.dias_antes_vencimento) <= hoje <= cob.vencimento
                and not ja_enviada(cob, Mensagem.TIPO_BOLETO)
                and tentativas_falhas(cob, Mensagem.TIPO_BOLETO) < MAX_TENTATIVAS):
            tipo = Mensagem.TIPO_BOLETO
        # 4) D+N: lembrete de atraso (uma única vez; respeita pausa por comprovante)
        elif (hoje >= cob.vencimento + timedelta(days=cfg.dias_apos_vencimento)
              and cob.status == Cobranca.VENCIDA
              and not (cob.lembretes_pausados_ate and cob.lembretes_pausados_ate >= hoje)
              and not ja_enviada(cob, Mensagem.TIPO_ATRASO)
              and tentativas_falhas(cob, Mensagem.TIPO_ATRASO) < MAX_TENTATIVAS):
            tipo = Mensagem.TIPO_ATRASO
        if tipo is None:
            continue
        if not cob.pdf:
            Tarefa.criar_unica(Tarefa.BOLETO_FALTANDO,
                               f"Boleto de {cli} ({nome_competencia(cob.competencia)}) sem PDF — envio pendente",
                               cliente=cli, cobranca=cob, prazo=hoje)
            continue
        try:
            msg = enviar_cobranca(cob.pk, tipo, automatica=True)
        except EnvioBloqueado as exc:
            log.info("Envio ignorado (%s): %s", cob, exc)
            continue
        if msg.status == Mensagem.ST_FALHA:
            res["falhas"] += 1
        else:
            res["boletos" if tipo == Mensagem.TIPO_BOLETO else "lembretes"] += 1

    # 5) clientes ativos sem boleto cadastrado para o próximo vencimento
    res["faltando"] = verificar_boletos_faltando(hoje)
    return res


def proximo_vencimento(cli: Cliente, hoje: date) -> date:
    import calendar
    ano, mes = hoje.year, hoje.month
    for _ in range(2):
        ultimo = calendar.monthrange(ano, mes)[1]
        d = date(ano, mes, min(cli.dia_vencimento, ultimo))
        if d >= hoje:
            return d
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    return d


def verificar_boletos_faltando(hoje: date, antecedencia=7) -> int:
    criadas = 0
    for cli in Cliente.objects.filter(ativo=True):
        venc = proximo_vencimento(cli, hoje)
        if (venc - hoje).days > antecedencia:
            continue
        comp = competencia_de(venc)
        if not Cobranca.objects.filter(cliente=cli, competencia=comp).exclude(pdf="").exists() and \
                not Cobranca.objects.filter(cliente=cli, competencia=comp, status__in=[Cobranca.PAGA, Cobranca.CANCELADA]).exists():
            titulo = f"Boleto de {cli} ({nome_competencia(comp)}) não cadastrado — vence {venc:%d/%m}"
            if not Tarefa.objects.filter(tipo=Tarefa.BOLETO_FALTANDO, cliente=cli, concluida=False,
                                         titulo__icontains=nome_competencia(comp)).exists():
                Tarefa.objects.create(tipo=Tarefa.BOLETO_FALTANDO, titulo=titulo, cliente=cli, prazo=venc)
                criadas += 1
    return criadas


# ======================================================================================
# Comprovantes e confirmação de pagamento
# ======================================================================================
def cobranca_mais_antiga_aberta(cli: Cliente):
    return (cli.cobrancas.filter(status__in=[Cobranca.VENCIDA, Cobranca.ENVIADA, Cobranca.INFORMADO,
                                             Cobranca.AGENDADA])
            .order_by("vencimento").first())


@transaction.atomic
def registrar_comprovante(cli: Cliente, conteudo: bytes, nome: str, mime: str, origem: str,
                          cobranca: Cobranca | None = None, telefone="", usuario=None) -> Comprovante:
    """Comprovante = evidência. Muda o status para 'pagamento informado' e pausa lembretes. NÃO baixa."""
    cfg = Configuracao.get()
    cob = cobranca or cobranca_mais_antiga_aberta(cli)
    comp = Comprovante(cliente=cli, cobranca=cob, mime=mime, origem=origem, telefone_origem=telefone)
    comp.arquivo.save(nome, ContentFile(conteudo), save=False)
    comp.save()
    if cob and cob.aberta:
        cob.status = Cobranca.INFORMADO
        cob.lembretes_pausados_ate = timezone.localdate() + timedelta(days=cfg.dias_pausa_comprovante)
        cob.save(update_fields=["status", "lembretes_pausados_ate", "atualizado_em"])
    Tarefa.criar_unica(
        Tarefa.COMPROVANTE,
        f"Conferir com o sócio o comprovante de {cli}"
        + (f" ({nome_competencia(cob.competencia)})" if cob else " (sem cobrança em aberto)"),
        cliente=cli, cobranca=cob, prazo=timezone.localdate() + timedelta(days=1))
    registrar("comprovante.recebido", cli, {"origem": origem, "cobranca": cob.pk if cob else None}, usuario=usuario)
    return comp


@transaction.atomic
def confirmar_pagamento(cob_id: int, pago_em: date, valor_pago: Decimal | None, fonte: str,
                        obs: str = "", usuario=None, request=None) -> Cobranca:
    if fonte not in dict(Cobranca.FONTE_CHOICES):
        raise ValueError("Fonte de confirmação inválida.")
    cfg = Configuracao.get()
    cob = Cobranca.objects.select_for_update().select_related("cliente").get(pk=cob_id)
    if cob.status == Cobranca.PAGA:
        return cob
    if cob.status == Cobranca.CANCELADA:
        raise ValueError("Cobrança cancelada não pode ser marcada como paga.")

    cob.status = Cobranca.PAGA
    cob.pago_em = pago_em
    cob.valor_pago = valor_pago or cob.valor
    cob.confirmacao_fonte = fonte
    cob.confirmacao_obs = obs[:255]
    cob.confirmado_por = usuario
    cob.confirmado_em = timezone.now()
    base = cob.valor_pago if cfg.base_calculo_repasse == Configuracao.BASE_VALOR_PAGO else cob.valor
    cob.recalcular_partes(base)
    cob.save()

    LancamentoSocio.objects.update_or_create(
        cobranca=cob,
        defaults=dict(data=pago_em, tipo=LancamentoSocio.CREDITO, valor=cob.parte_titular,
                      descricao=f"{cob.cliente} — {nome_competencia(cob.competencia)}", registrado_por=usuario),
    )
    Tarefa.objects.filter(cobranca=cob, concluida=False,
                          tipo__in=[Tarefa.COMPROVANTE, Tarefa.BLOQUEAR, Tarefa.FALHA_ENVIO, Tarefa.BOLETO_FALTANDO]
                          ).update(concluida=True, concluida_em=timezone.now(), concluida_por=usuario)
    if cob.cliente.situacao_licenca == Cliente.LIC_BLOQUEADO:
        Tarefa.criar_unica(Tarefa.LIBERAR, f"Liberar {cob.cliente} no portal (pagamento confirmado)",
                           cliente=cob.cliente, prazo=timezone.localdate())
    registrar("cobranca.confirmar_pagamento", cob,
              {"pago_em": pago_em, "valor_pago": cob.valor_pago, "fonte": fonte, "obs": obs,
               "parte_titular": cob.parte_titular}, request=request, usuario=usuario)
    return cob


@transaction.atomic
def desfazer_pagamento(cob_id: int, motivo: str, usuario=None, request=None) -> Cobranca:
    cob = Cobranca.objects.select_for_update().get(pk=cob_id)
    if cob.status != Cobranca.PAGA:
        return cob
    antes = {"pago_em": cob.pago_em, "valor_pago": cob.valor_pago, "fonte": cob.confirmacao_fonte}
    LancamentoSocio.objects.filter(cobranca=cob).delete()
    cob.status = Cobranca.VENCIDA if cob.vencimento < timezone.localdate() else Cobranca.ENVIADA
    cob.pago_em = cob.valor_pago = cob.confirmado_por = cob.confirmado_em = None
    cob.confirmacao_fonte = cob.confirmacao_obs = ""
    cob.recalcular_partes()
    cob.save()
    registrar("cobranca.desfazer_pagamento", cob, {"motivo": motivo, "antes": antes}, request=request, usuario=usuario)
    return cob


# ======================================================================================
# Webhook do WhatsApp
# ======================================================================================
MAPA_STATUS = {"sent": Mensagem.ST_ENVIADA, "delivered": Mensagem.ST_ENTREGUE, "read": Mensagem.ST_LIDA,
               "failed": Mensagem.ST_FALHA}
ORDEM_STATUS = [Mensagem.ST_ENVIADA, Mensagem.ST_ENTREGUE, Mensagem.ST_LIDA]


def cliente_por_telefone(numero: str):
    """Compara DDD + 8 últimos dígitos (resolve a diferença do 9º dígito no WhatsApp)."""
    dig = "".join(c for c in numero if c.isdigit())
    if dig.startswith("55"):
        dig = dig[2:]
    if len(dig) < 10:
        return None
    ddd, final = dig[:2], dig[-8:]
    for c in Cliente.objects.exclude(whatsapp="", telefone=""):
        n = c.whatsapp_e164[2:] if c.whatsapp_e164 else ""
        if n[:2] == ddd and n[-8:] == final:
            return c
    return None


def processar_webhook(dados: dict):
    for entry in dados.get("entry", []):
        for change in entry.get("changes", []):
            valor = change.get("value", {})
            for st in valor.get("statuses", []):
                _atualizar_status(st)
            for m in valor.get("messages", []):
                _mensagem_recebida(m)


def _atualizar_status(st):
    msg = Mensagem.objects.filter(wa_message_id=st.get("id")).first()
    if not msg:
        return
    novo = MAPA_STATUS.get(st.get("status"))
    if not novo:
        return
    if novo == Mensagem.ST_FALHA:
        msg.status = novo
        msg.erro = "; ".join(f"{e.get('code')}: {e.get('title')} {e.get('error_data', {}).get('details', '')}"
                             for e in st.get("errors", []))[:2000]
        if msg.cobranca:
            Tarefa.criar_unica(Tarefa.FALHA_ENVIO, f"WhatsApp não entregue para {msg.cliente}",
                               cliente=msg.cliente, cobranca=msg.cobranca, prazo=timezone.localdate())
    elif msg.status in ORDEM_STATUS and ORDEM_STATUS.index(novo) <= ORDEM_STATUS.index(msg.status):
        return
    else:
        msg.status = novo
    msg.save(update_fields=["status", "erro", "atualizado_em"])


def _mensagem_recebida(m):
    telefone = m.get("from", "")
    if m.get("id") and Mensagem.objects.filter(wa_message_id=m["id"]).exists():
        return  # a Meta pode reenviar o mesmo evento
    cli = cliente_por_telefone(telefone)
    tipo = m.get("type")
    conteudo = ""
    if tipo == "text":
        conteudo = m.get("text", {}).get("body", "")
    elif tipo in ("image", "document"):
        midia = m.get(tipo, {})
        conteudo = f"[{'Imagem' if tipo == 'image' else 'Documento'}] {midia.get('caption', '') or midia.get('filename', '')}"
        if cli:
            try:
                dados, mime = whatsapp.baixar_midia(midia["id"])
                nome = midia.get("filename") or f"comprovante.{whatsapp.extensao_de(mime)}"
                registrar_comprovante(cli, dados, nome, mime, Comprovante.ORIGEM_WHATSAPP, telefone=telefone)
                conteudo += " — anexado como comprovante"
            except Exception as exc:  # noqa: BLE001
                log.exception("Falha ao baixar mídia")
                conteudo += f" — falha ao baixar: {exc}"
    else:
        conteudo = f"[{tipo}]"
    Mensagem.objects.create(cliente=cli, tipo=Mensagem.TIPO_RECEBIDA, telefone=telefone, conteudo=conteudo,
                            status=Mensagem.ST_RECEBIDA, wa_message_id=m.get("id", ""))


# ======================================================================================
# Eventos do gateway QR Code
# ======================================================================================
MAPA_STATUS_GATEWAY = {"enviada": Mensagem.ST_ENVIADA, "entregue": Mensagem.ST_ENTREGUE,
                       "lida": Mensagem.ST_LIDA, "falha": Mensagem.ST_FALHA}


def processar_evento_gateway(ev: dict):
    import base64
    if ev.get("tipo") == "status":
        novo = MAPA_STATUS_GATEWAY.get(ev.get("status"))
        if novo:
            _atualizar_status({"id": ev.get("id"), "status": {v: k for k, v in MAPA_STATUS.items()}[novo]})
        return
    if ev.get("tipo") != "mensagem":
        return
    if ev.get("id") and Mensagem.objects.filter(wa_message_id=ev["id"]).exists():
        return
    telefone = ev.get("de", "")
    cli = cliente_por_telefone(telefone) if telefone else None
    conteudo = ev.get("texto", "")
    midia = ev.get("midia")
    if midia:
        rotulo = "[Imagem]" if midia.get("tipo") == "image" else "[Documento]"
        conteudo = f"{rotulo} {conteudo}".strip()
        if cli:
            dados = base64.b64decode(midia.get("base64", ""))
            registrar_comprovante(cli, dados, midia.get("nome") or "comprovante", midia.get("mime", ""),
                                  Comprovante.ORIGEM_WHATSAPP, telefone=telefone)
            conteudo += " — anexado como comprovante"
    elif ev.get("erro_midia"):
        conteudo += f" [falha ao baixar mídia: {ev['erro_midia']}]"
    Mensagem.objects.create(cliente=cli, tipo=Mensagem.TIPO_RECEBIDA, telefone=telefone or ev.get("nome", "?"),
                            conteudo=conteudo or "[mensagem]", status=Mensagem.ST_RECEBIDA,
                            wa_message_id=ev.get("id", ""))
