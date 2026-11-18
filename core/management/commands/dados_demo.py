"""Cria dados fictícios para conhecer o sistema. NÃO use em produção."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Cliente, Cobranca, LancamentoSocio
from core.services import cobrancas as svc
from core.services import demo_pdf
from core.services.linha_digitavel import decodificar, montar_linha


class Command(BaseCommand):
    help = "Popula o banco com clientes e cobranças fictícios (demonstração)."

    def handle(self, *args, **opts):
        if Cliente.objects.exists():
            self.stdout.write("Já existem clientes; nada feito.")
            return
        hoje = timezone.localdate()
        dados = [("Mercado Bom Preço", 250, 70, 5), ("Padaria Pão Quente", 200, 55, 10),
                 ("Auto Peças Silva", 320, 90, 15), ("Farmácia Saúde+", 280, 75, 20),
                 ("Loja Moda Bela", 180, 50, 25), ("Restaurante Sabor Caseiro", 220, 55, 8),
                 ("Ótica Visão", 200, 55, 12), ("Pet Shop Amigo Fiel", 190, 50, 18)]
        for i, (nome, mens, custo, dia) in enumerate(dados):
            cli = Cliente.objects.create(
                nome=nome, nome_contato=nome.split()[0], documento=f"{11 + i}.222.333/0001-{10 + i}",
                whatsapp=f"(11) 9{8000 + i:04d}-{1000 + i:04d}", mensalidade=Decimal(mens),
                custo_sistema=Decimal(custo), dia_vencimento=dia, banco=["Itaú", "Bradesco", "Sicoob", "Inter"][i % 4])
            for delta in (-2, -1, 0, 1):
                total = hoje.year * 12 + hoje.month - 1 + delta
                mes = hoje.replace(year=total // 12, month=total % 12 + 1, day=1)
                venc = mes.replace(day=dia)
                linha = montar_linha(["341", "237", "756", "077"][i % 4], f"{i:05d}" + "0" * 20, venc, Decimal(mens))
                cob = Cobranca(cliente=cli, competencia=mes, vencimento=venc, valor=Decimal(mens),
                               linha_digitavel=decodificar(linha).formatada)
                cob.congelar_valores()
                cob.pdf.save("b.pdf", ContentFile(demo_pdf.gerar([linha], cli.nome, cli.documento)), save=False)
                cob.status = Cobranca.VENCIDA if venc < hoje else Cobranca.AGENDADA
                cob.save()
                if venc < hoje - timedelta(days=3) and not (i in (2, 5) and delta == -1) and not (i == 4 and delta == 0):
                    svc.confirmar_pagamento(cob.pk, venc + timedelta(days=i % 3), None, Cobranca.FONTE_SOCIO_AVISOU)
        LancamentoSocio.objects.create(tipo=LancamentoSocio.PIX, valor=Decimal("450.00"),
                                       data=hoje - timedelta(days=20), descricao="PIX do sócio")
        LancamentoSocio.objects.create(tipo=LancamentoSocio.PIX, valor=Decimal("380.00"),
                                       data=hoje - timedelta(days=4), descricao="PIX do sócio")
        Cliente.objects.filter(nome__startswith="Auto").update(situacao_licenca=Cliente.LIC_BLOQUEADO)
        svc.processar_regua(enviar=False)
        # carnê de exemplo (próximo ano, 3 boletos por página) para testar a importação
        cli = Cliente.objects.get(nome="Padaria Pão Quente")
        ano = hoje.year + 1
        linhas = [montar_linha("237", "00001" + "1" * 20, hoje.replace(year=ano, month=m, day=10), cli.mensalidade)
                  for m in range(1, 13)]
        destino = settings.BASE_DIR / "dados" / f"exemplo-carne-padaria-{ano}.pdf"
        destino.write_bytes(demo_pdf.gerar(linhas, cli.nome, cli.documento, por_pagina=3))
        self.stdout.write(self.style.SUCCESS(f"Dados de demonstração criados. Carnê de exemplo: {destino}"))
