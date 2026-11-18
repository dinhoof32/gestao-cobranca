"""
Leitura automática de PDFs de boletos (inclusive carnês anuais com vários boletos).

Para cada página do PDF:
  1. extrai o texto;
  2. encontra todas as linhas digitáveis VÁLIDAS (dígitos verificadores conferidos);
  3. se a página tiver 1 boleto, ela vira um PDF individual;
     se tiver N boletos (carnê com várias lâminas por folha), a página é recortada em N faixas
     horizontais, na ordem em que as linhas aparecem (de cima para baixo), e o recorte é sinalizado
     para conferência visual.
  4. coleta CPFs/CNPJs presentes no texto para identificar o cliente.

Valor e vencimento vêm da própria linha digitável (fonte mais confiável que o texto do PDF).
PDF escaneado (imagem, sem texto) não é lido: o boleto fica para cadastro manual.
"""
import io
import re
from copy import deepcopy
from dataclasses import dataclass, field

from pypdf import PdfReader, PdfWriter

from .linha_digitavel import LinhaDigitavel, LinhaInvalida, decodificar

# 5 grupos da linha digitável com separadores flexíveis (pontos, espaços, quebras)
SEP = r"[\s.\-]{0,3}"
RE_LINHA = re.compile(
    r"(?<!\d)(\d{5}" + SEP + r"\d{5}" + SEP + r"\d{5}" + SEP + r"\d{6}" + SEP + r"\d{5}" + SEP
    + r"\d{6}" + SEP + r"\d" + SEP + r"\d{14})(?!\d)"
)
RE_CNPJ = re.compile(r"(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)")
RE_CPF = re.compile(r"(?<!\d)(\d{3}\.\d{3}\.\d{3}-\d{2})(?!\d)")


@dataclass
class BoletoLido:
    linha: LinhaDigitavel | None
    pdf_bytes: bytes
    pagina: int
    documentos: set = field(default_factory=set)
    alertas: list = field(default_factory=list)


def _documentos(texto):
    docs = set()
    for m in RE_CNPJ.findall(texto):
        docs.add(re.sub(r"\D", "", m))
    for m in RE_CPF.findall(texto):
        docs.add(re.sub(r"\D", "", m))
    return docs


def _linhas_com_posicao(pagina):
    """Retorna [(linha_decodificada, y)] na ordem de cima para baixo."""
    trechos = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text and text.strip():
            trechos.append((text, tm[5]))

    texto = pagina.extract_text(visitor_text=visitor) or ""
    encontrados = {}
    # 1) por trecho (posição conhecida)
    for t, y in trechos:
        for m in RE_LINHA.findall(t):
            try:
                ld = decodificar(m)
            except LinhaInvalida:
                continue
            encontrados.setdefault(ld.numeros, (ld, y))
    # 2) texto completo (linhas quebradas em vários trechos)
    for m in RE_LINHA.findall(texto):
        try:
            ld = decodificar(m)
        except LinhaInvalida:
            continue
        encontrados.setdefault(ld.numeros, (ld, None))
    itens = list(encontrados.values())
    # ordena de cima (y maior) para baixo; sem posição vão ao final mantendo a ordem do texto
    itens.sort(key=lambda it: -(it[1] if it[1] is not None else -1e9))
    return itens, texto


def _pagina_para_pdf(reader, indice, faixa=None, total_faixas=1):
    writer = PdfWriter()
    pagina = deepcopy(reader.pages[indice]) if faixa is not None else reader.pages[indice]
    if faixa is not None:
        box = pagina.mediabox
        x0, y0, x1, y1 = float(box.left), float(box.bottom), float(box.right), float(box.top)
        altura = (y1 - y0) / total_faixas
        topo = y1 - faixa * altura
        pagina.mediabox.lower_left = (x0, topo - altura)
        pagina.mediabox.upper_right = (x1, topo)
        pagina.cropbox.lower_left = (x0, topo - altura)
        pagina.cropbox.upper_right = (x1, topo)
    writer.add_page(pagina)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def ler_pdf(conteudo: bytes) -> list[BoletoLido]:
    reader = PdfReader(io.BytesIO(conteudo))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("PDF protegido por senha.") from exc

    resultado = []
    for i, pagina in enumerate(reader.pages):
        itens, texto = _linhas_com_posicao(pagina)
        docs = _documentos(texto)
        if not itens:
            if texto.strip() and len(reader.pages) > 1:
                continue  # página sem boleto (capa/instruções de carnê)
            resultado.append(BoletoLido(
                linha=None, pdf_bytes=_pagina_para_pdf(reader, i), pagina=i + 1, documentos=docs,
                alertas=["Nenhuma linha digitável válida encontrada (PDF escaneado ou formato não reconhecido)."]))
            continue
        if len(itens) == 1:
            resultado.append(BoletoLido(linha=itens[0][0], pdf_bytes=_pagina_para_pdf(reader, i),
                                        pagina=i + 1, documentos=docs))
            continue
        for k, (ld, _y) in enumerate(itens):
            resultado.append(BoletoLido(
                linha=ld, pdf_bytes=_pagina_para_pdf(reader, i, faixa=k, total_faixas=len(itens)),
                pagina=i + 1, documentos=docs,
                alertas=[f"Página {i + 1} tinha {len(itens)} boletos e foi recortada automaticamente. "
                         "Confira o PDF antes do envio."]))
    return resultado
