"""
Modelo de dados.

Princípios:
- Valores financeiros em Decimal (nunca float).
- Cada cobrança CONGELA os valores do mês (mensalidade, custo, partes) no momento em que é criada:
  mudar o cadastro do cliente não altera o passado.
- Um boleto só vira PAGO por confirmação explícita, com origem e evidência registradas.
- A conta corrente com o sócio é um razão (ledger): créditos = sua parte das mensalidades pagas,
  débitos = PIX recebidos. Saldo = soma dos créditos − soma dos débitos.
"""
import secrets
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

DUAS_CASAS = Decimal("0.01")


def q2(valor):
    return Decimal(valor).quantize(DUAS_CASAS, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------------------
# Configuração geral (registro único)
# --------------------------------------------------------------------------------------
class Configuracao(models.Model):
    BASE_VALOR_BOLETO = "boleto"
    BASE_VALOR_PAGO = "pago"
    BASE_CHOICES = [
        (BASE_VALOR_BOLETO, "Valor do boleto (juros/multa ficam fora da divisão)"),
        (BASE_VALOR_PAGO, "Valor efetivamente pago (juros/multa entram na divisão)"),
    ]

    nome_empresa = models.CharField("Nome da empresa", max_length=120, default="Minha Empresa")
    nome_socio = models.CharField("Nome do sócio", max_length=120, default="Sócio")
    dias_antes_vencimento = models.PositiveSmallIntegerField(
        "Enviar boleto quantos dias ANTES do vencimento", default=2,
        validators=[MaxValueValidator(15)])
    dias_apos_vencimento = models.PositiveSmallIntegerField(
        "Enviar lembrete quantos dias APÓS o vencimento", default=4,
        validators=[MinValueValidator(1), MaxValueValidator(30)])
    dias_sugerir_bloqueio = models.PositiveSmallIntegerField(
        "Sugerir bloqueio no portal após quantos dias de atraso", default=10)
    dias_pausa_comprovante = models.PositiveSmallIntegerField(
        "Pausar lembretes por quantos dias após o cliente enviar comprovante", default=5)
    hora_inicio_envio = models.PositiveSmallIntegerField(
        "Horário inicial de envio (h)", default=8, validators=[MaxValueValidator(23)])
    hora_fim_envio = models.PositiveSmallIntegerField(
        "Horário final de envio (h)", default=18, validators=[MaxValueValidator(23)])
    enviar_fim_de_semana = models.BooleanField("Enviar aos sábados e domingos", default=False)
    envio_automatico_ativo = models.BooleanField(
        "Envio automático ativado (chave geral)", default=True)
    base_calculo_repasse = models.CharField(
        "Base de cálculo do repasse", max_length=10, choices=BASE_CHOICES, default=BASE_VALOR_BOLETO)

    # Nomes dos templates aprovados no WhatsApp Manager
    template_boleto = models.CharField("Template: envio do boleto", max_length=80, default="envio_boleto")
    template_atraso = models.CharField("Template: lembrete de atraso", max_length=80, default="lembrete_atraso")
    template_idioma = models.CharField("Idioma dos templates", max_length=10, default="pt_BR")

    # Link de conferência do sócio
    socio_token = models.CharField(max_length=64, blank=True)
    socio_link_ativo = models.BooleanField("Link de conferência do sócio ativo", default=False)

    class Meta:
        verbose_name = "Configuração"
        verbose_name_plural = "Configuração"

    def __str__(self):
        return "Configuração"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def gerar_token_socio(self):
        self.socio_token = secrets.token_urlsafe(32)
        self.save(update_fields=["socio_token"])
        return self.socio_token


# --------------------------------------------------------------------------------------
# Clientes
# --------------------------------------------------------------------------------------
class Cliente(models.Model):
    LIC_LIBERADO = "liberado"
    LIC_BLOQUEADO = "bloqueado"
    LIC_CHOICES = [(LIC_LIBERADO, "Liberado"), (LIC_BLOQUEADO, "Bloqueado")]

    nome = models.CharField("Nome / Razão social", max_length=150)
    nome_contato = models.CharField("Nome do contato (usado nas mensagens)", max_length=80, blank=True)
    documento = models.CharField("CPF/CNPJ", max_length=18, blank=True, db_index=True,
                                 help_text="Usado para reconhecer o cliente nos PDFs dos boletos.")
    telefone = models.CharField("Telefone", max_length=20, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True,
                                help_text="Com DDD. Ex.: (11) 98765-4321")
    email = models.EmailField("E-mail", blank=True)

    mensalidade = models.DecimalField("Mensalidade (R$)", max_digits=10, decimal_places=2)
    custo_sistema = models.DecimalField("Custo do sistema (R$)", max_digits=10, decimal_places=2,
                                        default=Decimal("0"),
                                        help_text="Descontado antes da divisão. Varia conforme os módulos.")
    percentual_titular = models.DecimalField(
        "Sua parte (%) sobre o líquido", max_digits=5, decimal_places=2, default=Decimal("50"),
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    dia_vencimento = models.PositiveSmallIntegerField(
        "Dia de vencimento", validators=[MinValueValidator(1), MaxValueValidator(31)])
    banco = models.CharField("Banco emissor", max_length=60, blank=True)

    ativo = models.BooleanField("Ativo", default=True)
    envio_automatico = models.BooleanField("Envio automático pelo WhatsApp", default=True)
    situacao_licenca = models.CharField("Situação no portal", max_length=10, choices=LIC_CHOICES,
                                        default=LIC_LIBERADO)
    observacoes = models.TextField("Observações", blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def documento_numeros(self):
        return "".join(c for c in self.documento if c.isdigit())

    @property
    def whatsapp_e164(self):
        """Converte para o formato exigido pela API: 55 + DDD + número."""
        num = "".join(c for c in (self.whatsapp or self.telefone) if c.isdigit())
        if not num:
            return ""
        if not num.startswith("55") or len(num) <= 11:
            num = "55" + num.lstrip("0")
        return num

    @property
    def primeiro_nome(self):
        base = self.nome_contato or self.nome
        return base.split()[0] if base else ""

    def calcular_partes(self, valor=None):
        return calcular_partes(valor if valor is not None else self.mensalidade,
                               self.custo_sistema, self.percentual_titular)


def calcular_partes(valor, custo, percentual):
    """Sua parte = (valor − custo) × percentual. O restante (incluindo o custo) fica com o sócio."""
    valor, custo, percentual = Decimal(valor), Decimal(custo), Decimal(percentual)
    liquido = max(valor - custo, Decimal("0"))
    parte_titular = q2(liquido * percentual / Decimal("100"))
    parte_socio = q2(valor - parte_titular)
    return parte_titular, parte_socio


class HistoricoValorCliente(models.Model):
    """Registro de cada alteração de valores (auditoria de mensalidade/custo/percentual)."""
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="historico_valores")
    mensalidade = models.DecimalField(max_digits=10, decimal_places=2)
    custo_sistema = models.DecimalField(max_digits=10, decimal_places=2)
    percentual_titular = models.DecimalField(max_digits=5, decimal_places=2)
    alterado_em = models.DateTimeField(auto_now_add=True)
    alterado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-alterado_em"]


# --------------------------------------------------------------------------------------
# Cobranças (um boleto = uma cobrança mensal)
# --------------------------------------------------------------------------------------
def caminho_boleto(instance, filename):
    return f"boletos/{instance.cliente_id}/{uuid.uuid4().hex}.pdf"


class Cobranca(models.Model):
    AGENDADA = "agendada"
    ENVIADA = "enviada"
    VENCIDA = "vencida"
    INFORMADO = "informado"
    PAGA = "paga"
    CANCELADA = "cancelada"
    STATUS_CHOICES = [
        (AGENDADA, "Agendada"),
        (ENVIADA, "Boleto enviado"),
        (VENCIDA, "Vencida"),
        (INFORMADO, "Pagamento informado"),
        (PAGA, "Paga (confirmada)"),
        (CANCELADA, "Cancelada"),
    ]
    STATUS_ABERTOS = [AGENDADA, ENVIADA, VENCIDA, INFORMADO]

    FONTE_SOCIO_AVISOU = "socio_avisou"
    FONTE_SOCIO_LINK = "socio_link"
    FONTE_EXTRATO = "extrato"
    FONTE_CHOICES = [
        (FONTE_SOCIO_AVISOU, "Sócio confirmou que compensou"),
        (FONTE_SOCIO_LINK, "Sócio confirmou pelo link de conferência"),
        (FONTE_EXTRATO, "Conferido no extrato bancário"),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="cobrancas")
    competencia = models.DateField("Competência (mês de referência)", help_text="Sempre dia 1 do mês.")
    vencimento = models.DateField("Vencimento", db_index=True)
    valor = models.DecimalField("Valor do boleto (R$)", max_digits=10, decimal_places=2)

    # valores congelados
    custo_sistema = models.DecimalField(max_digits=10, decimal_places=2)
    percentual_titular = models.DecimalField(max_digits=5, decimal_places=2)
    parte_titular = models.DecimalField("Sua parte (R$)", max_digits=10, decimal_places=2)
    parte_socio = models.DecimalField("Parte do sócio (R$)", max_digits=10, decimal_places=2)

    linha_digitavel = models.CharField("Linha digitável", max_length=60, blank=True)
    pdf = models.FileField("PDF do boleto", upload_to=caminho_boleto, blank=True)
    pdf_nome_original = models.CharField(max_length=200, blank=True)
    leitura_alerta = models.CharField("Alertas da leitura do PDF", max_length=300, blank=True)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=AGENDADA, db_index=True)
    lembretes_pausados_ate = models.DateField(null=True, blank=True)

    # confirmação de pagamento
    pago_em = models.DateField("Data do pagamento", null=True, blank=True)
    valor_pago = models.DecimalField("Valor pago (R$)", max_digits=10, decimal_places=2, null=True, blank=True)
    confirmacao_fonte = models.CharField(max_length=20, choices=FONTE_CHOICES, blank=True)
    confirmacao_obs = models.CharField("Observação da confirmação", max_length=255, blank=True)
    confirmado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="+")
    confirmado_em = models.DateTimeField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["vencimento", "cliente__nome"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "competencia"], name="uma_cobranca_por_mes"),
        ]

    def __str__(self):
        return f"{self.cliente} — {self.competencia:%m/%Y}"

    @property
    def aberta(self):
        return self.status in self.STATUS_ABERTOS

    @property
    def dias_atraso(self):
        if not self.aberta:
            return 0
        d = (timezone.localdate() - self.vencimento).days
        return max(d, 0)

    def congelar_valores(self):
        cfg_cliente = self.cliente
        self.custo_sistema = cfg_cliente.custo_sistema
        self.percentual_titular = cfg_cliente.percentual_titular
        self.recalcular_partes()

    def recalcular_partes(self, base=None):
        valor = base if base is not None else self.valor
        self.parte_titular, self.parte_socio = calcular_partes(valor, self.custo_sistema, self.percentual_titular)


