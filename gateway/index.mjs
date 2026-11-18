/**
 * Gateway do WhatsApp por QR Code (WhatsApp Web, biblioteca Baileys).
 *
 * - Conecta um número escaneando o QR Code (exibido no sistema, em Configurações).
 * - A sessão fica salva em SESSAO_DIR: depois de conectado, não precisa escanear de novo.
 * - Expõe uma API HTTP interna (protegida por token) usada pelo sistema Django:
 *     GET  /status        -> estado da conexão + QR Code (data URL) quando aguardando leitura
 *     POST /enviar        -> { telefone, texto, documento?: { base64, nome, mime } }
 *     POST /desconectar   -> encerra a sessão (será preciso escanear novamente)
 * - Encaminha ao Django (WEBHOOK_URL) as mensagens recebidas (com mídia) e os status de entrega.
 *
 * PROTEÇÕES CONTRA BLOQUEIO: fila única de envio com intervalo aleatório entre mensagens,
 * simulação de "digitando", verificação se o número existe no WhatsApp antes de enviar.
 */
import http from 'node:http'
import fs from 'node:fs'
import { timingSafeEqual } from 'node:crypto'
import makeWASocket, {
  Browsers, DisconnectReason, downloadMediaMessage, fetchLatestBaileysVersion,
  isLidUser, jidNormalizedUser, useMultiFileAuthState,
} from 'baileys'
import pino from 'pino'
import QRCode from 'qrcode'

const PORTA = Number(process.env.PORTA || 3000)
const HOST = process.env.HOST || '0.0.0.0'
const TOKEN = process.env.GATEWAY_TOKEN || ''
const WEBHOOK_URL = process.env.WEBHOOK_URL || 'http://localhost:8000/webhook/gateway/'
const SESSAO_DIR = process.env.SESSAO_DIR || './sessao'
const INTERVALO_MIN = Number(process.env.INTERVALO_MIN_SEG || 20) * 1000
const INTERVALO_MAX = Number(process.env.INTERVALO_MAX_SEG || 45) * 1000

if (!TOKEN || TOKEN.length < 16) {
  console.error('Defina GATEWAY_TOKEN (mínimo 16 caracteres).')
  process.exit(1)
}

const log = pino({ level: process.env.LOG_LEVEL || 'warn' })
const estado = { conexao: 'iniciando', qr: null, numero: null, desde: new Date().toISOString(), erro: null }
let sock = null
let reconectando = false

// ------------------------------------------------------------------ conexão
async function conectar () {
  const { state, saveCreds } = await useMultiFileAuthState(SESSAO_DIR)
  let version
  try { ({ version } = await fetchLatestBaileysVersion()) } catch { version = undefined }
  sock = makeWASocket({
    auth: state,
    version,
    logger: log,
    browser: Browsers.ubuntu('Gestao Cobranca'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
  })
  estado.conexao = 'conectando'

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (u) => {
    if (u.qr) {
      estado.qr = await QRCode.toDataURL(u.qr, { margin: 1, width: 300 })
      estado.conexao = 'aguardando_qr'
    }
    if (u.connection === 'open') {
      estado.conexao = 'conectado'
      estado.qr = null
      estado.erro = null
      estado.numero = (sock.user?.id || '').split(':')[0].split('@')[0]
      estado.desde = new Date().toISOString()
      console.log(`WhatsApp conectado: ${estado.numero}`)
    }
    if (u.connection === 'close') {
      const codigo = u.lastDisconnect?.error?.output?.statusCode
      estado.conexao = 'desconectado'
      estado.erro = u.lastDisconnect?.error?.message || null
      if (codigo === DisconnectReason.loggedOut) {
        console.log('Sessão encerrada no celular. Limpando sessão; será exibido novo QR Code.')
        fs.rmSync(SESSAO_DIR, { recursive: true, force: true })
      }
      agendarReconexao(codigo === DisconnectReason.loggedOut ? 1000 : 5000)
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return
    for (const m of messages) {
      try { await tratarRecebida(m) } catch (e) { console.error('Erro ao tratar mensagem:', e.message) }
    }
  })

  sock.ev.on('messages.update', async (updates) => {
    for (const { key, update } of updates) {
      if (!key.fromMe || update.status == null) continue
      const mapa = { 2: 'enviada', 3: 'entregue', 4: 'lida', 5: 'lida' }
      const status = update.status === 0 ? 'falha' : mapa[update.status]
      if (status) await notificar({ tipo: 'status', id: key.id, status })
    }
  })
}

function agendarReconexao (ms) {
  if (reconectando) return
  reconectando = true
  setTimeout(() => { reconectando = false; conectar().catch(e => { console.error(e); agendarReconexao(10000) }) }, ms)
}

async function telefoneDe (key) {
  // WhatsApp moderno pode identificar contatos por LID; tentamos obter o número real.
  const candidatos = [key.remoteJidAlt, key.senderPn, key.remoteJid].filter(Boolean)
  for (const jid of candidatos) {
    if (jid.endsWith('@s.whatsapp.net')) return jid.split('@')[0].split(':')[0]
  }
  if (isLidUser(key.remoteJid)) {
    try {
      const pn = await sock.signalRepository?.lidMapping?.getPNForLID(key.remoteJid)
      if (pn) return pn.split('@')[0].split(':')[0]
    } catch { /* sem mapeamento */ }
  }
  return ''
}

async function tratarRecebida (m) {
  if (!m.message || m.key.fromMe) return
  const jid = m.key.remoteJid || ''
  if (jid.endsWith('@g.us') || jid === 'status@broadcast' || jid.endsWith('@newsletter')) return

  const msg = m.message.ephemeralMessage?.message || m.message.viewOnceMessageV2?.message || m.message
  const evento = { tipo: 'mensagem', id: m.key.id, de: await telefoneDe(m.key), nome: m.pushName || '', texto: '' }
  if (msg.conversation || msg.extendedTextMessage) {
    evento.texto = msg.conversation || msg.extendedTextMessage.text || ''
  } else if (msg.imageMessage || msg.documentMessage || msg.documentWithCaptionMessage) {
    const doc = msg.documentMessage || msg.documentWithCaptionMessage?.message?.documentMessage
    const img = msg.imageMessage
    evento.texto = (img?.caption || doc?.caption || '')
    try {
      const buffer = await downloadMediaMessage(m, 'buffer', {}, { logger: log, reuploadRequest: sock.updateMediaMessage })
      evento.midia = {
        base64: buffer.toString('base64'),
        mime: (img?.mimetype || doc?.mimetype || 'application/octet-stream'),
        nome: doc?.fileName || (img ? 'comprovante.jpg' : 'arquivo'),
        tipo: img ? 'image' : 'document',
      }
    } catch (e) {
      evento.erro_midia = e.message
    }
  } else {
    evento.texto = `[${Object.keys(msg)[0] || 'mensagem'}]`
  }
  await notificar(evento)
}

async function notificar (dados) {
  try {
    const r = await fetch(WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gateway-Token': TOKEN },
      body: JSON.stringify(dados),
      signal: AbortSignal.timeout(30000),
    })
    if (!r.ok) console.error(`Webhook respondeu HTTP ${r.status}`)
  } catch (e) {
    console.error('Falha ao avisar o sistema:', e.message)
  }
}

