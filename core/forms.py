from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Cliente, Cobranca, Configuracao, LancamentoSocio, Tarefa


class EstiloMixin:
    """Aplica classes CSS padrão nos campos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "check")
            else:
                w.attrs.setdefault("class", "input")


class DataInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kw):
        super().__init__(format="%Y-%m-%d", **kw)


class ClienteForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["nome", "nome_contato", "documento", "whatsapp", "telefone", "email", "mensalidade",
                  "custo_sistema", "percentual_titular", "dia_vencimento", "banco", "ativo",
                  "envio_automatico", "situacao_licenca", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        dados = super().clean()
        m, c = dados.get("mensalidade"), dados.get("custo_sistema")
        if m is not None and c is not None and c > m:
            self.add_error("custo_sistema", "O custo do sistema não pode ser maior que a mensalidade.")
        if dados.get("envio_automatico") and not (dados.get("whatsapp") or dados.get("telefone")):
            self.add_error("whatsapp", "Informe o WhatsApp para usar o envio automático.")
        return dados


class CobrancaForm(EstiloMixin, forms.ModelForm):
    pdf_upload = forms.FileField(label="PDF do boleto", required=False,
                                 widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"}))

    class Meta:
        model = Cobranca
        fields = ["cliente", "competencia", "vencimento", "valor", "linha_digitavel"]
        widgets = {"competencia": DataInput(), "vencimento": DataInput()}
        help_texts = {"competencia": "Qualquer dia do mês de referência."}

    def clean_competencia(self):
        return self.cleaned_data["competencia"].replace(day=1)

    def clean_linha_digitavel(self):
        from .services.linha_digitavel import LinhaInvalida, decodificar
        linha = self.cleaned_data.get("linha_digitavel", "").strip()
        if not linha:
            return ""
        try:
            return decodificar(linha).formatada
        except LinhaInvalida as exc:
            raise forms.ValidationError(f"Linha digitável inválida: {exc}") from exc

    def clean_pdf_upload(self):
        f = self.cleaned_data.get("pdf_upload")
        if f and not f.read(5).startswith(b"%PDF"):
            raise forms.ValidationError("O arquivo enviado não é um PDF.")
        if f:
            f.seek(0)
        return f


class MultiplosArquivos(forms.ClearableFileInput):
    allow_multiple_selected = True


class CampoMultiplosArquivos(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultiplosArquivos(attrs={"accept": "application/pdf", "multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(CampoMultiplosArquivos, self).clean(d, initial) for d in data]
        return [super().clean(data, initial)]


class ImportarBoletosForm(EstiloMixin, forms.Form):
    arquivos = CampoMultiplosArquivos(label="PDFs dos boletos / carnês")
    cliente = forms.ModelChoiceField(
        Cliente.objects.filter(ativo=True), required=False, label="Cliente",
        empty_label="Identificar automaticamente pelo CPF/CNPJ")
    substituir = forms.BooleanField(required=False, label="Substituir boletos já cadastrados (meses em aberto)")


class ConfirmarPagamentoForm(EstiloMixin, forms.Form):
    fonte = forms.ChoiceField(choices=Cobranca.FONTE_CHOICES, label="Como foi confirmado?")
    pago_em = forms.DateField(label="Data do pagamento", widget=DataInput(), initial=timezone.localdate)
    valor_pago = forms.DecimalField(label="Valor pago (R$)", max_digits=10, decimal_places=2, required=False,
                                    min_value=Decimal("0.01"), help_text="Em branco = valor do boleto.")
    obs = forms.CharField(label="Observação", required=False, max_length=255)
    ciente = forms.BooleanField(
        label="Confirmo que o pagamento foi verificado na conta (não apenas informado pelo cliente).")

    def clean_pago_em(self):
        d = self.cleaned_data["pago_em"]
        if d > timezone.localdate():
            raise forms.ValidationError("A data do pagamento não pode estar no futuro.")
        return d


class ComprovanteForm(EstiloMixin, forms.Form):
    arquivo = forms.FileField(label="Comprovante (imagem ou PDF)")


class PixForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = LancamentoSocio
        fields = ["data", "valor", "descricao", "comprovante"]
        widgets = {"data": DataInput()}
        labels = {"valor": "Valor do PIX (R$)", "descricao": "Descrição (opcional)"}

    def clean_valor(self):
        v = self.cleaned_data["valor"]
        if v <= 0:
            raise forms.ValidationError("Informe um valor positivo.")
        return v


class AjusteForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = LancamentoSocio
        fields = ["data", "valor", "descricao"]
        widgets = {"data": DataInput()}
        labels = {"valor": "Valor (R$) — positivo aumenta o saldo a receber, negativo diminui",
                  "descricao": "Motivo do ajuste"}

    def clean_descricao(self):
        d = self.cleaned_data.get("descricao", "").strip()
        if not d:
            raise forms.ValidationError("Informe o motivo do ajuste.")
        return d


class TarefaForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Tarefa
        fields = ["titulo", "cliente", "prazo"]
        widgets = {"prazo": DataInput()}


class ConfiguracaoForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Configuracao
        exclude = ["socio_token"]

    def clean(self):
        d = super().clean()
        if d.get("hora_inicio_envio") is not None and d.get("hora_fim_envio") is not None \
                and d["hora_inicio_envio"] >= d["hora_fim_envio"]:
            self.add_error("hora_fim_envio", "O horário final deve ser maior que o inicial.")
        return d


class TextoWhatsAppForm(EstiloMixin, forms.Form):
    texto = forms.CharField(label="Mensagem", widget=forms.Textarea(attrs={"rows": 3}), max_length=4000)


class LoginForm(EstiloMixin, forms.Form):
    usuario = forms.CharField(label="Usuário", max_length=150)
    senha = forms.CharField(label="Senha", widget=forms.PasswordInput)


class CodigoForm(EstiloMixin, forms.Form):
    codigo = forms.CharField(label="Código do aplicativo autenticador", max_length=6, min_length=6,
                             widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))
