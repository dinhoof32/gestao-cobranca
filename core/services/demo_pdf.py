"""Gera PDFs de boleto FICTÍCIOS para demonstração e testes (requer reportlab)."""
import io

from .linha_digitavel import decodificar

BANCOS = {"341": "Itaú Unibanco S.A.", "237": "Banco Bradesco S.A.", "756": "Sicoob", "077": "Banco Inter S.A.",
          "001": "Banco do Brasil S.A."}


def _desenhar(c, x, y, w, h, linha, pagador, documento, beneficiario):
    ld = decodificar(linha)
    c.setLineWidth(0.8)
    c.rect(x, y, w, h)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x + 8, y + h - 20, f"{BANCOS.get(ld.banco, 'Banco')} | {ld.banco}-X")
    c.setFont("Helvetica-Bold", 10.5)
    c.drawRightString(x + w - 8, y + h - 20, ld.formatada)
    c.line(x, y + h - 28, x + w, y + h - 28)
    c.setFont("Helvetica", 8)
    linhas = [
        ("Beneficiário", beneficiario),
        ("Pagador", f"{pagador} — CNPJ/CPF {documento}"),
        ("Vencimento", ld.vencimento.strftime("%d/%m/%Y") if ld.vencimento else "-"),
        ("Valor do documento", f"R$ {ld.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")),
        ("Instruções", "Após o vencimento cobrar multa de 2% e juros de 1% ao mês."),
    ]
    yy = y + h - 44
    for rot, val in linhas:
        c.setFillGray(0.4)
        c.drawString(x + 8, yy, rot)
        c.setFillGray(0)
        c.drawString(x + 110, yy, str(val))
        yy -= 14
    c.setFont("Helvetica", 6.5)
    c.setFillGray(0.5)
    c.drawString(x + 8, y + 8, "DOCUMENTO FICTÍCIO GERADO PARA DEMONSTRAÇÃO — NÃO PAGUE")
    c.setFillGray(0)
    # "código de barras" ilustrativo
    bx = x + w - 230
    for i, d in enumerate(ld.codigo_barras):
        c.rect(bx + i * 5, y + 10, 1 + int(d) % 3, 30, stroke=0, fill=1)


def gerar(boletos, pagador, documento, beneficiario="Empresa do Sócio LTDA", por_pagina=1) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margem = 30
    altura = (h - 2 * margem) / por_pagina
    for i, linha in enumerate(boletos):
        pos = i % por_pagina
        if i and pos == 0:
            c.showPage()
        topo = h - margem - pos * altura
        _desenhar(c, margem, topo - altura + 12, w - 2 * margem, min(altura - 24, 190), linha, pagador,
                  documento, beneficiario)
    c.showPage()
    c.save()
    return buf.getvalue()
