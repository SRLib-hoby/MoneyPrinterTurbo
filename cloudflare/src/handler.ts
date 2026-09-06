import {
  clearSessionCookie, createSessionCookie, hasValidSession, loginPage,
  passwordConfigured, passwordMatches, privateHeaders, sameOrigin,
} from "./auth";

export interface ProxyEnvironment {
  APP_ACCESS_PASSWORD?: string;
  QINGZHI_CONTAINER: { getByName(name: string): { fetch(request: Request): Promise<Response> } };
}

function message(body: string, status: number): Response {
  return new Response(body, { status, headers: { ...privateHeaders, "Content-Type": "text/plain; charset=utf-8" } });
}

function redirect(path: string, cookie?: string): Response {
  return new Response(null, {
    status: 303,
    headers: { ...privateHeaders, Location: path, ...(cookie ? { "Set-Cookie": cookie } : {}) },
  });
}

async function readSmallBody(request: Request): Promise<string | null> {
  if (Number(request.headers.get("Content-Length")) > 4096) return null;
  const reader = request.body?.getReader();
  if (!reader) return "";
  let size = 0;
  const chunks: Uint8Array[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 4096) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return new TextDecoder().decode(bytes);
}

export async function handleRequest(request: Request, env: ProxyEnvironment): Promise<Response> {
  if (!passwordConfigured(env.APP_ACCESS_PASSWORD)) {
    return message("服务尚未完成安全配置。请管理员设置至少 24 位的 APP_ACCESS_PASSWORD。\nSetup incomplete: set APP_ACCESS_PASSWORD to at least 24 characters.", 503);
  }
  const url = new URL(request.url);
  // Session cookies require HTTPS; wrangler dev uses --local-protocol=https.
  if (url.protocol !== "https:") return message("请使用 HTTPS。 / HTTPS is required.", 400);

  if (url.pathname === "/_auth/login") {
    if (request.method === "GET") return loginPage();
    if (request.method !== "POST") return message("Method not allowed", 405);
    if (!sameOrigin(request)) return message("Forbidden", 403);
    if (!request.headers.get("Content-Type")?.toLowerCase().startsWith("application/x-www-form-urlencoded")) {
      return message("Unsupported form", 415);
    }
    const body = await readSmallBody(request);
    if (body === null) return message("Request too large", 413);
    const candidate = new URLSearchParams(body).get("password") ?? "";
    if (candidate.length > 1024 || !await passwordMatches(candidate, env.APP_ACCESS_PASSWORD)) return loginPage(true, 401);
    return redirect("/", await createSessionCookie(env.APP_ACCESS_PASSWORD));
  }

  const authenticated = await hasValidSession(request, env.APP_ACCESS_PASSWORD);
  if (url.pathname === "/_auth/logout") {
    if (request.method === "GET") {
      if (!authenticated) return redirect("/_auth/login");
      return new Response('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>退出 / Sign out</title><form action="/_auth/logout" method="post"><button>退出青智焕新 / Sign out</button></form></html>', {
        headers: { ...privateHeaders, "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'" },
      });
    }
    if (request.method !== "POST") return message("Method not allowed", 405);
    if (!sameOrigin(request)) return message("Forbidden", 403);
    return redirect("/_auth/login", clearSessionCookie());
  }

  const isWebSocket = request.headers.get("Upgrade")?.toLowerCase() === "websocket";
  if (!authenticated) {
    if (!isWebSocket && request.method === "GET" && request.headers.get("Accept")?.includes("text/html")) return redirect("/_auth/login");
    return message("请先登录。 / Sign in first.", 401);
  }
  // Browser WebSockets carry cookies; reject other origins before contacting the app.
  if (isWebSocket && !sameOrigin(request)) return message("Forbidden", 403);
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method) && !sameOrigin(request)) return message("Forbidden", 403);

  const headers = new Headers(request.headers);
  // The login cookie and any caller-supplied proxy identity are not application data.
  const appCookies = (headers.get("Cookie") ?? "").split(";").filter((c) => !c.trim().startsWith("__Host-qingzhi_session=")).join(";");
  if (appCookies.trim()) headers.set("Cookie", appCookies); else headers.delete("Cookie");
  for (const name of ["Authorization", "cf-container-target-port", "Forwarded", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Forwarded-For"]) headers.delete(name);
  headers.set("X-Forwarded-Host", url.host);
  headers.set("X-Forwarded-Proto", "https");
  try {
    const response = await env.QINGZHI_CONTAINER.getByName("qingzhi-owner").fetch(new Request(request, { headers }));
    // Returning the original response preserves Cloudflare's WebSocket upgrade.
    if (response.status === 101) return response;
    const responseHeaders = new Headers(response.headers);
    for (const [name, value] of Object.entries(privateHeaders)) responseHeaders.set(name, value);
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers: responseHeaders });
  } catch {
    return message("工作室正在启动或暂时不可用，请稍后刷新。\nThe studio is starting or temporarily unavailable. Please retry shortly.", 503);
  }
}
