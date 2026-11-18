import csv
import json
import logging
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Max, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.forms import ConfiguracaoForm, TarefaForm, TextoWhatsAppForm
from core.models import (Cliente, Cobranca, Comprovante, Configuracao, Mensagem, RegistroAuditoria,
                         Tarefa)
from core.services import cobrancas as svc
from core.services import financeiro, whatsapp
from core.services.auditoria import registrar
from core.views.util import voltar

log = logging.getLogger(__name__)


@login_required
def painel(request):
    return render(request, "core/painel.html", {"p": financeiro.painel(), "simulacao": whatsapp.simulacao()})


# ------------------------------------------------------------------ tarefas
@login_required
def tarefas(request):
    form = TarefaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        t = form.save()
        registrar("tarefa.criar", t, request=request)
        messages.success(request, "Tarefa criada.")
        return redirect("tarefas")
    ver = request.GET.get("ver", "abertas")
    qs = Tarefa.objects.select_related("cliente", "cobranca", "concluida_por")
    qs = qs.filter(concluida=(ver == "concluidas"))
    if ver == "concluidas":
        qs = qs.order_by("-concluida_em")[:200]
    return render(request, "core/tarefas.html", {"tarefas": qs, "form": form, "ver": ver,
                                                 "hoje": timezone.localdate()})


@login_required
@require_POST
def concluir_tarefa(request, pk):
    t = get_object_or_404(Tarefa, pk=pk)
    t.concluida = not t.concluida
    t.concluida_em = timezone.now() if t.concluida else None
    t.concluida_por = request.user if t.concluida else None
    t.save()
    # concluir "liberar/bloquear no portal" atualiza a situação do cliente
    if t.concluida and t.cliente and t.tipo in (Tarefa.LIBERAR, Tarefa.BLOQUEAR):
        t.cliente.situacao_licenca = Cliente.LIC_LIBERADO if t.tipo == Tarefa.LIBERAR else Cliente.LIC_BLOQUEADO
        t.cliente.save(update_fields=["situacao_licenca", "atualizado_em"])
    registrar("tarefa.concluir" if t.concluida else "tarefa.reabrir", t, request=request)
    return voltar(request, "/tarefas/")


# ------------------------------------------------------------------ mensagens
@login_required
def mensagens(request):
    tipo = request.GET.get("tipo", "")
    qs = Mensagem.objects.select_related("cliente", "cobranca", "enviada_por")
    if tipo == "falhas":
        qs = qs.filter(status=Mensagem.ST_FALHA)
    elif tipo:
        qs = qs.filter(tipo=tipo)
    pagina = Paginator(qs, 50).get_page(request.GET.get("p"))
    return render(request, "core/mensagens.html", {"pagina": pagina, "tipo": tipo,
                                                   "tipos": Mensagem.TIPO_CHOICES})


@login_required
def conversa(request, pk):
    cli = get_object_or_404(Cliente, pk=pk)
    form = TextoWhatsAppForm(request.POST or None)
    ultima_recebida = cli.mensagens.filter(tipo=Mensagem.TIPO_RECEBIDA).aggregate(m=Max("criado_em"))["m"]
    janela_aberta = bool(ultima_recebida and timezone.now() - ultima_recebida < timedelta(hours=24))
    if request.method == "POST" and form.is_valid():
        if not janela_aberta and not whatsapp.simulacao():
            messages.error(request, "Fora da janela de 24h: só é possível enviar templates (boleto/lembrete).")
        else:
            texto = form.cleaned_data["texto"]
            msg = Mensagem(cliente=cli, tipo=Mensagem.TIPO_MANUAL, telefone=cli.whatsapp_e164, conteudo=texto,
                           enviada_por=request.user)
            try:
                msg.wa_message_id = whatsapp.enviar_texto(cli.whatsapp_e164, texto)
                msg.status = Mensagem.ST_SIMULADA if whatsapp.simulacao() else Mensagem.ST_ENVIADA
            except Exception as exc:  # noqa: BLE001
                msg.status, msg.erro = Mensagem.ST_FALHA, str(exc)
            msg.save()
            registrar("whatsapp.texto", cli, {"status": msg.status}, request=request)
            return redirect("conversa", pk)
    historico = cli.mensagens.order_by("criado_em")
    return render(request, "core/conversa.html", {"cli": cli, "historico": historico, "form": form,
                                                  "janela_aberta": janela_aberta})


# ------------------------------------------------------------------ relatórios
def _mes(request, chave="mes"):
    v = request.GET.get(chave)
    if v:
        try:
            a, m = v.split("-")
            return date(int(a), int(m), 1)
        except ValueError:
            pass
    return timezone.localdate().replace(day=1)


