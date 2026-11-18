"""
Envio e recebimento de WhatsApp com dois provedores intercambiáveis:

- "qrcode"  (padrão): número conectado por QR Code através do gateway próprio (pasta /gateway).
                      Sem custo por mensagem. Usa o WhatsApp Web (não oficial): há risco de bloqueio
                      do número pela Meta; o gateway espaça os envios para reduzir esse risco.
- "oficial":          WhatsApp Business Platform (Cloud API da Meta), com templates aprovados.

Sem as credenciais do provedor escolhido, opera em MODO SIMULAÇÃO: registra tudo, não envia nada.
"""
import base64
import hashlib
import hmac
import logging
import mimetypes
import uuid

import requests
from django.conf import settings

log = logging.getLogger(__name__)
TIMEOUT = 30
TIMEOUT_GATEWAY = 300  # o gateway espaça os envios (fila), então pode demorar


class ErroWhatsApp(Exception):
    pass


class WhatsAppIndisponivel(ErroWhatsApp):
    """Gateway fora do ar ou número desconectado: não é falha da mensagem, é para tentar depois."""


def provedor():
    return "oficial" if settings.WHATSAPP_PROVEDOR == "oficial" else "qrcode"


def simulacao():
    if settings.WHATSAPP["SIMULACAO"]:
        return True
    if provedor() == "qrcode":
        return not (settings.GATEWAY["URL"] and settings.GATEWAY["TOKEN"])
    c = settings.WHATSAPP
    return not (c["TOKEN"] and c["PHONE_NUMBER_ID"])


# ======================================================================================
# API de alto nível (usada pelo restante do sistema)
# ======================================================================================
def enviar_cobranca(telefone: str, texto: str, pdf: bytes | None, nome_pdf: str,
                    template: str, idioma: str, parametros: list[str]) -> str:
    """Envia a mensagem de cobrança com o PDF do boleto anexado. Retorna o id da mensagem."""
    if simulacao():
        log.info("[SIMULAÇÃO] cobrança para %s: %s", telefone, texto[:120])
        return f"sim-{uuid.uuid4().hex}"
    if provedor() == "qrcode":
        doc = {"base64": base64.b64encode(pdf).decode(), "nome": nome_pdf, "mime": "application/pdf"} if pdf else None
        return _gateway_post("/enviar", {"telefone": telefone, "texto": texto, "documento": doc})["id"]
    media_id = _oficial_enviar_midia(pdf, nome_pdf) if pdf else None
    return _oficial_enviar_template(telefone, template, idioma, parametros, media_id, nome_pdf)


def enviar_texto(telefone: str, texto: str) -> str:
    if simulacao():
        log.info("[SIMULAÇÃO] texto para %s: %s", telefone, texto[:120])
        return f"sim-{uuid.uuid4().hex}"
    if provedor() == "qrcode":
        return _gateway_post("/enviar", {"telefone": telefone, "texto": texto})["id"]
    payload = {"messaging_product": "whatsapp", "to": telefone, "type": "text",
               "text": {"preview_url": False, "body": texto[:4096]}}
    resp = requests.post(f"{_base()}/{settings.WHATSAPP['PHONE_NUMBER_ID']}/messages",
                         headers=_headers(), json=payload, timeout=TIMEOUT)
    return _checar(resp)["messages"][0]["id"]


def exige_janela_24h() -> bool:
    """Na API oficial, texto livre só até 24h após a última mensagem do cliente."""
    return provedor() == "oficial" and not simulacao()


def status_conexao() -> dict:
    """Estado da conexão para exibir no sistema."""
    if simulacao():
        return {"conexao": "simulacao", "qr": None, "numero": None}
    if provedor() == "oficial":
        return {"conexao": "conectado", "qr": None, "numero": settings.WHATSAPP["PHONE_NUMBER_ID"]}
    try:
        r = requests.get(f"{settings.GATEWAY['URL']}/status", headers=_gw_headers(), timeout=4)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        return {"conexao": "gateway_fora", "qr": None, "numero": None, "erro": str(exc)[:200]}


def conectado() -> bool:
    return simulacao() or status_conexao().get("conexao") == "conectado"


