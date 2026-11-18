from datetime import date, timedelta
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.forms import CobrancaForm, ComprovanteForm, ConfirmarPagamentoForm, ImportarBoletosForm
from core.models import Cobranca, Mensagem
from core.services import cobrancas as svc
from core.services import financeiro
from core.services.auditoria import registrar
from core.views.util import voltar


def _mes_param(request):
    valor = request.GET.get("mes")
    if valor:
        try:
            a, m = valor.split("-")
            return date(int(a), int(m), 1)
        except ValueError:
            pass
    return None


@login_required
def lista(request):
    qs = Cobranca.objects.select_related("cliente")
    mes = _mes_param(request)
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    if mes:
        qs = qs.filter(competencia=mes)
    if status == "abertas":
        qs = qs.filter(status__in=Cobranca.STATUS_ABERTOS)
    elif status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(cliente__nome__icontains=q) | Q(linha_digitavel__icontains=q))
    if not (mes or status or q):
        qs = qs.filter(status__in=Cobranca.STATUS_ABERTOS)
        status = "abertas"
    qs = qs.order_by("vencimento", "cliente__nome")[:500]
    return render(request, "core/cobrancas_lista.html", {
        "cobrancas": qs, "status": status, "q": q, "mes": mes.strftime("%Y-%m") if mes else "",
        "status_choices": Cobranca.STATUS_CHOICES,
    })


@login_required
def detalhe(request, pk):
    cob = get_object_or_404(Cobranca.objects.select_related("cliente", "confirmado_por"), pk=pk)
    texto = svc.texto_previsto(cob, Mensagem.TIPO_ATRASO if cob.vencimento < timezone.localdate()
                               else Mensagem.TIPO_BOLETO)
    return render(request, "core/cobranca_detalhe.html", {
        "cob": cob,
        "mensagens": cob.mensagens.select_related("enviada_por"),
        "comprovantes": cob.comprovantes.all(),
        "repasse": financeiro.alocacao_fifo().get(cob.pk),
        "form_pag": ConfirmarPagamentoForm(initial={"pago_em": timezone.localdate(), "valor_pago": cob.valor}),
        "comp_form": ComprovanteForm(),
        "wa_link": f"https://wa.me/{cob.cliente.whatsapp_e164}?text={quote(texto)}" if cob.cliente.whatsapp_e164 else "",
        "boleto_enviado": svc.ja_enviada(cob, Mensagem.TIPO_BOLETO),
        "atraso_enviado": svc.ja_enviada(cob, Mensagem.TIPO_ATRASO),
    })


def _salvar_form(request, form, criando):
    cob = form.save(commit=False)
    if criando or cob.status == Cobranca.AGENDADA:
        cob.congelar_valores()
    else:
        cob.recalcular_partes()
    pdf = form.cleaned_data.get("pdf_upload")
    if pdf:
        cob.pdf_nome_original = pdf.name[:200]
        cob.pdf.save("boleto.pdf", ContentFile(pdf.read()), save=False)
    if cob.status in (Cobranca.AGENDADA, Cobranca.ENVIADA) and cob.vencimento < timezone.localdate():
        cob.status = Cobranca.VENCIDA
    cob.save()
    registrar("cobranca.criar" if criando else "cobranca.editar", cob,
              {k: str(form.cleaned_data[k]) for k in form.changed_data if k != "pdf_upload"}, request=request)
    return cob