// ------------------------------------------------------------------ fila de envio
let fila = Promise.resolve()
let ultimoEnvio = 0
const espera = ms => new Promise(r => setTimeout(r, ms))
const aleatorio = (a, b) => a + Math.floor(Math.random() * (b - a))

function enfileirar (tarefa) {
  const p = fila.then(async () => {
    const intervalo = aleatorio(INTERVALO_MIN, INTERVALO_MAX)
    const falta = ultimoEnvio + intervalo - Date.now()
    if (falta > 0) await espera(falta)
    try { return await tarefa() } finally { ultimoEnvio = Date.now() }
  })
  fila = p.catch(() => {})
  return p
}

async function enviar ({ telefone, texto, documento }) {
  if (estado.conexao !== 'conectado') throw Object.assign(new Error('WhatsApp desconectado. Escaneie o QR Code em Configurações.'), { http: 503 })
  const numero = String(telefone || '').replace(/\D/g, '')
  if (numero.length < 12) throw Object.assign(new Error('Telefone inválido.'), { http: 400 })
  const [existe] = await sock.onWhatsApp(numero)
  if (!existe?.exists) throw Object.assign(new Error(`O número ${numero} não tem WhatsApp.`), { http: 404 })
  const destino = jidNormalizedUser(existe.jid)

  return enfileirar(async () => {
    await sock.presenceSubscribe(destino).catch(() => {})
    await sock.sendPresenceUpdate('composing', destino).catch(() => {})
    await espera(aleatorio(2000, 5000))
    await sock.sendPresenceUpdate('paused', destino).catch(() => {})
    let conteudo
    if (documento?.base64) {
      conteudo = {
        document: Buffer.from(documento.base64, 'base64'),
        mimetype: documento.mime || 'application/pdf',
        fileName: documento.nome || 'boleto.pdf',
        caption: texto || '',
      }
    } else {
      conteudo = { text: texto }
    }
    const r = await sock.sendMessage(destino, conteudo)
    return { id: r?.key?.id || '' }
  })
}

// ------------------------------------------------------------------ API HTTP
function autorizado (req) {
  const h = (req.headers.authorization || '').replace(/^Bearer\s+/i, '')
  const a = Buffer.from(h)
  const b = Buffer.from(TOKEN)
  return a.length === b.length && timingSafeEqual(a, b)
}

function lerCorpo (req, limite = 25 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let tamanho = 0
    const partes = []
    req.on('data', c => {
      tamanho += c.length
      if (tamanho > limite) { reject(Object.assign(new Error('Corpo muito grande'), { http: 413 })); req.destroy() } else partes.push(c)
    })
    req.on('end', () => {
      try { resolve(partes.length ? JSON.parse(Buffer.concat(partes).toString()) : {}) } catch { reject(Object.assign(new Error('JSON inválido'), { http: 400 })) }
    })
    req.on('error', reject)
  })
}

function responder (res, codigo, dados) {
  res.writeHead(codigo, { 'Content-Type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(dados))
}

http.createServer(async (req, res) => {
  if (req.url === '/saude') return responder(res, 200, { ok: true })
  if (!autorizado(req)) return responder(res, 401, { erro: 'não autorizado' })
  try {
    if (req.method === 'GET' && req.url === '/status') {
      return responder(res, 200, estado)
    }
    if (req.method === 'POST' && req.url === '/enviar') {
      const corpo = await lerCorpo(req)
      return responder(res, 200, await enviar(corpo))
    }
    if (req.method === 'POST' && req.url === '/desconectar') {
      if (sock) await sock.logout().catch(() => {})
      return responder(res, 200, { ok: true })
    }
    return responder(res, 404, { erro: 'rota inexistente' })
  } catch (e) {
    return responder(res, e.http || 500, { erro: e.message })
  }
}).listen(PORTA, HOST, () => console.log(`Gateway WhatsApp ouvindo em ${HOST}:${PORTA}`))

conectar().catch(e => { console.error(e); agendarReconexao(10000) })
