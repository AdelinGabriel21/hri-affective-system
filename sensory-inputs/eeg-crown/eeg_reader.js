// EEG reader pentru Neurosity Crown, in JavaScript (Node).
// Face acelasi lucru ca eeg_reader.py, dar foloseste signalQualityV2
// (exista doar in SDK-ul JS) ca sa decida cand trimitem no_signal.
//
// Rulare: npm install && node eeg_reader.js

import "dotenv/config";
import mqtt from "mqtt";
import { Neurosity } from "@neurosity/sdk";

const MQTT_HOST = process.env.MQTT_HOST || "hri.thecraceatree.com";
const MQTT_PORT = Number(process.env.MQTT_PORT || 443);
const MQTT_TOPIC = process.env.MQTT_TOPIC || "telemetry/eeg";
const USE_WSS = (process.env.MQTT_USE_WSS || "true").toLowerCase() === "true";

const DEVICE_ID = (process.env.NEUROSITY_DEVICE_ID || "").trim();
// Numele publicat in campul "device" din mesaj. Nu publicam DEVICE_ID-ul real,
// pentru ca ajunge pe broker si il vad toti subscriberii.
const DEVICE_NAME = "neurosity_crown";
const EMAIL = (process.env.NEUROSITY_EMAIL || "").trim();
const PASSWORD = process.env.NEUROSITY_PASSWORD || "";

// Calitatea semnalului (V2), verificata pe F5 si F6:
// status trebuie sa fie "adequate" si scorul de amplitudine (0-1) peste prag.
// Scorul sd scade cand semnalul are artefacte mari (miscare, clipit, contact slab).
const MIN_SD_SCORE = Number(process.env.EEG_MIN_SD_SCORE || 0.7);

// Pozitia lui F5 si F6 in listele de la powerByBand, daca SDK-ul nu trimite channelNames
let F5 = 2;
let F6 = 5;
const MAX_AGE_SECONDS = 3;

const latest = {
  bands: null,
  bandsTime: 0,
  calm: null,
  focus: null,
  quality: null,
  qualityTime: 0,
};

function now() {
  return Date.now() / 1000;
}

function onBands(message) {
  // in functie de versiune, benzile vin direct sau in message.data
  const bands = message.data || message;
  const names = message.info && message.info.channelNames;
  if (Array.isArray(names) && names.includes("F5") && names.includes("F6")) {
    F5 = names.indexOf("F5");
    F6 = names.indexOf("F6");
  }
  latest.bands = bands;
  latest.bandsTime = now();
}

function onQuality(message) {
  latest.quality = message;
  latest.qualityTime = now();
}

function createMqttClient() {
  const url = USE_WSS
    ? `wss://${MQTT_HOST}:${MQTT_PORT}/mqtt`
    : `mqtt://${MQTT_HOST}:${MQTT_PORT}`;
  console.log(`[MQTT] Conectare la ${MQTT_HOST}:${MQTT_PORT}...`);
  const client = mqtt.connect(url, { clientId: "eeg-crown-client", keepalive: 60 });
  client.on("connect", () => console.log("[MQTT] Conectat."));
  client.on("error", (err) => console.error("[MQTT ERROR]", err.message));
  return client;
}

async function connectNeurosity() {
  const sdk = new Neurosity({ deviceId: DEVICE_ID });
  await sdk.login({ email: EMAIL, password: PASSWORD });
  const subs = [
    sdk.brainwaves("powerByBand").subscribe(onBands),
    sdk.calm().subscribe((m) => (latest.calm = m.probability)),
    sdk.focus().subscribe((m) => (latest.focus = m.probability)),
    sdk.signalQualityV2().subscribe(onQuality),
  ];
  return { sdk, subs };
}

/**
 * Frontal Alpha Asymmetry (FAA) = ln(Alpha_Right) - ln(Alpha_Left)
 * Valoare pozitiva = abordare/stare pozitiva (Valence ridicat).
 * Valoare negativa = retragere/anxietate (Valence scazut).
 */
function calculateFaa(alphaLeft, alphaRight) {
  return Math.log(Math.max(0.001, alphaRight)) - Math.log(Math.max(0.001, alphaLeft));
}

