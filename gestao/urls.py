from django.contrib import admin
from django.urls import path

from core.views import auth, clientes, cobrancas, geral, socio

urlpatterns = [
    path("", geral.painel, name="painel"),
    # autenticação
    path("entrar/", auth.entrar, name="login"),
    path("entrar/verificacao/", auth.login_2fa, name="login_2fa"),
    path("sair/", auth.sair, name="logout"),
    path("seguranca/", auth.seguranca, name="seguranca"),
    # clientes
    path("clientes/", clientes.lista, name="clientes"),
    path("clientes/novo/", clientes.novo, name="cliente_novo"),
    path("clientes/<int:pk>/", clientes.detalhe, name="cliente_detalhe"),
    path("clientes/<int:pk>/editar/", clientes.editar, name="cliente_editar"),
    path("clientes/<int:pk>/licenca/", clientes.alternar_licenca, name="cliente_licenca"),
    path("clientes/<int:pk>/comprovante/", clientes.anexar_comprovante, name="cliente_comprovante"),
    path("clientes/<int:pk>/conversa/", geral.conversa, name="conversa"),
    # cobranças
    path("cobrancas/", cobrancas.lista, name="cobrancas"),
    path("cobrancas/nova/", cobrancas.nova, name="cobranca_nova"),
    path("cobrancas/importar/", cobrancas.importar, name="importar"),
    path("cobrancas/<int:pk>/", cobrancas.detalhe, name="cobranca_detalhe"),
    path("cobrancas/<int:pk>/editar/", cobrancas.editar, name="cobranca_editar"),
    path("cobrancas/<int:pk>/enviar/", cobrancas.enviar, name="cobranca_enviar"),
    path("cobrancas/<int:pk>/confirmar/", cobrancas.confirmar, name="cobranca_confirmar"),
    path("cobrancas/<int:pk>/desfazer/", cobrancas.desfazer, name="cobranca_desfazer"),
    path("cobrancas/<int:pk>/cancelar/", cobrancas.cancelar, name="cobranca_cancelar"),
    path("cobrancas/<int:pk>/reabrir/", cobrancas.reabrir, name="cobranca_reabrir"),
    path("cobrancas/<int:pk>/pausar/", cobrancas.pausar, name="cobranca_pausar"),
    path("cobrancas/<int:pk>/boleto.pdf", geral.arquivo_boleto, name="arquivo_boleto"),
    path("comprovantes/<int:pk>/", geral.arquivo_comprovante, name="arquivo_comprovante"),
    # sócio
    path("socio/", socio.conta, name="socio"),
    path("socio/pix/", socio.registrar_pix, name="socio_pix"),
    path("socio/ajuste/", socio.registrar_ajuste, name="socio_ajuste"),
    path("socio/lancamento/<int:pk>/excluir/", socio.excluir_lancamento, name="socio_excluir"),
    path("socio/lancamento/<int:pk>/comprovante/", geral.arquivo_pix, name="arquivo_pix"),
    path("socio/extrato/", socio.extrato_conferencia, name="socio_extrato"),
    path("socio/link/", socio.gerenciar_link, name="socio_link"),
    path("conferencia/<str:token>/", socio.conferencia_socio, name="conferencia_socio"),
    path("conferencia/<str:token>/comprovante/<int:pk>/", socio.comprovante_socio, name="conferencia_comprovante"),
    # demais
    path("tarefas/", geral.tarefas, name="tarefas"),
    path("tarefas/<int:pk>/concluir/", geral.concluir_tarefa, name="tarefa_concluir"),
    path("mensagens/", geral.mensagens, name="mensagens"),
    path("relatorios/", geral.relatorios, name="relatorios"),
    path("configuracoes/", geral.configuracoes, name="configuracoes"),
    path("auditoria/", geral.auditoria, name="auditoria"),
    path("webhook/whatsapp/", geral.webhook_whatsapp, name="webhook_whatsapp"),
    path("admin-tecnico/", admin.site.urls),
]
