"""
Cálculos financeiros: painel, conta corrente com o sócio (com alocação FIFO dos PIX) e relatórios.
"""
from collections import OrderedDict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from core.models import Cliente, Cobranca, LancamentoSocio, Mensagem, Tarefa

ZERO = Decimal("0.00")


def _soma(qs, campo):
    return qs.aggregate(t=Sum(campo))["t"] or ZERO


def saldo_socio() -> Decimal:
    total = ZERO
    for l in LancamentoSocio.objects.all().only("tipo", "valor"):
        total += l.efeito
    return total


def extrato_socio(inicio: date | None = None, fim: date | None = None):
    """Lançamentos com saldo acumulado. Retorna (saldo_anterior, linhas, saldo_final)."""
    saldo = ZERO
    anterior = ZERO
    linhas = []
    for l in LancamentoSocio.objects.select_related("cobranca__cliente").order_by("data", "id"):
        saldo += l.efeito
        if inicio and l.data < inicio:
            anterior = saldo
            continue
        if fim and l.data > fim:
            break
        linhas.append({"l": l, "saldo": saldo})
    final = linhas[-1]["saldo"] if linhas else anterior
    return anterior, linhas, final


def alocacao_fifo():
    """
    Distribui os pagamentos do sócio (PIX + ajustes negativos) entre as dívidas
    (sua parte de cada mensalidade + ajustes positivos), da mais antiga para a mais nova.
    Retorna {cobranca_id: {"devido", "repassado", "pendente", "situacao"}}.
    """
    lanc = list(LancamentoSocio.objects.order_by("data", "id"))
    disponivel = sum((l.valor if l.tipo == LancamentoSocio.PIX else -l.valor)
                     for l in lanc if l.tipo == LancamentoSocio.PIX or (l.tipo == LancamentoSocio.AJUSTE and l.valor < 0))
    res = {}
    for l in lanc:
        if l.tipo == LancamentoSocio.PIX or (l.tipo == LancamentoSocio.AJUSTE and l.valor < 0):
            continue
        aplicado = min(disponivel, l.valor)
        disponivel -= aplicado
        if l.cobranca_id:
            pend = l.valor - aplicado
            res[l.cobranca_id] = {
                "devido": l.valor, "repassado": aplicado, "pendente": pend,
                "situacao": "repassado" if pend == 0 else ("parcial" if aplicado > 0 else "pendente"),
            }
    return res


def resumo_mes(competencia: date) -> dict:
    qs = Cobranca.objects.filter(competencia=competencia).exclude(status=Cobranca.CANCELADA)
    pagas = qs.filter(status=Cobranca.PAGA)
    abertas = qs.filter(status__in=Cobranca.STATUS_ABERTOS)
    return {
        "total": _soma(qs, "valor"),
        "qtd": qs.count(),
        "recebido": _soma(pagas, "valor_pago"),
        "qtd_pagas": pagas.count(),
        "pendente": _soma(abertas, "valor"),
        "qtd_abertas": abertas.count(),
        "minha_parte_total": _soma(qs, "parte_titular"),
        "minha_parte_recebida": _soma(pagas, "parte_titular"),
        "parte_socio_total": _soma(qs, "parte_socio"),
        "custo_total": _soma(qs, "custo_sistema"),
    }


def painel(hoje: date | None = None) -> dict:
    hoje = hoje or timezone.localdate()
    comp = hoje.replace(day=1)
    abertas = Cobranca.objects.filter(status__in=Cobranca.STATUS_ABERTOS).select_related("cliente")
    vencidas = abertas.filter(vencimento__lt=hoje).order_by("vencimento")
    proximas = abertas.filter(vencimento__gte=hoje, vencimento__lte=hoje + timedelta(days=7)).order_by("vencimento")
    inicio_mes = timezone.make_aware(datetime(hoje.year, hoje.month, 1))
    msgs_mes = Mensagem.objects.filter(criado_em__gte=inicio_mes).exclude(tipo=Mensagem.TIPO_RECEBIDA)
    return {
        "hoje": hoje,
        "competencia": comp,
        "mes": resumo_mes(comp),
        "clientes_ativos": Cliente.objects.filter(ativo=True).count(),
        "clientes_bloqueados": Cliente.objects.filter(ativo=True, situacao_licenca=Cliente.LIC_BLOQUEADO).count(),
        "vencidas": vencidas[:12],
        "qtd_vencidas": vencidas.count(),
        "valor_vencido": _soma(vencidas, "valor"),
        "inadimplentes": vencidas.values("cliente").distinct().count(),
        "proximas": proximas[:12],
        "qtd_proximas": proximas.count(),
        "informados": abertas.filter(status=Cobranca.INFORMADO).count(),
        "msgs_mes": msgs_mes.count(),
        "msgs_falha": msgs_mes.filter(status=Mensagem.ST_FALHA).count(),
        "saldo_socio": saldo_socio(),
        "pix_mes": _soma(LancamentoSocio.objects.filter(tipo=LancamentoSocio.PIX, data__gte=comp), "valor"),
        "tarefas": Tarefa.objects.filter(concluida=False).select_related("cliente")[:10],
        "qtd_tarefas": Tarefa.objects.filter(concluida=False).count(),
        "serie": serie_mensal(hoje, 6),
    }


def serie_mensal(hoje: date, meses: int = 12):
    comps = []
    ano, mes = hoje.year, hoje.month
    for _ in range(meses):
        comps.append(date(ano, mes, 1))
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
    comps.reverse()
    dados = OrderedDict((c, {"comp": c, "previsto": ZERO, "recebido": ZERO, "minha": ZERO}) for c in comps)
    qs = (Cobranca.objects.filter(competencia__in=comps).exclude(status=Cobranca.CANCELADA)
          .values("competencia")
          .annotate(prev=Sum("valor"), rec=Sum("valor_pago", filter=Q(status=Cobranca.PAGA)),
                    minha=Sum("parte_titular", filter=Q(status=Cobranca.PAGA))))
    for r in qs:
        d = dados[r["competencia"]]
        d["previsto"], d["recebido"], d["minha"] = r["prev"] or ZERO, r["rec"] or ZERO, r["minha"] or ZERO
    maximo = max([d["previsto"] for d in dados.values()] + [Decimal(1)])
    for d in dados.values():
        d["pct_prev"] = int(d["previsto"] / maximo * 100)
        d["pct_rec"] = int(d["recebido"] / maximo * 100)
    return list(dados.values())


def relatorio_cliente(cli: Cliente):
    cobs = list(cli.cobrancas.order_by("-competencia"))
    pagas = [c for c in cobs if c.status == Cobranca.PAGA]
    atrasos = [(c.pago_em - c.vencimento).days for c in pagas if c.pago_em]
    return {
        "cobrancas": cobs,
        "total_pago": sum((c.valor_pago or ZERO for c in pagas), ZERO),
        "minha_parte_paga": sum((c.parte_titular for c in pagas), ZERO),
        "em_aberto": sum((c.valor for c in cobs if c.aberta), ZERO),
        "qtd_atrasos": sum(1 for a in atrasos if a > 0),
        "atraso_medio": round(sum(max(a, 0) for a in atrasos) / len(atrasos), 1) if atrasos else 0,
    }


def ranking_inadimplencia(limite=10):
    hoje = timezone.localdate()
    return (Cliente.objects.filter(cobrancas__status__in=Cobranca.STATUS_ABERTOS, cobrancas__vencimento__lt=hoje)
            .annotate(qtd=Count("cobrancas"), total=Sum("cobrancas__valor")).order_by("-total")[:limite])
