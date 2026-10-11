/*
 * Gateway do WhatsApp (conexão por QR code) para o Portal do Colaborador.
 *
 * - Conecta um número de WhatsApp pelo QR code (biblioteca Baileys) e repassa cada mensagem recebida
 *   para o portal (POST /whatsapp/gateway/). O portal decide a resposta e manda de volta por aqui.
 * - Só escuta em 127.0.0.1 (nada fica exposto na internet) e exige a senha WHATSAPP_GATEWAY_TOKEN do .env
 *   em todas as chamadas, nos dois sentidos.
 * - A sessão (as "chaves" do WhatsApp conectado) fica em ./sessao — NUNCA mande essa pasta para o GitHub.
 *
 * ATENÇÃO: conexão não oficial. O WhatsApp pode banir o número. Use um chip secundário, não o número
 * oficial da empresa. Para produção, a API oficial da Meta já está pronta no portal (WHATSAPP_CANAL=meta).
 *
 * Rodar: node gateway.mjs   (ou scripts\gateway_whatsapp.ps1, que instala e liga sozinho)
 */
import { timingSafeEqual } from "node:crypto";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import makeWASocket, {
  Browsers, DisconnectReason, downloadMediaMessage, fetchLatestBaileysVersion, generateMessageIDV2,
  normalizeMessageContent, useMultiFileAuthState,
} from "baileys";
import pino from "pino";
import QRCode from "qrcode";

const PASTA = dirname(fileURLToPath(import.meta.url));
const PASTA_SESSAO = join(PASTA, "sessao");
const LIMITE_ARQUIVO = 10 * 1024 * 1024;

