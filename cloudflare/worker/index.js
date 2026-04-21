/**
 * SemeClaw Update Server — Cloudflare Worker
 *
 * Proxies manifest.json and ad MP3 files from a Cloudflare R2 bucket.
 * Uses secret_text bindings for R2 credentials.
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS, HEAD",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Max-Age": "3600",
};

function addCors(headers) {
  for (const k in CORS_HEADERS) headers.set(k, CORS_HEADERS[k]);
}

function jsonResponse(obj, status = 200) {
  const h = new Headers();
  h.set("Content-Type", "application/json");
  h.set("X-SemeClaw-Update-Server", "1.0");
  addCors(h);
  return new Response(JSON.stringify(obj, null, 2), { status, headers: h });
}

// ── AWS Signature V4 (raw bytes for key derivation) ────────────────────────

async function sha256Hex(msg) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(msg));
  return Array.from(new Uint8Array(d)).map(b => b.toString(16).padStart(2, "0")).join("");
}

async function hmacRaw(keyBytes, msgBytes) {
  const k = await crypto.subtle.importKey(
    "raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  return crypto.subtle.sign("HMAC", k, msgBytes);
}

async function getSignatureKey(secretKey, dateStamp, region, service) {
  const enc = new TextEncoder();
  const kDate = await hmacRaw(enc.encode("AWS4" + secretKey), enc.encode(dateStamp));
  const kRegion = await hmacRaw(new Uint8Array(kDate), enc.encode(region));
  const kService = await hmacRaw(new Uint8Array(kRegion), enc.encode(service));
  const kSigning = await hmacRaw(new Uint8Array(kService), enc.encode("aws4_request"));
  return kSigning;
}

async function signR2Request(method, canonicalUri, host, keyId, secret, ymd) {
  const dateStamp = ymd.substring(0, 8);
  const credentialScope = `${dateStamp}/auto/s3/aws4_request`;

  const canonicalHeaders = [
    `host:${host}`,
    "x-amz-content-sha256:UNSIGNED-PAYLOAD",
    `x-amz-date:${ymd}`,
  ].join("\n") + "\n";

  const signedHeaders = "host;x-amz-content-sha256;x-amz-date";

  const canonicalRequest = [
    method,
    canonicalUri,
    "",
    canonicalHeaders,
    signedHeaders,
    "UNSIGNED-PAYLOAD",
  ].join("\n");

  const canonicalHash = await sha256Hex(canonicalRequest);
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    ymd,
    credentialScope,
    canonicalHash,
  ].join("\n");

  const signingKey = await getSignatureKey(secret, dateStamp, "auto", "s3");
  const sig = await hmacRaw(new Uint8Array(signingKey), new TextEncoder().encode(stringToSign));
  const signature = Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, "0")).join("");

  return `AWS4-HMAC-SHA256 Credential=${keyId}/${credentialScope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
}

// ── R2 fetch ───────────────────────────────────────────────────────────────

async function fetchFromR2(path, env) {
  const endpoint = env.R2_ENDPOINT_URL || "https://1fc06dad3c7f42576be40fc6437f8fec.r2.cloudflarestorage.com";
  const bucket = env.R2_BUCKET_NAME || "semeclaw-assets";
  const keyId = env.R2_ACCESS_KEY_ID;
  const secret = env.R2_SECRET_ACCESS_KEY;

  if (!keyId || !secret) throw new Error("R2 credentials not configured");

  const host = endpoint.replace("https://", "");
  const canonicalUri = "/" + bucket + "/" + path;
  const now = new Date();
  const ymd = now.toISOString().replace(/[:-]/g, "").split(".")[0] + "Z";

  const auth = await signR2Request("GET", canonicalUri, host, keyId, secret, ymd);
  const url = "https://" + host + canonicalUri;

  return fetch(url, {
    method: "GET",
    headers: {
      Authorization: auth,
      "x-amz-date": ymd,
      "x-amz-content-sha256": "UNSIGNED-PAYLOAD",
    },
  });
}

// ── Main handler ───────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\//, "");

    if (request.method === "OPTIONS") {
      const h = new Headers();
      addCors(h);
      return new Response(null, { status: 204, headers: h });
    }

    if (!path || path === "/") {
      return jsonResponse({
        service: "updates.semeclaw.com",
        version: "1.0",
        endpoints: ["GET /manifest.json", "GET /ads/:filename", "GET /health"],
      });
    }

    if (path === "health") {
      return jsonResponse({ status: "ok", version: "1.0" });
    }

    let res;
    try {
      res = await fetchFromR2(path, env);
    } catch (e) {
      console.error("R2 fetch error:", e);
      return jsonResponse({ error: "Upstream error", detail: e.message }, 502);
    }

    if (!res.ok) {
      return jsonResponse({ error: "Not found in R2", path, status: res.status }, 404);
    }

    const isMp3 = path.endsWith(".mp3");
    const isJson = path.endsWith(".json");
    let contentType = "application/octet-stream";
    if (isMp3) contentType = "audio/mpeg";
    if (isJson) contentType = "application/json";

    const h = new Headers();
    h.set("Content-Type", contentType);
    h.set("Cache-Control", path.startsWith("ads/") ? "public, max-age=86400" : "public, max-age=3600");
    h.set("X-SemeClaw-Update-Server", "1.0");
    addCors(h);

    res.headers.forEach((val, key) => {
      if (["content-length", "etag", "last-modified"].includes(key.toLowerCase())) {
        h.set(key, val);
      }
    });

    return new Response(res.body, { headers: h });
  },
};
