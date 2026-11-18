from core.models import Cobranca, Configuracao, Tarefa


def globais(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {
        "cfg_global": Configuracao.get(),
        "nav_tarefas": Tarefa.objects.filter(concluida=False).count(),
        "nav_informados": Cobranca.objects.filter(status=Cobranca.INFORMADO).count(),
    }
