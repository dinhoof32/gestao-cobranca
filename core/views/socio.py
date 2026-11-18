"""
Conta corrente com o sócio + link público de conferência (o sócio confirma o que compensou).
"""
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.http import require_POST

from core.forms import AjusteForm, PixForm
from core.models import Cobranca, Comprovante, Configuracao, LancamentoSocio
from core.services import financeiro
from core.services import cobrancas as svc
from core.services.auditoria import registrar


def _periodo(request):
    hoje = timezone.localdate()
    try:
        inicio = date.fromisoformat(request.GET["inicio"]) if request.GET.get("inicio") else None
        fim = date.fromisoformat(request.GET["fim"]) if request.GET.get("fim") else None
    except ValueError:
        inicio = fim = None
    if not inicio and not fim:
        inicio = (date(hoje.year - 1, 12, 1) if hoje.month == 1 else date(hoje.year, hoje.month - 1, 1))
    return inicio, fim


@login_required
def conta(request):
    inicio, fim = _periodo(request)
    anterior, linhas, final = financeiro.extrato_socio(inicio, fim)
    aloc = financeiro.alocacao_fifo()
    pendentes = (Cobranca.objects.filter(pk__in=[k for k, v in aloc.items() if v["pendente"] > 0])
                 .select_related("cliente").order_by("pago_em"))
    for c in pendentes:
        c.repasse = aloc[c.pk]
    cfg = Configuracao.get()
    return render(request, "core/socio.html", {
        "anterior": anterior, "linhas": linhas, "final": final, "saldo": financeiro.saldo_socio(),
        "inicio": inicio, "fim": fim, "pendentes": pendentes,
        "pix_form": PixForm(initial={"data": timezone.localdate()}),
        "ajuste_form": AjusteForm(initial={"data": timezone.localdate()}),
        "cfg": cfg,
        "link_socio": f"{settings.SITE_URL}/conferencia/{cfg.socio_token}/" if cfg.socio_token else "",
        "aguardando": Cobranca.objects.filter(status=Cobranca.INFORMADO).count(),
    })


@login_required
@require_POST
def registrar_pix(request):
    form = PixForm(request.POST, request.FILES)
    if form.is_valid():
        l = form.save(commit=False)
        l.tipo = LancamentoSocio.PIX
        l.descricao = l.descricao or "PIX recebido do sócio"
        l.registrado_por = request.user
        l.save()
        registrar("socio.pix", f"R$ {l.valor}", {"data": l.data, "valor": l.valor}, request=request)
        messages.success(request, f"PIX de R$ {svc.brl(l.valor)} registrado. Saldo atualizado.")
    else:
        messages.error(request, "Verifique os dados do PIX.")
    return redirect("socio")


@login_required
@require_POST
def registrar_ajuste(request):
    form = AjusteForm(request.POST)
    if form.is_valid():
        l = form.save(commit=False)
        l.tipo = LancamentoSocio.AJUSTE
        l.registrado_por = request.user
        l.save()
        registrar("socio.ajuste", f"R$ {l.valor}", {"motivo": l.descricao}, request=request)
        messages.success(request, "Ajuste registrado.")
    else:
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("socio")


@login_required
@require_POST
def excluir_lancamento(request, pk):
    l = get_object_or_404(LancamentoSocio, pk=pk)
    if l.tipo == LancamentoSocio.CREDITO:
        messages.error(request, "Créditos de mensalidade são removidos desfazendo a confirmação da cobrança.")
    else:
        registrar("socio.excluir_lancamento", f"{l.get_tipo_display()} R$ {l.valor}",
                  {"data": l.data, "descricao": l.descricao}, request=request)
        l.delete()
        messages.success(request, "Lançamento excluído.")
    return redirect("socio")