// Configuração: lida do .env do portal (pasta de cima), sem depender de outra biblioteca.
function lerEnv() {
  const valores = {};
  const caminho = join(PASTA, "..", ".env");
  if (existsSync(caminho)) {
    for (const linha of readFileSync(caminho, "utf8").split(/\r?\n/)) {
      const achado = linha.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (achado) valores[achado[1]] = achado[2].replace(/^["']|["']$/g, "");
    }
  }
  return { ...valores, ...process.env };
}
const ENV = lerEnv();
const TOKEN = ENV.WHATSAPP_GATEWAY_TOKEN || "";
const PORTA = Number(ENV.WHATSAPP_GATEWAY_PORTA || 3005);
const PORTAL = (ENV.WHATSAPP_GATEWAY_PORTAL || "http://127.0.0.1:8020").replace(/\/$/, "");
// Grupos que o portal lê (só lê, nunca responde): ex. "Faltas, Atestados e Coberturas - SEMED".
// Códigos separados por vírgula no .env; a lista de grupos aparece em GET /grupos.
const GRUPOS_OCORRENCIAS = new Set(String(ENV.WHATSAPP_GRUPOS_OCORRENCIAS || "").split(",")
  .map((codigo) => codigo.trim()).filter(Boolean));
if (TOKEN.length < 20) {
  console.error("Defina WHATSAPP_GATEWAY_TOKEN (20+ caracteres) no .env do portal. O gateway não liga sem senha.");
  process.exit(1);
}

// Ritmo de envio "de gente": quem manda rápido demais e sem parar parece robô e o número pode ser bloqueado.
const LIMITE_POR_MINUTO = Math.max(1, Number(ENV.WHATSAPP_GATEWAY_LIMITE_MINUTO || 30));
const PAUSA_MINIMA_MS = 900;
const PAUSA_MAXIMA_MS = 2500;

const logger = pino({ level: "warn" });
const situacao = { estado: "iniciando", qr: null, numero: null, desde: new Date().toISOString() };
const jidDoTelefone = new Map(); // para responder no mesmo endereço em que a pessoa escreveu
let sock = null;

function mudar(estado, extra = {}) {
  Object.assign(situacao, { estado, desde: new Date().toISOString() }, extra);
  console.log(new Date().toISOString(), "situação:", estado, situacao.numero || "");
}

// --- Identificação do telefone (o WhatsApp novo pode mandar um identificador "@lid" no lugar do número) ---
function telefoneDe(key) {
  for (const jid of [key.remoteJid, key.remoteJidAlt, key.senderPn]) {
    if (jid && jid.endsWith("@s.whatsapp.net")) return jid.split("@")[0].split(":")[0];
  }
  return null;
}

// --- Fila de envio: uma mensagem por vez, com "digitando…", pausa e limite por minuto ---
const esperar = (ms) => new Promise((pronto) => setTimeout(pronto, ms));
const enviosRecentes = []; // horários dos últimos envios (para o limite por minuto)
const enviadasPeloGateway = new Set(); // ids das mensagens que o assistente/painel mandou (não são do celular)
let fila = Promise.resolve();

function enfileirar(tarefa) {
  const resultado = fila.then(tarefa);
  fila = resultado.catch(() => {}); // um envio com erro não trava os próximos
  return resultado;
}

async function respeitarLimite() {
  for (;;) {
    const agora = Date.now();
    while (enviosRecentes.length && agora - enviosRecentes[0] >= 60_000) enviosRecentes.shift();
    if (enviosRecentes.length < LIMITE_POR_MINUTO) return;
    await esperar(60_000 - (agora - enviosRecentes[0]) + 100);
  }
}

async function enviarComoPessoa(jid, conteudo) {
  await respeitarLimite();
  if (situacao.estado !== "conectado") throw new Error("WhatsApp não está conectado");
  const texto = conteudo.text || conteudo.caption || "";
  const pausa = Math.min(PAUSA_MAXIMA_MS, PAUSA_MINIMA_MS + texto.length * 12) + Math.random() * 700;
  await sock.sendPresenceUpdate("composing", jid).catch(() => {});
  await esperar(pausa);
  await sock.sendPresenceUpdate("paused", jid).catch(() => {});
  // O id é criado antes do envio: o WhatsApp avisa da própria mensagem antes de o envio terminar.
  const messageId = generateMessageIDV2(sock.user?.id);
  enviadasPeloGateway.add(messageId);
  setTimeout(() => enviadasPeloGateway.delete(messageId), 30 * 60_000);
  const enviada = await sock.sendMessage(jid, conteudo, { messageId });
  enviosRecentes.push(Date.now());
  return enviada;
}

async function repassarParaOPortal(dados) {
  try {
    const resposta = await fetch(`${PORTAL}/whatsapp/gateway/`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Gateway-Token": TOKEN },
      body: JSON.stringify(dados),
      signal: AbortSignal.timeout(120_000),
    });
    if (!resposta.ok) {
      console.error("Portal respondeu", resposta.status, "para a mensagem", dados.id);
      return {};
    }
    return (resposta.headers.get("content-type") || "").includes("json") ? await resposta.json() : {};
  } catch (erro) {
    console.error("Não consegui falar com o portal em", PORTAL, "-", erro.message);
    return {};
  }
}

// Reação do assistente numa mensagem (🤖 = "esta ocorrência foi registrada no sistema").
async function reagir(key, emoji) {
  if (situacao.estado !== "conectado") return;
  const messageId = generateMessageIDV2(sock.user?.id);
  enviadasPeloGateway.add(messageId);
  setTimeout(() => enviadasPeloGateway.delete(messageId), 30 * 60_000);
  await sock.sendMessage(key.remoteJid, { react: { text: emoji, key } }, { messageId })
    .catch((erro) => console.error("Não consegui reagir à mensagem:", erro.message));
}

async function tratarMensagem(msg) {
  const key = msg.key || {};
  if (!msg.message) return;
  const jid = key.remoteJid || "";
  if (jid.endsWith("@g.us")) {  // grupos: só o de ocorrências é lido, e o assistente nunca responde em grupo
    if (GRUPOS_OCORRENCIAS.has(jid)) return tratarMensagemDoGrupo(msg);  // inclui o 👍 do ADM (do próprio chip)
    return;
  }
  if (jid.endsWith("@broadcast") || jid.endsWith("@newsletter")) return; // status, canais
  if (key.fromMe) return tratarMensagemDoCelular(msg);
  const telefone = telefoneDe(key);
  if (!telefone) {
    console.warn("Mensagem sem número de telefone identificável (", jid, "): ignorada");
    return;
  }
  const conteudo = normalizeMessageContent(msg.message) || {};
  let texto = conteudo.conversation || conteudo.extendedTextMessage?.text || "";
  let midia = null;
  // Foto/PDF (termo de VT) e áudio (vai para o RH ouvir no painel) são baixados e repassados ao portal.
  const arquivo = conteudo.imageMessage || conteudo.documentMessage || conteudo.audioMessage;
  // O que o assistente não consegue ler: o portal responde pedindo para escrever.
  const tipo = conteudo.audioMessage ? "audio" : conteudo.videoMessage ? "video"
    : conteudo.stickerMessage ? "figurinha" : "";
  if (!texto && !arquivo && !tipo) return; // sinais internos do WhatsApp (sem texto): antes viravam resposta em dobro

  jidDoTelefone.set(telefone, jid);
  sock.readMessages([key]).catch(() => {}); // marca como lida (os "dois tracinhos azuis"), como uma pessoa faria
  if (arquivo) {
    texto = arquivo.caption || texto;
    let base64 = "";
    if (Number(arquivo.fileLength || 0) <= LIMITE_ARQUIVO) {
      try {
        const buffer = await downloadMediaMessage(msg, "buffer", {}, { logger, reuploadRequest: sock.updateMediaMessage });
        base64 = buffer.toString("base64");
      } catch (erro) {
        console.error("Não consegui baixar o arquivo recebido:", erro.message);
      }
    }
    midia = {
      base64,
      nome: arquivo.fileName || (conteudo.imageMessage ? "foto.jpg" : conteudo.audioMessage ? "audio.ogg" : "documento.pdf"),
      mime: arquivo.mimetype || "",
    };
  }
  await repassarParaOPortal({ de: telefone, id: key.id || "", texto, midia, tipo });
}

// Grupo de ocorrências (faltas, atestados, coberturas, extras): repassa ao portal, que separa os campos.
// Não marca como lida nem responde nada no grupo.
async function tratarMensagemDoGrupo(msg) {
  const key = msg.key;
  const conteudo = normalizeMessageContent(msg.message) || {};
  if (conteudo.reactionMessage) {  // 👍 numa ocorrência: o portal marca "OK do ADM" (se foi o próprio chip)
    if (enviadasPeloGateway.has(key.id)) return; // o 🤖 que o próprio assistente pôs
    const reacao = conteudo.reactionMessage;
    return repassarParaOPortal({
      tipo: "reacao_grupo", grupo: key.remoteJid, id: key.id || "", id_reagido: reacao.key?.id || "",
      emoji: reacao.text || "", de_mim: Boolean(key.fromMe), autor_nome: msg.pushName || "",
    });
  }
  if (key.fromMe && enviadasPeloGateway.has(key.id)) return;
  const arquivo = conteudo.imageMessage || conteudo.documentMessage;
  const texto = conteudo.conversation || conteudo.extendedTextMessage?.text || arquivo?.caption || "";
  if (!texto && !arquivo) return; // figurinha, áudio, reação, sinais internos
  let midia = null;
  if (arquivo && Number(arquivo.fileLength || 0) <= LIMITE_ARQUIVO) {
    try {
      const buffer = await downloadMediaMessage(msg, "buffer", {}, { logger, reuploadRequest: sock.updateMediaMessage });
      midia = { base64: buffer.toString("base64"), nome: arquivo.fileName || (conteudo.imageMessage ? "foto.jpg" : "documento.pdf"),
                mime: arquivo.mimetype || "" };
    } catch (erro) {
      console.error("Não consegui baixar o anexo do grupo:", erro.message);
    }
  }
  const autor = telefoneDe({ remoteJid: key.participantAlt, remoteJidAlt: key.participant, senderPn: key.participantPn }) || "";
  const resposta = await repassarParaOPortal({
    tipo: "ocorrencia_grupo", grupo: key.remoteJid, id: key.id || "", texto, midia,
    autor, autor_nome: msg.pushName || "", enviada_em: Number(msg.messageTimestamp || 0),
  });
  if (resposta.reagir) await reagir(key, resposta.reagir); // 🤖: a ocorrência entrou no sistema
}

// Alguém do RH escreveu pelo próprio celular do chip (ou pelo WhatsApp Web dele), e não pelo painel:
// o portal registra a mensagem e para o assistente nessa conversa, para o robô não entrar no meio.
async function tratarMensagemDoCelular(msg) {
  const key = msg.key;
  if (enviadasPeloGateway.has(key.id)) return; // mensagem do próprio assistente (ou do painel)
  const segundos = Number(msg.messageTimestamp || 0);
  if (segundos && Date.now() / 1000 - segundos > 120) return; // histórico antigo, não é conversa de agora
  const telefone = telefoneDe(key);
  if (!telefone) return;
  const conteudo = normalizeMessageContent(msg.message) || {};
  const texto = conteudo.conversation || conteudo.extendedTextMessage?.text
    || conteudo.imageMessage?.caption || conteudo.documentMessage?.caption || "";
  const temArquivo = Boolean(conteudo.imageMessage || conteudo.documentMessage || conteudo.audioMessage
    || conteudo.videoMessage || conteudo.stickerMessage);
  if (!texto && !temArquivo) return; // sinais internos (lido, reação...)
  await repassarParaOPortal({ de: telefone, id: key.id || "", texto: texto || "[arquivo enviado pelo celular]",
                              midia: null, tipo: "rh_celular" });
}

// Ligação: o assistente não atende. Recusa (como "ocupado") e o portal manda uma mensagem explicando.
async function tratarLigacao(ligacao) {
  if (ligacao.status !== "offer" || ligacao.isGroup) return;
  await sock.rejectCall(ligacao.id, ligacao.from).catch((erro) => console.error("Não consegui recusar a ligação:", erro.message));
  const pn = ligacao.callerPn && !ligacao.callerPn.includes("@") ? `${ligacao.callerPn}@s.whatsapp.net` : ligacao.callerPn;
  const telefone = telefoneDe({ remoteJid: ligacao.from, remoteJidAlt: pn });
  if (!telefone) return;
  jidDoTelefone.set(telefone, ligacao.from.endsWith("@s.whatsapp.net") ? ligacao.from : `${telefone}@s.whatsapp.net`);
  await repassarParaOPortal({ de: telefone, id: `ligacao-${ligacao.id}`, texto: "", midia: null, tipo: "ligacao" });
}

async function conectar() {
  const { state, saveCreds } = await useMultiFileAuthState(PASTA_SESSAO);
  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch {
    version = undefined; // sem internet agora: usa a versão padrão da biblioteca
  }
  sock = makeWASocket({
    auth: state, version, logger, browser: Browsers.windows("Portal do Colaborador"),
    markOnlineOnConnect: false, syncFullHistory: false,
  });
  sock.ev.on("creds.update", saveCreds);
  sock.ev.on("connection.update", async (atualizacao) => {
    if (atualizacao.qr) {
      mudar("aguardando_qr", { qr: await QRCode.toDataURL(atualizacao.qr, { margin: 1, width: 320 }), numero: null });
    }
    if (atualizacao.connection === "open") {
      mudar("conectado", { qr: null, numero: (sock.user?.id || "").split(":")[0].split("@")[0] });
    }
    if (atualizacao.connection === "close") {
      const codigo = atualizacao.lastDisconnect?.error?.output?.statusCode;
      if (codigo === DisconnectReason.loggedOut) {
        rmSync(PASTA_SESSAO, { recursive: true, force: true }); // desconectado no celular: novo QR code
        mudar("desconectado", { qr: null, numero: null });
      } else {
        mudar("reconectando", { qr: null });
      }
      setTimeout(() => conectar().catch((erro) => console.error("Erro ao reconectar:", erro)), 3000);
    }
  });
  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    // Só mensagens novas. As do próprio número (RH digitando no celular) podem vir como "append";
    // o histórico antigo é descartado pela hora da mensagem em tratarMensagemDoCelular.
    if (type !== "notify" && type !== "append") return;
    for (const msg of messages) {
      if (type === "append" && !msg.key?.fromMe) continue;
      await tratarMensagem(msg).catch((erro) => console.error("Erro ao tratar mensagem:", erro));
    }
  });
  sock.ev.on("call", async (ligacoes) => {
    for (const ligacao of ligacoes) {
      await tratarLigacao(ligacao).catch((erro) => console.error("Erro ao tratar ligação:", erro));
    }
  });
}