def caminho_comprovante(instance, filename):
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "bin").lower()[:5]
    return f"comprovantes/{uuid.uuid4().hex}.{ext}"


class Comprovante(models.Model):
    """Comprovante enviado pelo cliente. É evidência, NÃO confirmação de pagamento."""
    ORIGEM_WHATSAPP = "whatsapp"
    ORIGEM_MANUAL = "manual"
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="comprovantes", null=True)
    cobranca = models.ForeignKey(Cobranca, on_delete=models.SET_NULL, related_name="comprovantes",
                                 null=True, blank=True)
    arquivo = models.FileField(upload_to=caminho_comprovante)
    mime = models.CharField(max_length=80, blank=True)
    origem = models.CharField(max_length=10, choices=[(ORIGEM_WHATSAPP, "WhatsApp"), (ORIGEM_MANUAL, "Manual")])
    telefone_origem = models.CharField(max_length=20, blank=True)
    recebido_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-recebido_em"]


# --------------------------------------------------------------------------------------
# Mensagens WhatsApp (histórico)
# --------------------------------------------------------------------------------------
class Mensagem(models.Model):
    TIPO_BOLETO = "boleto"
    TIPO_ATRASO = "atraso"
    TIPO_MANUAL = "manual"
    TIPO_RECEBIDA = "recebida"
    TIPO_CHOICES = [
        (TIPO_BOLETO, "Envio do boleto"),
        (TIPO_ATRASO, "Lembrete de atraso"),
        (TIPO_MANUAL, "Envio manual"),
        (TIPO_RECEBIDA, "Recebida do cliente"),
    ]
    ST_PENDENTE = "pendente"
    ST_SIMULADA = "simulada"
    ST_ENVIADA = "enviada"
    ST_ENTREGUE = "entregue"
    ST_LIDA = "lida"
    ST_FALHA = "falha"
    ST_RECEBIDA = "recebida"
    STATUS_CHOICES = [
        (ST_PENDENTE, "Pendente"), (ST_SIMULADA, "Simulada (sem envio real)"),
        (ST_ENVIADA, "Enviada"), (ST_ENTREGUE, "Entregue"), (ST_LIDA, "Lida"),
        (ST_FALHA, "Falha"), (ST_RECEBIDA, "Recebida"),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name="mensagens")
    cobranca = models.ForeignKey(Cobranca, on_delete=models.SET_NULL, null=True, blank=True, related_name="mensagens")
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    automatica = models.BooleanField(default=False)
    telefone = models.CharField(max_length=20)
    conteudo = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ST_PENDENTE)
    wa_message_id = models.CharField(max_length=128, blank=True, db_index=True)
    erro = models.TextField(blank=True)
    enviada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]


