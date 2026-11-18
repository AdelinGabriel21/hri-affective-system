// Test: compara signal quality V1 (ce foloseste SDK-ul Python) cu V2
// (doar in SDK-ul JavaScript) pentru F5 si F6.
// Rulare: npm install && node test_signal_quality.js
// Foloseste acelasi .env ca eeg_reader.py.

// ESM (import), pentru ca build-ul CommonJS al @neurosity/sdk 7.5.1 nu exporta nimic.
import "dotenv/config";
import { Neurosity } from "@neurosity/sdk";

const DEVICE_ID = (process.env.NEUROSITY_DEVICE_ID || "").trim();
const EMAIL = (process.env.NEUROSITY_EMAIL || "").trim();
const PASSWORD = process.env.NEUROSITY_PASSWORD || "";
const DURATION_SECONDS = Number(process.env.DURATION_SECONDS || 60);

// Aceiasi indecsi ca in eeg_reader.py
const F5 = 2;
const F6 = 5;

const latest = { v1: null, v2: null };

function fmt(value, digits = 1) {
  return typeof value === "number" ? value.toFixed(digits) : "?";
}

function v1Text(channel) {
  if (!channel) return "?";
  return `${fmt(channel.standardDeviation)}(${channel.status})`;
}

function v2Text(channel) {
  if (!channel) return "?";
  return `${fmt(channel.score, 2)}/${channel.status} sd=${fmt(channel.standardDeviation)}`;
}

async function main() {
  if (!DEVICE_ID || !EMAIL || !PASSWORD) {
    console.error("Lipsesc NEUROSITY_DEVICE_ID / NEUROSITY_EMAIL / NEUROSITY_PASSWORD in .env");
    process.exit(1);
  }

  const neurosity = new Neurosity({ deviceId: DEVICE_ID });
  await neurosity.login({ email: EMAIL, password: PASSWORD });
  console.log("[OK] Logat. Citim signal quality V1 si V2...");

  let v2DebugPrinted = false;
  const subs = [];

  subs.push(
    neurosity.signalQuality().subscribe({
      next: (quality) => {
        latest.v1 = quality;
      },
      error: (err) => console.error("[V1 ERROR]", err),
    })
  );

  subs.push(
    neurosity.signalQualityV2().subscribe({
      next: (quality) => {
        latest.v2 = quality;
        if (!v2DebugPrinted) {
          console.log("[DEBUG] Primul payload V2:", JSON.stringify(quality));
          v2DebugPrinted = true;
        }
      },
      error: (err) => console.error("[V2 ERROR]", err),
    })
  );

  const timer = setInterval(() => {
    const v1 = latest.v1;
    const v2 = latest.v2;
    const byChannel = (v2 && v2.byChannel) || {};

    const v1Part = v1
      ? `V1 F5=${v1Text(v1[F5])} F6=${v1Text(v1[F6])}`
      : "V1 (nimic inca)";
    const v2Part = v2
      ? `V2 F5=${v2Text(byChannel.F5)} F6=${v2Text(byChannel.F6)} overall=${v2Text(v2.overall)}`
      : "V2 (nimic inca)";

    console.log(`${v1Part} | ${v2Part}`);
  }, 1000);

  let stopping = false;
  async function stop() {
    if (stopping) return;
    stopping = true;
    clearInterval(timer);
    subs.forEach((sub) => sub.unsubscribe());
    try {
      await neurosity.logout();
    } catch (err) {
      // nu conteaza la oprire
    }
    console.log("[STOP] Gata.");
    process.exit(0);
  }

  process.on("SIGINT", stop);
  setTimeout(stop, DURATION_SECONDS * 1000);
}

main().catch((err) => {
  console.error("[ERROR]", err);
  process.exit(1);
});
