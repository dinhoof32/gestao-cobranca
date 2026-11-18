import hashlib
import hmac
import io
import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (Cliente, Cobranca, Comprovante, Configuracao, LancamentoSocio, Mensagem,
                         Tarefa, calcular_partes)
from core.services import cobrancas as svc
from core.services import financeiro
from core.services.linha_digitavel import LinhaInvalida, decodificar, montar_linha

CAMPO_LIVRE = "1234567890123456789012345"


def pdf_boletos(boletos_por_pagina, documento="12.345.678/0001-95"):
    """Gera PDF de teste. boletos_por_pagina: lista de listas de linhas digitáveis."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    for pagina in boletos_por_pagina:
        n = len(pagina)
        for i, linha in enumerate(pagina):
            topo = h - i * (h / n)
            c.drawString(40, topo - 40, "Banco Teste S.A. - Recibo do pagador")
            c.drawString(40, topo - 60, f"Pagador: Cliente Teste CNPJ {documento}")
            ld = decodificar(linha)
            c.drawString(40, topo - 80, ld.formatada)
        c.showPage()
    c.save()
    return buf.getvalue()


def agora_util():
    """Hoje às 10h (horário local) — dentro da janela de envio."""
    hoje = timezone.localdate()
    return timezone.make_aware(datetime(hoje.year, hoje.month, hoje.day, 10, 0))


class Base(TestCase):
    def setUp(self):
        cfg = Configuracao.get()
        cfg.enviar_fim_de_semana = True
        cfg.save()
        self.cli = Cliente.objects.create(
            nome="Padaria Exemplo", documento="12.345.678/0001-95", whatsapp="(11) 98765-4321",
            mensalidade=Decimal("200.00"), custo_sistema=Decimal("55.00"), dia_vencimento=10)
        self.user = User.objects.create_user("admin", password="SenhaForte#2026")

    def cobranca(self, vencimento, pdf=True, **kw):
        c = Cobranca(cliente=self.cli, competencia=vencimento.replace(day=1), vencimento=vencimento,
                     valor=kw.pop("valor", Decimal("200.00")), **kw)
        c.congelar_valores()
        if pdf:
            c.pdf.save("b.pdf", ContentFile(b"%PDF-1.4 teste"), save=False)
        c.save()
        return c


class LinhaDigitavelTests(TestCase):
    def test_ida_e_volta(self):
        venc = timezone.localdate() + timedelta(days=10)
        linha = montar_linha("001", CAMPO_LIVRE, venc, Decimal("200.00"))
        ld = decodificar(linha)
        self.assertEqual(ld.valor, Decimal("200.00"))
        self.assertEqual(ld.vencimento, venc)
        self.assertEqual(ld.banco, "001")

    def test_digito_invalido(self):
        linha = montar_linha("341", CAMPO_LIVRE, timezone.localdate(), Decimal("99.90"))
        errada = linha[:5] + str((int(linha[5]) + 1) % 10) + linha[6:]
        with self.assertRaises(LinhaInvalida):
            decodificar(errada)

    def test_fator_reiniciado_2025(self):
        # fator 1000 = 22/02/2025 (reinício do fator de vencimento)
        from core.services.linha_digitavel import vencimento_do_fator
        from datetime import date
        self.assertEqual(vencimento_do_fator(1000, date(2025, 3, 1)), date(2025, 2, 22))


class RepasseTests(TestCase):
    def test_exemplo_do_usuario(self):
        # (200 − 55) × 50% = 72,50
        tit, soc = calcular_partes(Decimal("200"), Decimal("55"), Decimal("50"))
        self.assertEqual(tit, Decimal("72.50"))
        self.assertEqual(soc, Decimal("127.50"))

    def test_custo_maior_que_valor_nao_gera_negativo(self):
        tit, soc = calcular_partes(Decimal("50"), Decimal("80"), Decimal("50"))
        self.assertEqual(tit, Decimal("0.00"))


class ImportacaoTests(Base):
    def test_carne_com_varios_boletos_por_pagina(self):
        hoje = timezone.localdate()
        linhas = [montar_linha("237", CAMPO_LIVRE, (hoje.replace(day=10) + timedelta(days=31 * i)).replace(day=10),
                               Decimal("200.00")) for i in range(4)]
        pdf = pdf_boletos([linhas[:3], linhas[3:]])
        itens = svc.importar_pdf(pdf, "carne.pdf")
        criadas = [i for i in itens if i.status == "criada"]
        self.assertEqual(len(criadas), 4, [i.mensagem for i in itens])
        self.assertEqual(Cobranca.objects.filter(cliente=self.cli).count(), 4)
        c = Cobranca.objects.order_by("vencimento").first()
        self.assertEqual(c.parte_titular, Decimal("72.50"))
        self.assertTrue(c.pdf)
        # reimportar não duplica
        itens2 = svc.importar_pdf(pdf, "carne.pdf")
        self.assertTrue(all(i.status == "ignorada" for i in itens2))

    def test_cliente_nao_identificado(self):
        linha = montar_linha("001", CAMPO_LIVRE, timezone.localdate() + timedelta(days=5), Decimal("200"))
        itens = svc.importar_pdf(pdf_boletos([[linha]], documento="99.999.999/0001-99"), "x.pdf")
        self.assertEqual(itens[0].status, "erro")

    def test_valor_divergente_gera_tarefa(self):
        linha = montar_linha("001", CAMPO_LIVRE, timezone.localdate() + timedelta(days=5), Decimal("250"))
        svc.importar_pdf(pdf_boletos([[linha]]), "x.pdf")
        self.assertTrue(Tarefa.objects.filter(tipo=Tarefa.REVISAR_PDF).exists())


class ReguaTests(Base):
    def test_envia_boleto_dois_dias_antes_sem_duplicar(self):
        cob = self.cobranca(timezone.localdate() + timedelta(days=2))
        r = svc.processar_regua(agora=agora_util())
        self.assertEqual(r["boletos"], 1)
        svc.processar_regua(agora=agora_util())
        self.assertEqual(cob.mensagens.filter(tipo=Mensagem.TIPO_BOLETO).count(), 1)
        cob.refresh_from_db()
        self.assertEqual(cob.status, Cobranca.ENVIADA)

    def test_nao_envia_antes_da_hora(self):
        self.cobranca(timezone.localdate() + timedelta(days=5))
        r = svc.processar_regua(agora=agora_util())
        self.assertEqual(r["boletos"], 0)

    def test_lembrete_quatro_dias_depois(self):
        cob = self.cobranca(timezone.localdate() - timedelta(days=4))
        r = svc.processar_regua(agora=agora_util())
        self.assertEqual(r["lembretes"], 1)
        cob.refresh_from_db()
        self.assertEqual(cob.status, Cobranca.VENCIDA)

    def test_boleto_pago_nunca_e_cobrado(self):
        cob = self.cobranca(timezone.localdate() - timedelta(days=4))
        svc.confirmar_pagamento(cob.pk, timezone.localdate(), None, Cobranca.FONTE_SOCIO_AVISOU)
        r = svc.processar_regua(agora=agora_util())
        self.assertEqual(r["lembretes"], 0)
        with self.assertRaises(svc.EnvioBloqueado):
            svc.enviar_cobranca(cob.pk, Mensagem.TIPO_ATRASO)

    def test_comprovante_pausa_lembrete_mas_nao_baixa(self):
        cob = self.cobranca(timezone.localdate() - timedelta(days=4))
        svc.registrar_comprovante(self.cli, b"img", "c.jpg", "image/jpeg", Comprovante.ORIGEM_WHATSAPP)
        cob.refresh_from_db()
        self.assertEqual(cob.status, Cobranca.INFORMADO)
        self.assertFalse(LancamentoSocio.objects.exists())
        r = svc.processar_regua(agora=agora_util())
        self.assertEqual(r["lembretes"], 0)

    def test_fora_da_janela_nao_envia(self):
        self.cobranca(timezone.localdate() + timedelta(days=1))
        noite = agora_util().replace(hour=22)
        r = svc.processar_regua(agora=noite)
        self.assertEqual(r["boletos"], 0)

    def test_sem_pdf_gera_tarefa(self):
        self.cobranca(timezone.localdate() + timedelta(days=1), pdf=False)
        svc.processar_regua(agora=agora_util())
        self.assertTrue(Tarefa.objects.filter(tipo=Tarefa.BOLETO_FALTANDO).exists())

    def test_cliente_bloqueado_ganha_tarefa_de_liberar(self):
        self.cli.situacao_licenca = Cliente.LIC_BLOQUEADO
        self.cli.save()
        cob = self.cobranca(timezone.localdate() - timedelta(days=12))
        svc.confirmar_pagamento(cob.pk, timezone.localdate(), None, Cobranca.FONTE_SOCIO_LINK)
        self.assertTrue(Tarefa.objects.filter(tipo=Tarefa.LIBERAR, concluida=False).exists())


class SocioTests(Base):
    def test_saldo_e_fifo(self):
        c1 = self.cobranca(timezone.localdate() - timedelta(days=40))
        c2 = self.cobranca(timezone.localdate() - timedelta(days=5))
        hoje = timezone.localdate()
        svc.confirmar_pagamento(c1.pk, hoje - timedelta(days=30), None, Cobranca.FONTE_SOCIO_AVISOU)
        svc.confirmar_pagamento(c2.pk, hoje, None, Cobranca.FONTE_SOCIO_AVISOU)
        self.assertEqual(financeiro.saldo_socio(), Decimal("145.00"))
        LancamentoSocio.objects.create(tipo=LancamentoSocio.PIX, valor=Decimal("100.00"), data=hoje)
        self.assertEqual(financeiro.saldo_socio(), Decimal("45.00"))
        aloc = financeiro.alocacao_fifo()
        self.assertEqual(aloc[c1.pk]["situacao"], "repassado")
        self.assertEqual(aloc[c2.pk]["situacao"], "parcial")
        self.assertEqual(aloc[c2.pk]["pendente"], Decimal("45.00"))

    def test_desfazer_remove_credito(self):
        c = self.cobranca(timezone.localdate())
        svc.confirmar_pagamento(c.pk, timezone.localdate(), None, Cobranca.FONTE_SOCIO_AVISOU)
        svc.desfazer_pagamento(c.pk, "engano")
        self.assertEqual(financeiro.saldo_socio(), Decimal("0"))

    def test_link_do_socio(self):
        cfg = Configuracao.get()
        token = cfg.gerar_token_socio()
        cfg.socio_link_ativo = True
        cfg.save()
        c = self.cobranca(timezone.localdate() - timedelta(days=1))
        url = reverse("conferencia_socio", args=[token])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"cobranca": c.pk, "pago_em": timezone.localdate().isoformat()})
        c.refresh_from_db()
        self.assertEqual(c.status, Cobranca.PAGA)
        self.assertEqual(c.confirmacao_fonte, Cobranca.FONTE_SOCIO_LINK)
        self.assertEqual(self.client.get(reverse("conferencia_socio", args=["errado"])).status_code, 404)


@override_settings(WHATSAPP={"TOKEN": "t", "PHONE_NUMBER_ID": "1", "APP_SECRET": "segredo",
                             "VERIFY_TOKEN": "v", "API_VERSION": "v21.0", "SIMULACAO": False})
class WebhookTests(Base):
    def post(self, dados, assinar=True):
        corpo = json.dumps(dados).encode()
        sig = "sha256=" + hmac.new(b"segredo", corpo, hashlib.sha256).hexdigest() if assinar else "sha256=x"
        return self.client.post("/webhook/whatsapp/", corpo, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=sig)

    def test_verificacao(self):
        r = self.client.get("/webhook/whatsapp/", {"hub.mode": "subscribe", "hub.verify_token": "v",
                                                   "hub.challenge": "123"})
        self.assertEqual(r.content, b"123")

    def test_assinatura_invalida(self):
        self.assertEqual(self.post({}, assinar=False).status_code, 403)

    def test_status_e_comprovante(self):
        cob = self.cobranca(timezone.localdate() - timedelta(days=2))
        m = Mensagem.objects.create(cliente=self.cli, cobranca=cob, tipo="boleto", telefone="x",
                                    status=Mensagem.ST_ENVIADA, wa_message_id="wamid.1")
        self.post({"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1", "status": "read"}]}}]}]})
        m.refresh_from_db()
        self.assertEqual(m.status, Mensagem.ST_LIDA)
        with mock.patch("core.services.whatsapp.baixar_midia", return_value=(b"img", "image/jpeg")):
            # número sem o 9º dígito, como o WhatsApp às vezes entrega
            self.post({"entry": [{"changes": [{"value": {"messages": [
                {"from": "551187654321", "id": "wamid.2", "type": "image", "image": {"id": "mid"}}]}}]}]})
        cob.refresh_from_db()
        self.assertEqual(cob.status, Cobranca.INFORMADO)
        self.assertEqual(cob.comprovantes.count(), 1)


class TelasTests(Base):
    def test_login_e_paginas(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        r = self.client.post("/entrar/", {"usuario": "admin", "senha": "SenhaForte#2026"})
        self.assertEqual(r.status_code, 302)
        cob = self.cobranca(timezone.localdate() + timedelta(days=3))
        for url in ["/", "/clientes/", f"/clientes/{self.cli.pk}/", "/clientes/novo/", f"/clientes/{self.cli.pk}/editar/",
                    "/cobrancas/", f"/cobrancas/{cob.pk}/", "/cobrancas/nova/", "/cobrancas/importar/",
                    f"/cobrancas/{cob.pk}/editar/", "/socio/", "/socio/extrato/", "/tarefas/", "/mensagens/",
                    f"/clientes/{self.cli.pk}/conversa/", "/relatorios/", "/relatorios/?csv=1",
                    "/configuracoes/", "/auditoria/", "/seguranca/", f"/cobrancas/{cob.pk}/boleto.pdf"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_bloqueio_apos_tentativas(self):
        for _ in range(5):
            self.client.post("/entrar/", {"usuario": "admin", "senha": "errada"})
        r = self.client.post("/entrar/", {"usuario": "admin", "senha": "SenhaForte#2026"}, follow=True)
        self.assertContains(r, "Muitas tentativas")

    def test_arquivos_exigem_login(self):
        cob = self.cobranca(timezone.localdate())
        r = self.client.get(f"/cobrancas/{cob.pk}/boleto.pdf")
        self.assertEqual(r.status_code, 302)