# --------------------------------------------------------------------------------------
# Conta corrente com o sócio
# --------------------------------------------------------------------------------------
class LancamentoSocio(models.Model):
    CREDITO = "credito"   # sua parte de uma mensalidade paga (sócio passa a te dever)
    PIX = "pix"           # PIX recebido do sócio (abate o saldo)
    AJUSTE = "ajuste"     # ajuste manual (+ aumenta o que ele deve, − diminui)
    TIPO_CHOICES = [(CREDITO, "Sua parte de mensalidade"), (PIX, "PIX recebido"), (AJUSTE, "Ajuste")]

    data = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    valor = models.DecimalField(max_digits=10, decimal_places=2,
                                help_text="Sempre positivo para crédito e PIX. Ajuste pode ser negativo.")
    cobranca = models.OneToOneField(Cobranca, null=True, blank=True, on_delete=models.CASCADE,
                                    related_name="lancamento_socio")
    descricao = models.CharField(max_length=255, blank=True)
    comprovante = models.FileField(upload_to="pix/", blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data", "id"]

    @property
    def efeito(self):
        """Efeito no saldo a receber: positivo aumenta, negativo diminui."""
        return -self.valor if self.tipo == self.PIX else self.valor


# --------------------------------------------------------------------------------------
# Tarefas e notificações
# --------------------------------------------------------------------------------------
class Tarefa(models.Model):
    LIBERAR = "liberar_portal"
    BLOQUEAR = "bloquear_portal"
    COMPROVANTE = "conferir_comprovante"
    BOLETO_FALTANDO = "boleto_faltando"
    FALHA_ENVIO = "falha_envio"
    REVISAR_PDF = "revisar_pdf"
    GERAL = "geral"
    TIPO_CHOICES = [
        (LIBERAR, "Liberar cliente no portal"),
        (BLOQUEAR, "Avaliar bloqueio no portal"),
        (COMPROVANTE, "Conferir comprovante com o sócio"),
        (BOLETO_FALTANDO, "Boleto não cadastrado"),
        (FALHA_ENVIO, "Falha no envio do WhatsApp"),
        (REVISAR_PDF, "Revisar leitura do PDF"),
        (GERAL, "Geral"),
    ]
    tipo = models.CharField(max_length=24, choices=TIPO_CHOICES, default=GERAL)
    titulo = models.CharField(max_length=200)
    cliente = models.ForeignKey(Cliente, null=True, blank=True, on_delete=models.CASCADE, related_name="tarefas")
    cobranca = models.ForeignKey(Cobranca, null=True, blank=True, on_delete=models.CASCADE, related_name="tarefas")
    prazo = models.DateField(null=True, blank=True)
    concluida = models.BooleanField(default=False, db_index=True)
    concluida_em = models.DateTimeField(null=True, blank=True)
    concluida_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["concluida", "prazo", "-criado_em"]

    def __str__(self):
        return self.titulo

    @classmethod
    def criar_unica(cls, tipo, titulo, cliente=None, cobranca=None, prazo=None):
        """Não duplica tarefa aberta do mesmo tipo para a mesma cobrança/cliente."""
        existente = cls.objects.filter(tipo=tipo, cliente=cliente, cobranca=cobranca, concluida=False).first()
        if existente:
            return existente
        return cls.objects.create(tipo=tipo, titulo=titulo, cliente=cliente, cobranca=cobranca, prazo=prazo)


# --------------------------------------------------------------------------------------
# Segurança e auditoria
# --------------------------------------------------------------------------------------
class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    totp_secret = models.CharField(max_length=64, blank=True)
    totp_ativo = models.BooleanField(default=False)

    @classmethod
    def de(cls, user):
        obj, _ = cls.objects.get_or_create(usuario=user)
        return obj


class TentativaLogin(models.Model):
    usuario = models.CharField(max_length=150, db_index=True)
    ip = models.GenericIPAddressField(null=True)
    sucesso = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)


class RegistroAuditoria(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    acao = models.CharField(max_length=60, db_index=True)
    objeto = models.CharField(max_length=120, blank=True)
    detalhes = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Registro de auditoria"
        verbose_name_plural = "Auditoria"
