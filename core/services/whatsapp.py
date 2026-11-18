"""
Cliente da WhatsApp Business Platform — Cloud API OFICIAL da Meta.

- Mensagens iniciadas pela empresa usam TEMPLATES aprovados (exigência da Meta).
  O PDF do boleto vai como documento no cabeçalho do template.
- Respostas livres (texto) só são permitidas até 24h após a última mensagem do cliente.
- Webhook: a Meta envia status (entregue/lida/falha) e mensagens recebidas (ex.: comprovantes).
  Toda requisição do webhook tem a assinatura X-Hub-Signature-256 conferida com o App Secret.

Sem credenciais configuradas, opera em MODO SIMULAÇÃO: nada sai, tudo é registrado.
"""
import hashlib
import hmac
import logging
import mimetypes
import uuid

import requests
from django.conf import settings

log = logging.getLogger(__name__)
TIMEOUT = 30


class ErroWhatsApp(Exception):
    pass


def _cfg():
    return settings.WHATSAPP


def simulacao():
    c = _cfg()
    return c["SIMULACAO"] or not (c["TOKEN"] and c["PHONE_NUMBER_ID"])


def _base():
    c = _cfg()
    return f"https://graph.facebook.com/{c['API_VERSION']}"


def _headers():
    return {"Authorization": f"Bearer {_cfg()['TOKEN']}"}


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


def enviar_midia(conteudo: bytes, nome_arquivo: str, mime: str = "application/pdf") -> str:
    """Sobe o arquivo para a Meta e retorna o media_id (válido por 30 dias)."""
    if simulacao():
        return f"sim-media-{uuid.uuid4().hex[:12]}"
    url = f"{_base()}/{_cfg()['PHONE_NUMBER_ID']}/media"
    resp = requests.post(
        url, headers=_headers(), timeout=TIMEOUT,
        data={"messaging_product": "whatsapp", "type": mime},
        files={"file": (nome_arquivo, conteudo, mime)},
    )
    return _checar(resp)["id"]


def enviar_template(telefone: str, template: str, idioma: str, parametros_corpo: list[str],
                    documento_id: str | None = None, nome_documento: str = "boleto.pdf") -> str:
    componentes = []
    if documento_id:
        componentes.append({
            "type": "header",
            "parameters": [{"type": "document", "document": {"id": documento_id, "filename": nome_documento}}],
        })
    if parametros_corpo:
        componentes.append({
            "type": "body",
            "parameters": [{"type": "text", "text": (str(p) or "-")[:1000]} for p in parametros_corpo],
        })
    payload = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "template",
        "template": {"name": template, "language": {"code": idioma}, "components": componentes},
    }
    if simulacao():
        log.info("[SIMULAÇÃO] template %s para %s: %s", template, telefone, parametros_corpo)
        return f"sim-{uuid.uuid4().hex}"
    resp = requests.post(f"{_base()}/{_cfg()['PHONE_NUMBER_ID']}/messages",
                         headers=_headers(), json=payload, timeout=TIMEOUT)
    return _checar(resp)["messages"][0]["id"]


def enviar_texto(telefone: str, texto: str) -> str:
    if simulacao():
        log.info("[SIMULAÇÃO] texto para %s: %s", telefone, texto)
        return f"sim-{uuid.uuid4().hex}"
    payload = {"messaging_product": "whatsapp", "to": telefone, "type": "text",
               "text": {"preview_url": False, "body": texto[:4096]}}
    resp = requests.post(f"{_base()}/{_cfg()['PHONE_NUMBER_ID']}/messages",
                         headers=_headers(), json=payload, timeout=TIMEOUT)
    return _checar(resp)["messages"][0]["id"]


def baixar_midia(media_id: str) -> tuple[bytes, str]:
    """Baixa uma mídia recebida (ex.: comprovante). Retorna (conteúdo, mime)."""
    info = _checar(requests.get(f"{_base()}/{media_id}", headers=_headers(), timeout=TIMEOUT))
    resp = requests.get(info["url"], headers=_headers(), timeout=60)
    if resp.status_code >= 400:
        raise ErroWhatsApp(f"Falha ao baixar mídia: HTTP {resp.status_code}")
    return resp.content, info.get("mime_type", "application/octet-stream")


def assinatura_valida(corpo: bytes, cabecalho: str | None) -> bool:
    segredo = _cfg()["APP_SECRET"]
    if not segredo:
        # sem App Secret configurado só aceitamos em modo simulação (ambiente local)
        return simulacao()
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.split("=", 1)[1])


def extensao_de(mime: str) -> str:
    return (mimetypes.guess_extension(mime or "") or ".bin").lstrip(".")
