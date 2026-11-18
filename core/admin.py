from django.contrib import admin

from .models import (Cliente, Cobranca, Comprovante, Configuracao, LancamentoSocio, Mensagem,
                     RegistroAuditoria, Tarefa)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nome", "mensalidade", "custo_sistema", "dia_vencimento", "ativo")
    search_fields = ("nome", "documento")


@admin.register(Cobranca)
class CobrancaAdmin(admin.ModelAdmin):
    list_display = ("cliente", "competencia", "vencimento", "valor", "status")
    list_filter = ("status",)


@admin.register(RegistroAuditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "usuario", "acao", "objeto", "ip")
    readonly_fields = [f.name for f in RegistroAuditoria._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register([Configuracao, Comprovante, Mensagem, LancamentoSocio, Tarefa])
admin.site.site_header = "Administração técnica"
