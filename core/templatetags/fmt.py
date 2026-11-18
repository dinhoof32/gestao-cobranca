from decimal import Decimal

from django import template

register = template.Library()

CORES_STATUS = {
    "agendada": "neutro", "enviada": "info", "vencida": "perigo", "informado": "alerta",
    "paga": "ok", "cancelada": "neutro",
    "pendente": "neutro", "simulada": "alerta", "entregue": "info", "lida": "ok", "falha": "perigo",
    "recebida": "info", "liberado": "ok", "bloqueado": "perigo",
    "repassado": "ok", "parcial": "alerta",
}


@register.filter
def brl(valor):
    if valor in (None, ""):
        return "—"
    v = f"{Decimal(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {v}"


@register.filter
def cor(status):
    return CORES_STATUS.get(status, "neutro")


@register.filter
def abs_(valor):
    return abs(valor)
