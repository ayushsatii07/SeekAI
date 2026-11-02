export const dynamic = "force-dynamic";

async function proxyRequest(request) {
  const backendBase =
    process.env.APP_URL ||
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";

  const { pathname, search } = new URL(request.url);
  // Strip /api prefix to match FastAPI route paths
  const subpath = pathname.replace(/^\/api\/?/, "") || "";
  const baseWithSlash = backendBase.endsWith("/") ? backendBase : `${backendBase}/`;
  const targetUrl = new URL(subpath + search, baseWithSlash);

  const headers = new Headers(request.headers);
  headers.delete("host");

  const options = {
    method: request.method,
    headers,
    cache: "no-store",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    options.body = request.body;
    // duplex: 'half' is required in Node.js / Vercel Edge for streaming request bodies
    options.duplex = "half";
  }

  try {
    const response = await fetch(targetUrl, options);
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  } catch (error) {
    return new Response(
      JSON.stringify({
        detail: "Failed to connect to internal backend service.",
        error: error.message,
      }),
      {
        status: 502,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
}

export async function GET(request) {
  return proxyRequest(request);
}

export async function POST(request) {
  return proxyRequest(request);
}

export async function PUT(request) {
  return proxyRequest(request);
}

export async function DELETE(request) {
  return proxyRequest(request);
}

export async function PATCH(request) {
  return proxyRequest(request);
}

export async function HEAD(request) {
  return proxyRequest(request);
}

export async function OPTIONS(request) {
  return proxyRequest(request);
}

