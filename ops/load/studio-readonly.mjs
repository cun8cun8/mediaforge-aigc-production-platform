import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = (__ENV.MEDIAFORGE_LOAD_BASE_URL || "http://127.0.0.1:8020").replace(/\/$/, "");
const token = __ENV.MEDIAFORGE_LOAD_TOKEN || "";
const projectId = __ENV.MEDIAFORGE_LOAD_PROJECT_ID || "";
const maxP95 = Number(__ENV.MEDIAFORGE_LOAD_MAX_P95_MS || "1000");
const failureRate = Number(__ENV.MEDIAFORGE_LOAD_MAX_FAILURE_RATE || "0.01");
const headers = token ? { Authorization: `Bearer ${token}` } : {};

export const options = {
  scenarios: {
    studio_readonly: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: __ENV.MEDIAFORGE_LOAD_RAMP_UP || "30s", target: Number(__ENV.MEDIAFORGE_LOAD_VUS || "10") },
        { duration: __ENV.MEDIAFORGE_LOAD_HOLD || "2m", target: Number(__ENV.MEDIAFORGE_LOAD_VUS || "10") },
        { duration: __ENV.MEDIAFORGE_LOAD_RAMP_DOWN || "30s", target: 0 },
      ],
      gracefulRampDown: "30s",
    },
  },
  thresholds: {
    http_req_failed: [`rate<${failureRate}`],
    http_req_duration: [`p(95)<${maxP95}`],
    checks: ["rate>0.99"],
  },
};

function get(path, name) {
  return http.get(`${baseUrl}${path}`, { headers, tags: { name } });
}

export default function () {
  const livez = get("/livez", "livez");
  check(livez, { "livez returns 200": (response) => response.status === 200 });

  const health = get("/health", "health");
  check(health, { "traffic endpoint is ready": (response) => response.status === 200 });

  const provider = get("/providers/diagnostics", "provider_diagnostics");
  check(provider, {
    "provider diagnostics returns 200": (response) => response.status === 200,
    "provider diagnostics is JSON": (response) => String(response.headers["Content-Type"] || "").includes("application/json"),
  });

  const readiness = get("/ops/readiness", "production_readiness");
  check(readiness, { "production readiness returns 200": (response) => response.status === 200 });

  if (projectId) {
    const project = get(`/projects/${encodeURIComponent(projectId)}`, "project_view");
    check(project, { "configured project is readable": (response) => response.status === 200 });
  }
  sleep(Number(__ENV.MEDIAFORGE_LOAD_THINK_TIME_SECONDS || "1"));
}
