const COOKIE_NAME = "__Host-qingzhi_session";
const SESSION_SECONDS = 12 * 60 * 60;
const encoder = new TextEncoder();

export const privateHeaders = {
  "Cache-Control": "no-store",
  "Referrer-Policy": "same-origin",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

function hex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sessionKey(password: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw", encoder.encode(password), { name: "HMAC", hash: "SHA-256" }, false,
    ["sign", "verify"],
  );
}

export function passwordConfigured(password: string | undefined): password is string {
  return typeof password === "string" && password.length >= 24;
}

export async function passwordMatches(candidate: string, password: string): Promise<boolean> {
  // Compare fixed-size digests without an early-exit string comparison.
  const [left, right] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(candidate)),
    crypto.subtle.digest("SHA-256", encoder.encode(password)),
  ]);
  const a = new Uint8Array(left);
  const b = new Uint8Array(right);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

export async function createSessionCookie(password: string, now = Date.now()): Promise<string> {
  const issued = Math.floor(now / 1000);
  const nonce = hex(crypto.getRandomValues(new Uint8Array(16)).buffer);
  const payload = `v1.${issued}.${issued + SESSION_SECONDS}.${nonce}`;
  const signature = hex(await crypto.subtle.sign("HMAC", await sessionKey(password), encoder.encode(payload)));
  return `${COOKIE_NAME}=${payload}.${signature}; Path=/; Max-Age=${SESSION_SECONDS}; HttpOnly; Secure; SameSite=Strict`;
}

export function clearSessionCookie(): string {
  return `${COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict`;
}

export async function hasValidSession(request: Request, password: string, now = Date.now()): Promise<boolean> {
  const cookies = (request.headers.get("Cookie") ?? "").split(";");
  const token = cookies.map((c) => c.trim()).find((c) => c.startsWith(`${COOKIE_NAME}=`))?.slice(COOKIE_NAME.length + 1);
  if (!token || token.length > 256) return false;
  const match = /^v1\.(\d{1,12})\.(\d{1,12})\.([a-f0-9]{32})\.([a-f0-9]{64})$/.exec(token);
  if (!match) return false;
  const [, issued, expires, , signature] = match;
  const seconds = Math.floor(now / 1000);
  if (+issued > seconds || +expires <= seconds || +expires - +issued !== SESSION_SECONDS) return false;
  const bytes = Uint8Array.from(signature.match(/../g)!, (s) => parseInt(s, 16));
  return crypto.subtle.verify("HMAC", await sessionKey(password), bytes, encoder.encode(token.slice(0, token.lastIndexOf("."))));
}

export function sameOrigin(request: Request): boolean {
  return request.headers.get("Origin") === new URL(request.url).origin;
}

export function loginPage(error = false, status = 200): Response {
  return new Response(`<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>青智焕新 · 登录</title>
<style>html{color-scheme:light}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#f3f7f5;color:#12362c;font:16px/1.6 system-ui,sans-serif}main{width:min(100%,420px);background:white;padding:40px;border:1px solid #d5e4dc;border-radius:20px}h1{font-size:30px;margin:0 0 8px}p{color:#557367}label{display:block;margin-top:24px}input,button{width:100%;padding:13px 16px;border-radius:9px;font:inherit}input{margin:8px 0 18px;border:1px solid #a9c5b7}button{background:#14694d;color:white;border:0;cursor:pointer}small{display:block;color:#557367;margin-top:24px}.error{color:#a12020}</style></head>
<body><main><h1>青智焕新</h1><p>你的 AI 内容创作空间<br>Your AI creation workspace</p>
${error ? '<p class="error" role="alert">密码不正确，请重试。<br>Incorrect password. Please try again.</p>' : ""}
<form method="post" action="/_auth/login"><label for="password">访问密码 / Access password</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="1024"><button type="submit">进入工作室 / Open studio</button></form>
<small>仅供可信使用者访问。<br>For the trusted workspace owner.</small></main></body></html>`, {
    status,
    headers: {
      ...privateHeaders,
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    },
  });
}
