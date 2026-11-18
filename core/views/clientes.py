from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.forms import ClienteForm, ComprovanteForm
from core.models import Cliente, Cobranca, Comprovante, HistoricoValorCliente
from core.services import financeiro
from core.services.auditoria import registrar
from core.services.cobrancas import registrar_comprovante
from core.views.util import voltar


@login_required
def lista(request):
    q = request.GET.get("q", "").strip()
    filtro = request.GET.get("f", "ativos")
    qs = Cliente.objects.annotate(
        abertas=Count("cobrancas", filter=Q(cobrancas__status__in=Cobranca.STATUS_ABERTOS)),
        vencidas=Count("cobrancas", filter=Q(cobrancas__status=Cobranca.VENCIDA)),
    )
    if q:
        qs = qs.filter(Q(nome__icontains=q) | Q(documento__icontains=q) | Q(whatsapp__icontains=q)
                       | Q(nome_contato__icontains=q))
    if filtro == "ativos":
        qs = qs.filter(ativo=True)
    elif filtro == "inativos":
        qs = qs.filter(ativo=False)
    elif filtro == "bloqueados":
        qs = qs.filter(situacao_licenca=Cliente.LIC_BLOQUEADO)
    elif filtro == "inadimplentes":
        qs = qs.filter(vencidas__gt=0)
    return render(request, "core/clientes_lista.html", {"clientes": qs, "q": q, "f": filtro})


def _salvar(request, form, cliente=None):
    antes = None
    if cliente:
        antes = (cliente.mensalidade, cliente.custo_sistema, cliente.percentual_titular)
    obj = form.save()
    depois = (obj.mensalidade, obj.custo_sistema, obj.percentual_titular)
    if antes != depois:
        HistoricoValorCliente.objects.create(cliente=obj, mensalidade=obj.mensalidade,
                                             custo_sistema=obj.custo_sistema,
                                             percentual_titular=obj.percentual_titular, alterado_por=request.user)
        # cobranças ainda não enviadas acompanham o novo valor de divisão
        for cob in obj.cobrancas.filter(status=Cobranca.AGENDADA):
            cob.congelar_valores()
            cob.save()
    registrar("cliente.criar" if cliente is None else "cliente.editar", obj,
              {k: str(v) for k, v in form.cleaned_data.items() if k in form.changed_data}, request=request)
    return obj


@login_required
def novo(request):
    form = ClienteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = _salvar(request, form)
        messages.success(request, f"Cliente {obj} cadastrado.")
        return redirect("cliente_detalhe", obj.pk)
    return render(request, "core/form.html", {"form": form, "titulo": "Novo cliente", "voltar": "clientes",
                                              "preview_repasse": True})


@login_required
def editar(request, pk):
    cli = get_object_or_404(Cliente, pk=pk)
    form = ClienteForm(request.POST or None, instance=cli)
    if request.method == "POST" and form.is_valid():
        _salvar(request, form, Cliente.objects.get(pk=pk))
        messages.success(request, "Cliente atualizado.")
        return redirect("cliente_detalhe", pk)
    return render(request, "core/form.html", {"form": form, "titulo": f"Editar {cli}", "voltar": "clientes",
                                              "preview_repasse": True})


@login_required
def detalhe(request, pk):
    cli = get_object_or_404(Cliente, pk=pk)
    rel = financeiro.relatorio_cliente(cli)
    aloc = financeiro.alocacao_fifo()
    for c in rel["cobrancas"]:
        c.repasse = aloc.get(c.pk)
    return render(request, "core/cliente_detalhe.html", {
        "cli": cli, "rel": rel, "partes": cli.calcular_partes(),
        "mensagens": cli.mensagens.select_related("cobranca")[:30],
        "comprovantes": cli.comprovantes.select_related("cobranca")[:20],
        "tarefas": cli.tarefas.filter(concluida=False),
        "historico": cli.historico_valores.select_related("alterado_por")[:10],
        "comp_form": ComprovanteForm(),
    })


@login_required
@require_POST
def alternar_licenca(request, pk):
    cli = get_object_or_404(Cliente, pk=pk)
    cli.situacao_licenca = (Cliente.LIC_LIBERADO if cli.situacao_licenca == Cliente.LIC_BLOQUEADO
                            else Cliente.LIC_BLOQUEADO)
    cli.save(update_fields=["situacao_licenca", "atualizado_em"])
    from core.models import Tarefa
    tipo = Tarefa.LIBERAR if cli.situacao_licenca == Cliente.LIC_LIBERADO else Tarefa.BLOQUEAR
    cli.tarefas.filter(tipo=tipo, concluida=False).update(concluida=True, concluida_por=request.user)
    registrar("cliente.licenca", cli, {"situacao": cli.situacao_licenca}, request=request)
    messages.success(request, f"{cli}: marcado como {cli.get_situacao_licenca_display().lower()} no portal.")
    return voltar(request, f"/clientes/{pk}/")


@login_required
@require_POST
def anexar_comprovante(request, pk):
    cli = get_object_or_404(Cliente, pk=pk)
    form = ComprovanteForm(request.POST, request.FILES)
    cob = None
    if request.POST.get("cobranca"):
        cob = get_object_or_404(Cobranca, pk=request.POST["cobranca"], cliente=cli)
    if form.is_valid():
        f = form.cleaned_data["arquivo"]
        registrar_comprovante(cli, f.read(), f.name, f.content_type or "", Comprovante.ORIGEM_MANUAL,
                              cobranca=cob, usuario=request.user)
        messages.success(request, "Comprovante anexado. A cobrança aguarda confirmação do sócio.")
    else:
        messages.error(request, "Envie um arquivo válido.")
    return voltar(request, f"/clientes/{pk}/")
