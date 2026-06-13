const API_BASE = "/api";

async function apiFetch(path, options = {}) {
  const token = localStorage.getItem("cm_token");
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(API_BASE + path, { ...options, headers });
  const data = await res.json();

  if (res.status === 401) {
    localStorage.removeItem("cm_token");
    if (!window.location.pathname.startsWith("/auth") && !window.location.pathname.startsWith("/admin")) {
      window.location.href = "/auth";
    }
    throw new Error("Session expired. Please log in again.");
  }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const api = {
  // Auth
  register:     (body)               => apiFetch("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  login:        (body)               => apiFetch("/auth/login",    { method: "POST", body: JSON.stringify(body) }),
  logout:       ()                   => apiFetch("/auth/logout",   { method: "POST" }),
  me:           ()                   => apiFetch("/auth/me"),

  // Public
  status:       ()                   => apiFetch("/status"),

  // Applicant
  apply:        (body)               => apiFetch("/apply",         { method: "POST", body: JSON.stringify(body) }),
  getResult:    (id)                 => apiFetch(`/result/${id}`),

  // Admin
  adminList:    (params = "")        => apiFetch(`/admin/applicants${params}`),
  stats:        ()                   => apiFetch("/admin/stats"),
  adminConfig:  ()                   => apiFetch("/admin/config"),
  announce:     ()                   => apiFetch("/admin/announce", { method: "POST" }),
  updateQuota:  (quota)              => apiFetch("/admin/quota",    { method: "POST", body: JSON.stringify({ quota }) }),
  disqualify:   (id, reason)         => apiFetch(`/admin/disqualify/${id}`, { method: "POST", body: JSON.stringify({ reason }) }),
  override:     (id, reason, status) => apiFetch(`/admin/override/${id}`,  { method: "POST", body: JSON.stringify({ reason, status }) }),
  resolveAppeal:(id, note)           => apiFetch(`/admin/appeal/${id}/resolve`, { method: "POST", body: JSON.stringify({ note }) }),
  exportCSV:    ()                   => window.open(API_BASE + "/admin/export"),
  importCSV:    (file) => {
    const fd = new FormData();
    fd.append("file", file);
    const token = localStorage.getItem("cm_token");
    const headers = token ? { "Authorization": `Bearer ${token}` } : {};
    return fetch(API_BASE + "/admin/import", { method: "POST", headers, body: fd })
      .then(res => res.json().then(data => { if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`); return data; }));
  },
  adminLogin:   (password)           => apiFetch("/admin/login",   { method: "POST", body: JSON.stringify({ password }) }),
  resetApps:    ()                   => apiFetch("/admin/reset",   { method: "POST" }),
};

function statusBadge(status) {
  const map = {
    qualified:     ["badge-teal",   "Qualified"],
    waiting_list:  ["badge-orange", "Waiting List"],
    rejected:      ["badge-red",    "Rejected"],
    disqualified:  ["badge-orange", "Disqualified"],
    pending:       ["badge-gray",   "Pending"],
  };
  const [cls, label] = map[status] || ["badge-gray", status];
  return `<span class="badge ${cls}">${label}</span>`;
}

function catClass(cat) {
  return {
    kemiskinan:    "cat-kemiskinan",
    tanggungan:    "cat-tanggungan",
    infrastruktur: "cat-infrastruktur",
    sosial:        "cat-sosial",
    prestasi:      "cat-prestasi",
  }[cat] || "";
}

function catLabel(cat) {
  return {
    kemiskinan:    "A — Poverty",
    tanggungan:    "B — Dependents",
    infrastruktur: "C — Infrastructure & Economy",
    sosial:        "D — Social Conditions",
    prestasi:      "E — Academic Achievement",
  }[cat] || cat;
}