@login_required
def nova(request):
    inicial = {}
    if request.GET.get("cliente"):
        from core.models import Cliente
        cli = Cliente.objects.filter(pk=request.GET["cliente"]).first()
        if cli:
            hoje = timezone.localdate()
            venc = svc.proximo_vencimento(cli, hoje)
            inicial = {"cliente": cli, "valor": cli.mensalidade, "vencimento": venc, "competencia": venc.replace(day=1)}
    form = CobrancaForm(request.POST or None, request.FILES or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        if Cobranca.objects.filter(cliente=form.cleaned_data["cliente"],
                                   competencia=form.cleaned_data["competencia"]).exists():
            form.add_error("competencia", "Já existe cobrança deste cliente para este mês.")
        else:
            cob = _salvar_form(request, form, True)
            messages.success(request, "Cobrança cadastrada e agendada.")
            return redirect("cobranca_detalhe", cob.pk)
    return render(request, "core/form.html", {"form": form, "titulo": "Nova cobrança", "voltar": "cobrancas",
                                              "multipart": True})


@login_required
def editar(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk)
    if cob.status in (Cobranca.PAGA, Cobranca.CANCELADA):
        messages.error(request, "Cobranças pagas ou canceladas não podem ser editadas.")
        return redirect("cobranca_detalhe", pk)
    form = CobrancaForm(request.POST or None, request.FILES or None, instance=cob)
    if request.method == "POST" and form.is_valid():
        _salvar_form(request, form, False)
        messages.success(request, "Cobrança atualizada.")
        return redirect("cobranca_detalhe", pk)
    return render(request, "core/form.html", {"form": form, "titulo": f"Editar cobrança — {cob}",
                                              "voltar": "cobrancas", "multipart": True})


@login_required
def importar(request):
    form = ImportarBoletosForm(request.POST or None, request.FILES or None)
    itens = None
    if request.method == "POST" and form.is_valid():
        itens = []
        for f in form.cleaned_data["arquivos"]:
            conteudo = f.read()
            if not conteudo.startswith(b"%PDF"):
                itens.append(svc.ItemImportado(f.name, 0, "erro", "Arquivo não é PDF."))
                continue
            itens.extend(svc.importar_pdf(conteudo, f.name, form.cleaned_data["cliente"],
                                          form.cleaned_data["substituir"], usuario=request.user))
        criadas = sum(1 for i in itens if i.status in ("criada", "atualizada"))
        messages.success(request, f"{criadas} boleto(s) importado(s) e agendado(s).")
        form = ImportarBoletosForm()
    return render(request, "core/importar.html", {"form": form, "itens": itens})


@login_required
@require_POST
def enviar(request, pk):
    tipo = request.POST.get("tipo", Mensagem.TIPO_BOLETO)
    if tipo not in (Mensagem.TIPO_BOLETO, Mensagem.TIPO_ATRASO):
        tipo = Mensagem.TIPO_BOLETO
    try:
        msg = svc.enviar_cobranca(pk, tipo, usuario=request.user)
    except svc.EnvioBloqueado as exc:
        messages.error(request, str(exc))
    else:
        if msg.status == Mensagem.ST_FALHA:
            messages.error(request, f"Falha no envio: {msg.erro}")
        elif msg.status == Mensagem.ST_SIMULADA:
            messages.warning(request, "Modo simulação: mensagem registrada, mas não enviada (configure a API).")
        else:
            messages.success(request, "Mensagem enviada pelo WhatsApp.")
    return voltar(request, f"/cobrancas/{pk}/")


@login_required
@require_POST
def confirmar(request, pk):
    form = ConfirmarPagamentoForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            svc.confirmar_pagamento(pk, d["pago_em"], d["valor_pago"], d["fonte"], d["obs"],
                                    usuario=request.user, request=request)
            messages.success(request, "Pagamento confirmado. Sua parte foi lançada na conta com o sócio.")
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        erros = "; ".join(f"{form.fields[k].label if k in form.fields else k}: {', '.join(v)}"
                          for k, v in form.errors.items())
        messages.error(request, f"Não confirmado — {erros}")
    return voltar(request, f"/cobrancas/{pk}/")


@login_required
@require_POST
def desfazer(request, pk):
    motivo = request.POST.get("motivo", "").strip()
    if not motivo:
        messages.error(request, "Informe o motivo para desfazer a confirmação.")
    else:
        svc.desfazer_pagamento(pk, motivo, usuario=request.user, request=request)
        messages.success(request, "Confirmação desfeita. O lançamento na conta do sócio foi removido.")
    return redirect("cobranca_detalhe", pk)


@login_required
@require_POST
def cancelar(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk)
    motivo = request.POST.get("motivo", "").strip()
    if cob.status == Cobranca.PAGA:
        messages.error(request, "Desfaça a confirmação antes de cancelar.")
    elif not motivo:
        messages.error(request, "Informe o motivo do cancelamento.")
    else:
        cob.status = Cobranca.CANCELADA
        cob.save(update_fields=["status", "atualizado_em"])
        cob.tarefas.filter(concluida=False).update(concluida=True, concluida_por=request.user)
        registrar("cobranca.cancelar", cob, {"motivo": motivo}, request=request)
        messages.success(request, "Cobrança cancelada. Nenhuma mensagem será enviada.")
    return redirect("cobranca_detalhe", pk)


@login_required
@require_POST
def reabrir(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk, status=Cobranca.CANCELADA)
    cob.status = Cobranca.VENCIDA if cob.vencimento < timezone.localdate() else Cobranca.AGENDADA
    cob.save(update_fields=["status", "atualizado_em"])
    registrar("cobranca.reabrir", cob, request=request)
    messages.success(request, "Cobrança reaberta.")
    return redirect("cobranca_detalhe", pk)


@login_required
@require_POST
def pausar(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk)
    dias = max(1, min(int(request.POST.get("dias", 5) or 5), 60))
    cob.lembretes_pausados_ate = timezone.localdate() + timedelta(days=dias)
    cob.save(update_fields=["lembretes_pausados_ate", "atualizado_em"])
    registrar("cobranca.pausar_lembretes", cob, {"dias": dias}, request=request)
    messages.success(request, f"Lembretes pausados até {cob.lembretes_pausados_ate:%d/%m/%Y}.")
    return redirect("cobranca_detalhe", pk)