function average(values) {
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

function round(value, digits) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function buildMessage(status, data) {
  return {
    v: 1,
    source: "eeg",
    device: DEVICE_ID ? DEVICE_NAME : "crown-sim",
    timestamp: now(),
    status,
    mock: !DEVICE_ID,
    data,
  };
}

function channelOk(channel) {
  return (
    channel &&
    channel.status === "adequate" &&
    typeof channel.standardDeviation === "number" &&
    channel.standardDeviation >= MIN_SD_SCORE
  );
}

function describe(channel) {
  if (!channel) return "?";
  return `${channel.status} score=${round(channel.score, 2)} sd=${round(channel.standardDeviation, 2)}`;
}

function getRealData() {
  const bands = latest.bands;
  const quality = latest.quality;

  if (!bands || !Array.isArray(bands.alpha)) return ["no_signal", null];
  if (now() - latest.bandsTime > MAX_AGE_SECONDS) return ["no_signal", null];

  if (!quality || !quality.byChannel) return ["no_signal", null];
  if (now() - latest.qualityTime > MAX_AGE_SECONDS) return ["no_signal", null];

  const qF5 = quality.byChannel.F5;
  const qF6 = quality.byChannel.F6;
  if (!channelOk(qF5) || !channelOk(qF6)) {
    console.log(`[DEBUG] F5=${describe(qF5)} | F6=${describe(qF6)}`);
    return ["no_signal", null];
  }

  const alphaLeft = bands.alpha[F5];
  const alphaRight = bands.alpha[F6];
  const faa = calculateFaa(alphaLeft, alphaRight);

  const calm = latest.calm === null ? null : round(latest.calm, 2);
  const focus = latest.focus === null ? null : round(latest.focus, 2);

  const data = {
    bands: {
      delta: round(average(bands.delta), 2),
      theta: round(average(bands.theta), 2),
      alpha: round(average(bands.alpha), 2),
      beta: round(average(bands.beta), 2),
      gamma: round(average(bands.gamma), 2),
    },
    alpha_left: round(alphaLeft, 2),
    alpha_right: round(alphaRight, 2),
    faa: round(faa, 3),
    calm_score: calm,
    focus_score: focus,
  };
  return ["ok", data];
}

function uniform(min, max) {
  return min + Math.random() * (max - min);
}

function getMockData(step) {
  const alphaLeft = 10.0 + 2.0 * Math.sin(step) + uniform(-0.5, 0.5);
  const alphaRight = 11.0 + 2.0 * Math.cos(step) + uniform(-0.5, 0.5);
  const delta = 20.0 + uniform(-1.0, 1.0);
  const theta = 6.0 + uniform(-0.3, 0.3);
  const alpha = (alphaLeft + alphaRight) / 2;
  const beta = 15.0 + 3.0 * Math.sin(step * 1.5) + uniform(-0.8, 0.8);
  const gamma = 4.0 + uniform(-0.3, 0.3);

  const faa = calculateFaa(alphaLeft, alphaRight);
  const calm = Math.max(0.0, Math.min(1.0, 0.5 + 0.4 * Math.sin(step / 3)));
  const focus = Math.max(0.0, Math.min(1.0, beta / (alpha + theta)));

  const data = {
    bands: {
      delta: round(delta, 2),
      theta: round(theta, 2),
      alpha: round(alpha, 2),
      beta: round(beta, 2),
      gamma: round(gamma, 2),
    },
    alpha_left: round(alphaLeft, 2),
    alpha_right: round(alphaRight, 2),
    faa: round(faa, 3),
    calm_score: round(calm, 2),
    focus_score: round(focus, 2),
  };
  return ["ok", data];
}

async function main() {
  const client = createMqttClient();
  console.log("[EEG] Pornire modul Neurosity Crown...");

  let neurosity = null;
  if (DEVICE_ID) {
    console.log("[EEG] Conectare la casca Neurosity Crown...");
    neurosity = await connectNeurosity();
  } else {
    console.log("[MOCK EEG] Nu s-a gasit NEUROSITY_DEVICE_ID in .env. Rulam in mod SIMULATOR...");
  }

  let step = 0;
  const timer = setInterval(() => {
    step += 0.2;
    const [status, data] = neurosity ? getRealData() : getMockData(step);

    if (status === "no_signal") {
      client.publish(MQTT_TOPIC, JSON.stringify(buildMessage("no_signal", null)));
      console.log("[EEG] no_signal - verifica casca");
    } else {
      client.publish(MQTT_TOPIC, JSON.stringify(buildMessage(status, data)));
      console.log(`[EEG] FAA: ${data.faa} | Calm: ${data.calm_score} | Focus: ${data.focus_score}`);
    }
  }, 1000);

  let stopping = false;
  async function stop() {
    if (stopping) return;
    stopping = true;
    console.log("[EEG] Oprire modul.");
    clearInterval(timer);
    if (neurosity) {
      neurosity.subs.forEach((sub) => sub.unsubscribe());
      try {
        await neurosity.sdk.logout();
      } catch (err) {
        // nu conteaza la oprire
      }
    }
    client.end(false, {}, () => process.exit(0));
  }

  process.on("SIGINT", stop);
}

main().catch((err) => {
  console.error("[ERROR]", err);
  process.exit(1);
});