// --- API local usada pelo portal ---
function autorizado(req) {
  const recebido = Buffer.from(String(req.headers["x-gateway-token"] || ""));
  const esperado = Buffer.from(TOKEN);
  return recebido.length === esperado.length && timingSafeEqual(recebido, esperado);
}

async function lerCorpo(req) {
  const partes = [];
  let tamanho = 0;
  for await (const parte of req) {
    tamanho += parte.length;
    if (tamanho > 20 * 1024 * 1024) throw new Error("corpo grande demais");
    partes.push(parte);
  }
  return partes.length ? JSON.parse(Buffer.concat(partes).toString("utf8")) : {};
}

function responder(res, status, dados) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(dados));
}

const servidor = createServer(async (req, res) => {
  if (!autorizado(req)) return responder(res, 403, { erro: "senha do gateway inválida" });
  try {
    if (req.method === "GET" && req.url === "/status") return responder(res, 200, situacao);
    if (req.method === "GET" && req.url === "/grupos") {  // para descobrir o código do grupo de ocorrências
      if (situacao.estado !== "conectado") return responder(res, 503, { erro: "WhatsApp não está conectado" });
      const grupos = await sock.groupFetchAllParticipating();
      return responder(res, 200, Object.values(grupos).map((g) => ({
        id: g.id, nome: g.subject, participantes: g.participants?.length || 0, lido: GRUPOS_OCORRENCIAS.has(g.id),
      })));
    }
    if (req.method === "POST" && req.url === "/enviar") {
      if (situacao.estado !== "conectado") return responder(res, 503, { erro: "WhatsApp não está conectado" });
      const dados = await lerCorpo(req);
      const para = String(dados.para || "").replace(/\D/g, "");
      const jid = jidDoTelefone.get(para) || `${para}@s.whatsapp.net`;
      const conteudo = dados.tipo === "documento"
        ? { document: Buffer.from(dados.base64 || "", "base64"), mimetype: dados.mime || "application/pdf",
            fileName: dados.nome || "documento.pdf", caption: dados.legenda || undefined }
        : { text: String(dados.texto || "") };
      const enviada = await enfileirar(() => enviarComoPessoa(jid, conteudo));
      return responder(res, 200, { id: enviada?.key?.id || "" });
    }
    if (req.method === "POST" && req.url === "/desconectar") {
      await sock?.logout().catch(() => {}); // o evento "close" apaga a sessão e gera um novo QR code
      return responder(res, 200, { ok: true });
    }
    return responder(res, 404, { erro: "não encontrado" });
  } catch (erro) {
    console.error("Erro na API do gateway:", erro);
    return responder(res, 500, { erro: String(erro.message || erro) });
  }
});

servidor.listen(PORTA, "127.0.0.1", () => {
  console.log(`Gateway do WhatsApp em http://127.0.0.1:${PORTA} — repassando para ${PORTAL}`);
  conectar().catch((erro) => {
    console.error("Erro ao conectar:", erro);
    mudar("erro");
  });
});
