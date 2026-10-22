// Alternativa leve à Evolution API: um microserviço Baileys sem Docker.
// Uso:
//   cd whatsapp-baileys && npm install && BAILEYS_TOKEN=seu-token node index.js
// Escaneie o QR que aparece no terminal. A sessão fica salva em ./auth.
// Endpoint: POST http://localhost:3333/send  {"number":"5511999999999","text":"..."}
//           header x-token: <BAILEYS_TOKEN>

import http from "node:http";
import qrcode from "qrcode-terminal";
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} from "@whiskeysockets/baileys";
import pino from "pino";

const PORT = Number(process.env.PORT || 3333);
const TOKEN = process.env.BAILEYS_TOKEN || "";
let sock = null;
let conectado = false;

async function iniciar() {
  const { state, saveCreds } = await useMultiFileAuthState("./auth");
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: "warn" }),
    browser: ["Radar HQ", "Chrome", "1.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log("\nEscaneie o QR abaixo (WhatsApp > Dispositivos conectados):\n");
      qrcode.generate(qr, { small: true });
    }
    if (connection === "open") {
      conectado = true;
      console.log("WhatsApp conectado.");
    }
    if (connection === "close") {
      conectado = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        console.log("Sessão encerrada no celular. Apague ./auth e rode de novo.");
        process.exit(1);
      }
      console.log("Conexão caiu, reconectando em 5s...");
      setTimeout(iniciar, 5000);
    }
  });
}

function lerCorpo(req) {
  return new Promise((ok, erro) => {
    let dados = "";
    req.on("data", (c) => (dados += c));
    req.on("end", () => {
      try { ok(JSON.parse(dados || "{}")); } catch (e) { erro(e); }
    });
  });
}

http
  .createServer(async (req, res) => {
    const responder = (code, obj) => {
      res.writeHead(code, { "Content-Type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    if (req.method === "GET" && req.url === "/status") return responder(200, { conectado });
    if (req.method !== "POST" || req.url !== "/send") return responder(404, { erro: "rota" });
    if (TOKEN && req.headers["x-token"] !== TOKEN) return responder(401, { erro: "token" });
    if (!conectado) return responder(503, { erro: "whatsapp desconectado" });
    try {
      const { number, text } = await lerCorpo(req);
      const jid = `${String(number).replace(/\D/g, "")}@s.whatsapp.net`;
      await sock.sendMessage(jid, { text });
      responder(200, { ok: true });
    } catch (e) {
      responder(500, { erro: String(e) });
    }
  })
  .listen(PORT, "127.0.0.1", () => console.log(`Baileys ouvindo em http://127.0.0.1:${PORT}`));

iniciar();
