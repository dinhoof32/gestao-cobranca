import json

from core.models import RegistroAuditoria


def ip_de(request):
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    ip = fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")
    return ip or None


def registrar(acao, objeto="", detalhes=None, request=None, usuario=None):
    if usuario is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        usuario = request.user
    if isinstance(detalhes, (dict, list)):
        detalhes = json.dumps(detalhes, ensure_ascii=False, default=str)
    RegistroAuditoria.objects.create(
        usuario=usuario, acao=acao, objeto=str(objeto)[:120], detalhes=detalhes or "", ip=ip_de(request),
    )