@login_required
def extrato_conferencia(request):
    """Página para imprimir / copiar e enviar ao sócio."""
    inicio, fim = _periodo(request)
    anterior, linhas, final = financeiro.extrato_socio(inicio, fim)
    cfg = Configuracao.get()
    texto = [f"Extrato de repasses — {cfg.nome_empresa}",
             f"Período: {inicio:%d/%m/%Y} a {(fim or timezone.localdate()):%d/%m/%Y}" if inicio else "",
             f"Saldo anterior: R$ {svc.brl(anterior)}", ""]
    for it in linhas:
        l = it["l"]
        sinal = "-" if l.tipo == LancamentoSocio.PIX or l.valor < 0 else "+"
        texto.append(f"{l.data:%d/%m} {sinal} R$ {svc.brl(abs(l.valor))}  {l.descricao or l.get_tipo_display()}")
    texto += ["", f"SALDO A RECEBER: R$ {svc.brl(final)}"]
    return render(request, "core/extrato_conferencia.html", {
        "anterior": anterior, "linhas": linhas, "final": final, "inicio": inicio, "fim": fim, "cfg": cfg,
        "texto": "\n".join(t for t in texto if t is not None),
    })


@login_required
@require_POST
def gerenciar_link(request):
    cfg = Configuracao.get()
    acao = request.POST.get("acao")
    if acao == "gerar":
        cfg.gerar_token_socio()
        cfg.socio_link_ativo = True
        cfg.save(update_fields=["socio_link_ativo"])
        messages.success(request, "Novo link gerado. O link anterior deixou de funcionar.")
    elif acao == "desativar":
        cfg.socio_link_ativo = False
        cfg.save(update_fields=["socio_link_ativo"])
        messages.success(request, "Link do sócio desativado.")
    elif acao == "ativar" and cfg.socio_token:
        cfg.socio_link_ativo = True
        cfg.save(update_fields=["socio_link_ativo"])
        messages.success(request, "Link do sócio ativado.")
    registrar("socio.link", acao, request=request)
    return redirect("socio")


# ---------------------------------------------------------------------------
# Área pública do sócio (acesso por token, sem login)
# ---------------------------------------------------------------------------
def _cfg_por_token(token):
    cfg = Configuracao.get()
    if not (cfg.socio_link_ativo and cfg.socio_token and constant_time_compare(cfg.socio_token, token)):
        raise Http404
    return cfg


def conferencia_socio(request, token):
    cfg = _cfg_por_token(token)
    if request.method == "POST":
        cob = get_object_or_404(Cobranca, pk=request.POST.get("cobranca"), status__in=Cobranca.STATUS_ABERTOS)
        try:
            d = date.fromisoformat(request.POST.get("pago_em") or "")
        except ValueError:
            d = timezone.localdate()
        d = min(d, timezone.localdate())
        svc.confirmar_pagamento(cob.pk, d, None, Cobranca.FONTE_SOCIO_LINK,
                                f"Confirmado por {cfg.nome_socio} pelo link de conferência", request=request)
        messages.success(request, f"{cob.cliente} — {svc.nome_competencia(cob.competencia)} confirmado. Obrigado!")
        return redirect("conferencia_socio", token)
    hoje = timezone.localdate()
    abertas = (Cobranca.objects.filter(status__in=Cobranca.STATUS_ABERTOS, vencimento__lte=hoje + timedelta(days=5))
               .select_related("cliente").prefetch_related("comprovantes").order_by("-status", "vencimento"))
    recentes = (Cobranca.objects.filter(status=Cobranca.PAGA, confirmado_em__isnull=False)
                .select_related("cliente").order_by("-confirmado_em")[:10])
    return render(request, "core/conferencia_socio.html", {
        "cfg": cfg, "abertas": abertas, "recentes": recentes, "token": token, "hoje": hoje,
    })


def comprovante_socio(request, token, pk):
    _cfg_por_token(token)
    comp = get_object_or_404(Comprovante, pk=pk, cobranca__status__in=Cobranca.STATUS_ABERTOS)
    return FileResponse(comp.arquivo.open("rb"), content_type=comp.mime or "application/octet-stream")