@login_required
def relatorios(request):
    comp = _mes(request)
    resumo = financeiro.resumo_mes(comp)
    cobs = (Cobranca.objects.filter(competencia=comp).exclude(status=Cobranca.CANCELADA)
            .select_related("cliente").order_by("cliente__nome"))
    if request.GET.get("csv"):
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="faturamento-{comp:%Y-%m}.csv"'
        resp.write("﻿")
        w = csv.writer(resp, delimiter=";")
        w.writerow(["Cliente", "Vencimento", "Valor", "Custo sistema", "Sua parte", "Parte sócio", "Status",
                    "Pago em", "Valor pago", "Confirmação"])
        for c in cobs:
            w.writerow([c.cliente.nome, c.vencimento.strftime("%d/%m/%Y"), svc.brl(c.valor), svc.brl(c.custo_sistema),
                        svc.brl(c.parte_titular), svc.brl(c.parte_socio), c.get_status_display(),
                        c.pago_em.strftime("%d/%m/%Y") if c.pago_em else "",
                        svc.brl(c.valor_pago) if c.valor_pago else "", c.get_confirmacao_fonte_display()])
        registrar("relatorio.csv", f"{comp:%m/%Y}", request=request)
        return resp
    return render(request, "core/relatorios.html", {
        "comp": comp, "resumo": resumo, "cobrancas": cobs,
        "serie": financeiro.serie_mensal(comp, 12), "ranking": financeiro.ranking_inadimplencia(),
        "clientes": Cliente.objects.order_by("nome"),
    })


# ------------------------------------------------------------------ configurações
@login_required
def configuracoes(request):
    cfg = Configuracao.get()
    form = ConfiguracaoForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        form.save()
        registrar("configuracao.salvar", "Configuração",
                  {k: str(form.cleaned_data[k]) for k in form.changed_data}, request=request)
        messages.success(request, "Configurações salvas.")
        return redirect("configuracoes")
    w = settings.WHATSAPP
    return render(request, "core/configuracoes.html", {
        "form": form, "simulacao": whatsapp.simulacao(),
        "wa": {"token": bool(w["TOKEN"]), "phone": bool(w["PHONE_NUMBER_ID"]), "secret": bool(w["APP_SECRET"]),
               "verify": bool(w["VERIFY_TOKEN"]), "versao": w["API_VERSION"]},
        "webhook_url": f"{settings.SITE_URL}/webhook/whatsapp/",
        "exemplo": svc.texto_previsto(_cobranca_exemplo(), Mensagem.TIPO_BOLETO),
        "exemplo_atraso": svc.texto_previsto(_cobranca_exemplo(), Mensagem.TIPO_ATRASO),
    })


def _cobranca_exemplo():
    from decimal import Decimal
    hoje = timezone.localdate()
    return Cobranca(cliente=Cliente(nome="João da Silva"), competencia=hoje.replace(day=1), vencimento=hoje,
                    valor=Decimal("200.00"), linha_digitavel="00190.00009 01234.567890 12345.678901 2 12340000020000")


@login_required
def auditoria(request):
    qs = RegistroAuditoria.objects.select_related("usuario")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(acao__icontains=q) | Q(objeto__icontains=q) | Q(detalhes__icontains=q))
    pagina = Paginator(qs, 50).get_page(request.GET.get("p"))
    return render(request, "core/auditoria.html", {"pagina": pagina, "q": q})


# ------------------------------------------------------------------ arquivos protegidos
@login_required
def arquivo_boleto(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk)
    if not cob.pdf:
        raise Http404
    return FileResponse(cob.pdf.open("rb"), content_type="application/pdf",
                        filename=f"boleto-{cob.cliente.nome[:30]}-{cob.competencia:%Y-%m}.pdf")


@login_required
def arquivo_comprovante(request, pk):
    comp = get_object_or_404(Comprovante, pk=pk)
    return FileResponse(comp.arquivo.open("rb"), content_type=comp.mime or "application/octet-stream")


@login_required
def arquivo_pix(request, pk):
    from core.models import LancamentoSocio
    l = get_object_or_404(LancamentoSocio, pk=pk)
    if not l.comprovante:
        raise Http404
    return FileResponse(l.comprovante.open("rb"))


# ------------------------------------------------------------------ webhook WhatsApp
@csrf_exempt
def webhook_whatsapp(request):
    if request.method == "GET":
        verify = settings.WHATSAPP["VERIFY_TOKEN"]
        if (request.GET.get("hub.mode") == "subscribe" and verify
                and request.GET.get("hub.verify_token") == verify):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden("token inválido")
    if request.method != "POST":
        return HttpResponse(status=405)
    if not whatsapp.assinatura_valida(request.body, request.headers.get("X-Hub-Signature-256")):
        log.warning("Webhook com assinatura inválida rejeitado")
        return HttpResponseForbidden("assinatura inválida")
    try:
        svc.processar_webhook(json.loads(request.body or b"{}"))
    except Exception:  # noqa: BLE001 — sempre 200 para a Meta não ficar reenviando
        log.exception("Erro ao processar webhook")
    return HttpResponse("ok")