def desconectar():
    if provedor() == "qrcode" and not simulacao():
        _gateway_post("/desconectar", {})


# ======================================================================================
# Gateway QR Code
# ======================================================================================
def _gw_headers():
    return {"Authorization": f"Bearer {settings.GATEWAY['TOKEN']}"}


def _gateway_post(caminho, dados):
    try:
        r = requests.post(f"{settings.GATEWAY['URL']}{caminho}", json=dados, headers=_gw_headers(),
                          timeout=TIMEOUT_GATEWAY)
    except requests.RequestException as exc:
        raise WhatsAppIndisponivel(f"Gateway do WhatsApp inacessível: {exc}") from exc
    try:
        corpo = r.json()
    except ValueError:
        corpo = {"erro": r.text[:300]}
    if r.status_code == 503:
        raise WhatsAppIndisponivel(corpo.get("erro", "WhatsApp desconectado"))
    if r.status_code >= 400:
        raise ErroWhatsApp(corpo.get("erro") or f"HTTP {r.status_code}")
    return corpo


def token_gateway_valido(recebido: str | None) -> bool:
    esperado = settings.GATEWAY["TOKEN"]
    return bool(esperado and recebido and hmac.compare_digest(esperado, recebido))


# ======================================================================================
# API oficial (Cloud API da Meta)
# ======================================================================================
def _base():
    return f"https://graph.facebook.com/{settings.WHATSAPP['API_VERSION']}"


def _headers():
    return {"Authorization": f"Bearer {settings.WHATSAPP['TOKEN']}"}


def _checar(resp):
    try:
        dados = resp.json()
    except ValueError:
        dados = {"raw": resp.text[:500]}
    if resp.status_code >= 400 or "error" in dados:
        err = dados.get("error", {}) if isinstance(dados, dict) else {}
        msg = err.get("message") or str(dados)[:500]
        detalhe = (err.get("error_data") or {}).get("details", "")
        raise ErroWhatsApp(f"[{resp.status_code}] {msg} {detalhe}".strip())
    return dados


def _oficial_enviar_midia(conteudo: bytes, nome_arquivo: str, mime: str = "application/pdf") -> str:
    url = f"{_base()}/{settings.WHATSAPP['PHONE_NUMBER_ID']}/media"
    resp = requests.post(url, headers=_headers(), timeout=TIMEOUT,
                         data={"messaging_product": "whatsapp", "type": mime},
                         files={"file": (nome_arquivo, conteudo, mime)})
    return _checar(resp)["id"]


def _oficial_enviar_template(telefone, template, idioma, parametros, documento_id, nome_documento):
    componentes = []
    if documento_id:
        componentes.append({"type": "header", "parameters": [
            {"type": "document", "document": {"id": documento_id, "filename": nome_documento}}]})
    if parametros:
        componentes.append({"type": "body", "parameters": [
            {"type": "text", "text": (str(p) or "-")[:1000]} for p in parametros]})
    payload = {"messaging_product": "whatsapp", "to": telefone, "type": "template",
               "template": {"name": template, "language": {"code": idioma}, "components": componentes}}
    resp = requests.post(f"{_base()}/{settings.WHATSAPP['PHONE_NUMBER_ID']}/messages",
                         headers=_headers(), json=payload, timeout=TIMEOUT)
    return _checar(resp)["messages"][0]["id"]


def baixar_midia(media_id: str) -> tuple[bytes, str]:
    """(API oficial) Baixa uma mídia recebida. Retorna (conteúdo, mime)."""
    info = _checar(requests.get(f"{_base()}/{media_id}", headers=_headers(), timeout=TIMEOUT))
    resp = requests.get(info["url"], headers=_headers(), timeout=60)
    if resp.status_code >= 400:
        raise ErroWhatsApp(f"Falha ao baixar mídia: HTTP {resp.status_code}")
    return resp.content, info.get("mime_type", "application/octet-stream")


def assinatura_valida(corpo: bytes, cabecalho: str | None) -> bool:
    segredo = settings.WHATSAPP["APP_SECRET"]
    if not segredo:
        return False
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.split("=", 1)[1])


def extensao_de(mime: str) -> str:
    return (mimetypes.guess_extension(mime or "") or ".bin").lstrip(".")
