"""
Validação e decodificação da linha digitável de boletos bancários (padrão FEBRABAN, 47 dígitos).

Estrutura da linha digitável:
  Campo 1: AAABC.CCCCX   (banco, moeda, 5 posições do campo livre, DV)
  Campo 2: DDDDD.DDDDDY  (10 posições do campo livre, DV)
  Campo 3: EEEEE.EEEEEZ  (10 posições do campo livre, DV)
  Campo 4: K             (DV geral do código de barras)
  Campo 5: UUUUVVVVVVVVVV (fator de vencimento + valor em centavos)

Fator de vencimento: dias desde 07/10/1997. Ao atingir 9999 (21/02/2025) o fator reiniciou em 1000
(22/02/2025). Por isso o mesmo fator pode significar duas datas; escolhemos a mais próxima da referência.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

BASE_ANTIGA = date(1997, 10, 7)
BASE_NOVA = date(2025, 2, 22)  # fator 1000


@dataclass
class LinhaDigitavel:
    numeros: str
    banco: str
    valor: Decimal | None
    vencimento: date | None
    codigo_barras: str

    @property
    def formatada(self):
        n = self.numeros
        return f"{n[0:5]}.{n[5:10]} {n[10:15]}.{n[15:21]} {n[21:26]}.{n[26:32]} {n[32]} {n[33:47]}"


class LinhaInvalida(ValueError):
    pass


def somente_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def mod10(numero: str) -> int:
    soma = 0
    peso = 2
    for d in reversed(numero):
        p = int(d) * peso
        soma += p // 10 + p % 10
        peso = 1 if peso == 2 else 2
    return (10 - soma % 10) % 10


def mod11_boleto(numero: str) -> int:
    soma = 0
    peso = 2
    for d in reversed(numero):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    dv = 11 - (soma % 11)
    return 1 if dv in (0, 10, 11) else dv


def vencimento_do_fator(fator: int, referencia: date | None = None) -> date | None:
    if fator == 0:
        return None
    referencia = referencia or date.today()
    candidatos = [BASE_ANTIGA + timedelta(days=fator)]
    if fator >= 1000:
        candidatos.append(BASE_NOVA + timedelta(days=fator - 1000))
    return min(candidatos, key=lambda d: abs((d - referencia).days))


def decodificar(linha: str, referencia: date | None = None) -> LinhaDigitavel:
    n = somente_digitos(linha)
    if len(n) == 48 and n.startswith("8"):
        raise LinhaInvalida("Boleto de arrecadação/convênio (48 dígitos) não suportado.")
    if len(n) != 47:
        raise LinhaInvalida(f"A linha digitável deve ter 47 dígitos (encontrados {len(n)}).")

    campos = [(n[0:9], n[9]), (n[10:20], n[20]), (n[21:31], n[31])]
    for i, (corpo, dv) in enumerate(campos, start=1):
        if mod10(corpo) != int(dv):
            raise LinhaInvalida(f"Dígito verificador do campo {i} não confere.")

    campo_livre = n[4:9] + n[10:20] + n[21:31]
    codigo = n[0:4] + n[32] + n[33:47] + campo_livre
    if mod11_boleto(codigo[:4] + codigo[5:]) != int(codigo[4]):
        raise LinhaInvalida("Dígito verificador geral não confere.")

    fator = int(n[33:37])
    valor_centavos = int(n[37:47])
    return LinhaDigitavel(
        numeros=n,
        banco=n[0:3],
        valor=(Decimal(valor_centavos) / 100) if valor_centavos else None,
        vencimento=vencimento_do_fator(fator, referencia),
        codigo_barras=codigo,
    )


def eh_valida(linha: str) -> bool:
    try:
        decodificar(linha)
        return True
    except LinhaInvalida:
        return False


def fator_de(vencimento: date) -> int:
    """Inverso de vencimento_do_fator (usado em testes)."""
    if vencimento >= BASE_NOVA:
        return 1000 + (vencimento - BASE_NOVA).days
    return (vencimento - BASE_ANTIGA).days


def montar_linha(banco: str, campo_livre: str, vencimento: date, valor: Decimal, moeda: str = "9") -> str:
    """Gera uma linha digitável válida (útil para testes e para o modo demonstração)."""
    assert len(banco) == 3 and len(campo_livre) == 25
    fator = f"{fator_de(vencimento):04d}"
    valor_s = f"{int(Decimal(valor) * 100):010d}"
    sem_dv = banco + moeda + fator + valor_s + campo_livre
    dv = mod11_boleto(sem_dv)
    codigo = sem_dv[:4] + str(dv) + sem_dv[4:]
    c1 = codigo[0:4] + codigo[19:24]
    c2 = codigo[24:34]
    c3 = codigo[34:44]
    return (c1 + str(mod10(c1)) + c2 + str(mod10(c2)) + c3 + str(mod10(c3))
            + codigo[4] + codigo[5:19])
