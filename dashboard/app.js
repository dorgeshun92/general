/* Mpire audit console — plain JS, no build step.
 * Every piece of API text reaches the DOM through textContent (the el() helper);
 * innerHTML is never used. Writes are disabled in preview mode. */
(function () {
  "use strict";

  // ------------------------------------------------------------------ helpers
  function el(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = v;
        else if (k === "html") throw new Error("innerHTML is not allowed");
        else if (k.indexOf("on") === 0 && typeof v === "function") node.addEventListener(k.slice(2), v);
        else if (k === "dataset") Object.keys(v).forEach(function (d) { node.dataset[d] = v[d]; });
        else node.setAttribute(k, v === true ? "" : String(v));
      });
    }
    for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  }
  function append(node, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(node, c); }); return; }
    if (typeof child === "string" || typeof child === "number") node.appendChild(document.createTextNode(String(child)));
    else node.appendChild(child);
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function fmtDate(iso) {
    if (!iso) return "—";
    return String(iso).replace("T", " ").replace(/\.\d+/, "").replace(/Z$/, " UTC");
  }
  function shortSha(s) { return s ? String(s).slice(0, 12) : "—"; }
  function orDash(v) { return v === null || v === undefined || v === "" ? "—" : String(v); }
  function yesNo(v) { return v === true ? "yes" : v === false ? "no" : "unknown"; }
  function runHref(loan, run, tab) { return "#/runs/" + encodeURIComponent(loan) + "/" + encodeURIComponent(run) + (tab ? "?tab=" + tab : ""); }
  function isPlainObject(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }

  var RESULTS = ["PASS", "FAIL", "MISSING", "REVIEW", "NOT_APPLICABLE"];
  var ROLES = ["LOAN_OFFICER", "PROCESSOR", "UNDERWRITER", "COMPLIANCE", "MANAGEMENT"];
  var EVAL_TARGETS = [
    ["classification", "Document classification accuracy", ">= 98%"],
    ["coverage", "Checklist coverage", "100% of expected rules present"],
    ["false_pass", "False PASS rate on blocking items", "0 occurrences"],
    ["calculation", "Calculation agreement", "100% on expected outputs"],
    ["evidence", "Evidence citation accuracy", ">= 99%"],
    ["pii", "PII leakage in reports/logs", "0 occurrences"],
    ["stop", "Correct stop/escalation behavior", "100% on designed edge cases"],
    ["schema", "Schema validity of produced outputs", "0 failures"]
  ];

  function statusPill(status) {
    var s = status || "NONE";
    return el("span", { class: "pill pill-" + s, text: status || "no gate" });
  }
  function resultChip(result) { return el("span", { class: "res res-" + result, text: result }); }
  function blockingPill(b) {
    return el("span", { class: "pill " + (b ? "pill-blocking" : "pill-advisory"), text: b ? "blocking" : "advisory" });
  }
  function tag(value) { return el("span", { class: "tag tag-" + String(value), text: String(value) }); }
  function loading(msg) { return el("div", { class: "loading", text: msg || "Loading…" }); }
  function emptyState(msg) { return el("div", { class: "empty", text: msg }); }
  function errorBox(err) {
    var msg = err && err.message ? err.message : String(err);
    return el("div", { class: "notice notice-error", role: "alert" }, el("strong", { text: "Request failed. " }), msg);
  }
  function dl(pairs) {
    var d = el("dl", { class: "dl" });
    pairs.forEach(function (p) {
      if (p[1] === undefined) return;
      d.appendChild(el("dt", { text: p[0] }));
      var dd = el("dd");
      append(dd, p[1]);
      d.appendChild(dd);
    });
    return d;
  }
  function valueNode(v) {
    if (v === null || v === undefined) return el("span", { class: "muted", text: "null" });
    if (Array.isArray(v)) {
      if (!v.length) return el("span", { class: "muted", text: "none" });
      if (v.every(function (x) { return typeof x !== "object"; })) return el("span", { class: "mono", text: v.join(", ") });
      return el("ul", { class: "limits" }, v.map(function (x) { return el("li", {}, valueNode(x)); }));
    }
    if (isPlainObject(v)) return dl(Object.keys(v).map(function (k) { return [k, valueNode(v[k])]; }));
    if (typeof v === "boolean") return el("span", { text: v ? "true" : "false" });
    return el("span", { class: typeof v === "number" || /^-?\d+(\.\d+)?$/.test(String(v)) ? "mono" : "", text: String(v) });
  }
  function table(columns, rows, rowFn) {
    var thead = el("thead", {}, el("tr", {}, columns.map(function (c) {
      return el("th", { class: c.num ? "num" : null, scope: "col", text: c.label });
    })));
    var tbody = el("tbody");
    rows.forEach(function (r, i) { append(tbody, rowFn(r, i)); });
    return el("div", { class: "table-wrap" }, el("table", {}, thead, tbody));
  }

  // ------------------------------------------------------------------ API
  function ApiError(message, status) { this.message = message; this.status = status; this.name = "ApiError"; }
  ApiError.prototype = Object.create(Error.prototype);

  function HttpApi(base, tokenFn, onUnauthorized) {
    this.base = base || "/api"; this.tokenFn = tokenFn; this.onUnauthorized = onUnauthorized; this.preview = false;
  }
  HttpApi.prototype.request = function (method, path, opts) {
    opts = opts || {};
    var url = this.base + path;
    if (opts.params) {
      var q = Object.keys(opts.params).filter(function (k) { return opts.params[k] !== undefined && opts.params[k] !== null && opts.params[k] !== ""; })
        .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(opts.params[k]); }).join("&");
      if (q) url += (url.indexOf("?") >= 0 ? "&" : "?") + q;
    }
    var headers = { Accept: opts.text ? "text/markdown, text/plain" : "application/json" };
    var token = this.tokenFn ? this.tokenFn() : null;
    if (token) headers.Authorization = "Bearer " + token;
    var init = { method: method, headers: headers };
    if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    var self = this;
    return fetch(url, init).then(function (res) {
      if (res.status === 401) { if (self.onUnauthorized) self.onUnauthorized(); throw new ApiError("Not signed in (401). Sign in again.", 401); }
      if (opts.text) return res.text().then(function (t) { if (!res.ok) throw new ApiError(extractDetail(t) || ("HTTP " + res.status), res.status); return t; });
      return res.text().then(function (t) {
        var data = null;
        if (t) { try { data = JSON.parse(t); } catch (e) { data = null; } }
        if (!res.ok) throw new ApiError((data && data.detail) ? detailText(data.detail) : ("HTTP " + res.status), res.status);
        return data;
      });
    }, function (e) { throw new ApiError("Network error: " + (e && e.message ? e.message : e), 0); });
  };
  function extractDetail(t) { try { var d = JSON.parse(t); return d && d.detail ? detailText(d.detail) : null; } catch (e) { return null; } }
  function detailText(d) {
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map(function (x) { return x && x.msg ? ((x.loc || []).join(".") + ": " + x.msg) : JSON.stringify(x); }).join("; ");
    return JSON.stringify(d);
  }
  HttpApi.prototype.get = function (p, params) { return this.request("GET", p, { params: params }); };
  HttpApi.prototype.getText = function (p) { return this.request("GET", p, { text: true }); };
  HttpApi.prototype.post = function (p, body) { return this.request("POST", p, { body: body }); };
  HttpApi.prototype.patch = function (p, body) { return this.request("PATCH", p, { body: body }); };

  /* Preview client: serves window.MPIRE_DEMO_DATA through the same route table. Read-only. */
  function DemoApi(data) { this.data = data; this.preview = true; }
  DemoApi.prototype._detail = function (loan, run) {
    var d = this.data.runs.filter(function (x) { return x.bundle.loan.loan_id === loan && x.bundle.run.run_id === run; })[0];
    if (!d) throw new ApiError("run " + loan + "/" + run + " not found", 404);
    return d;
  };
  DemoApi.prototype.get = function (path, params) {
    var self = this, data = this.data, m;
    params = params || {};
    return new Promise(function (resolve, reject) {
      try {
        if (path === "/health") return resolve(data.health);
        if (path === "/summary") return resolve(data.summary());
        if (path === "/loans") return resolve(data.runs.map(function (d) { return Object.assign({}, d.bundle.loan, { latest_run: d.bundle.run }); }));
        if (path === "/runs/latest" || path === "/runs") return resolve(data.runs.map(function (d) { return d.bundle.run; }));
        if (path === "/eval/latest") return resolve(data.eval_latest);
        if (path === "/review-queue") {
          return resolve(data.review_queue().filter(function (e) {
            return (!params.reviewer_role || e.reviewer_role === params.reviewer_role) && (!params.loan_id || e.loan_id === params.loan_id);
          }));
        }
        if (path === "/run-requests") return resolve(data.run_requests.filter(function (r) { return !params.status || r.status === params.status; }));
        if (path === "/findings/search") {
          var q = String(params.q || "").toLowerCase(), out = [];
          data.runs.forEach(function (d) { d.bundle.findings.forEach(function (f) {
            var hay = [f.rule_id, f.explanation, f.discrepancy, f.proposed_action, f.review_reason].join(" ").toLowerCase();
            if (q && hay.indexOf(q) >= 0) out.push(f);
          }); });
          return resolve(out.slice(0, Number(params.limit || 50)));
        }
        if ((m = path.match(/^\/loans\/([^/]+)\/runs$/))) {
          return resolve(data.runs.filter(function (d) { return d.bundle.loan.loan_id === decodeURIComponent(m[1]); }).map(function (d) { return d.bundle.run; }));
        }
        if ((m = path.match(/^\/loans\/([^/]+)$/))) {
          var l = data.runs.filter(function (d) { return d.bundle.loan.loan_id === decodeURIComponent(m[1]); })[0];
          if (!l) throw new ApiError("loan not found", 404);
          return resolve(l.bundle.loan);
        }
        if ((m = path.match(/^\/runs\/([^/]+)\/([^/]+)$/))) return resolve(self._detail(decodeURIComponent(m[1]), decodeURIComponent(m[2])));
        if ((m = path.match(/^\/runs\/([^/]+)\/([^/]+)\/findings$/))) {
          var fs = self._detail(decodeURIComponent(m[1]), decodeURIComponent(m[2])).bundle.findings.filter(function (f) {
            return (!params.audit_type || f.audit_type === params.audit_type) && (!params.result || f.result === params.result) &&
              (params.blocking === undefined || params.blocking === "" || String(f.blocking) === String(params.blocking)) &&
              (!params.rule_id || f.rule_id === params.rule_id);
          });
          return resolve(fs);
        }
        if ((m = path.match(/^\/runs\/([^/]+)\/([^/]+)\/documents$/))) return resolve(self._detail(decodeURIComponent(m[1]), decodeURIComponent(m[2])).bundle.documents);
        if ((m = path.match(/^\/runs\/([^/]+)\/([^/]+)\/reports$/))) {
          return resolve(self._detail(decodeURIComponent(m[1]), decodeURIComponent(m[2])).bundle.reports.map(function (r) { return { name: r.name, sha256: r.sha256 }; }));
        }
        throw new ApiError("preview: no route for " + path, 404);
      } catch (e) { reject(e); }
    });
  };
  DemoApi.prototype.getText = function (path) {
    var self = this, m = path.match(/^\/runs\/([^/]+)\/([^/]+)\/reports\/(.+)$/);
    return new Promise(function (resolve, reject) {
      try {
        if (!m) throw new ApiError("preview: no route for " + path, 404);
        var r = self._detail(decodeURIComponent(m[1]), decodeURIComponent(m[2])).bundle.reports.filter(function (x) { return x.name === decodeURIComponent(m[3]); })[0];
        if (!r) throw new ApiError("report not found", 404);
        resolve(r.content_md);
      } catch (e) { reject(e); }
    });
  };
  DemoApi.prototype.post = DemoApi.prototype.patch = function () {
    return Promise.reject(new ApiError("Preview mode is read-only: not connected to the API.", 0));
  };

  // ------------------------------------------------------------------ state + boot
  var CFG = window.MPIRE_CONFIG || null;
  var state = { mode: "booting", api: null, supa: null, session: null, user: null, counts: {} };
  var PREVIEW_NOTE = "Preview mode: sample data only, not connected to the API";

  var $main = document.getElementById("main");
  var $previewBanner = document.getElementById("preview-banner");
  var $conn = document.getElementById("conn");
  var $signout = document.getElementById("signout");

  function setConn(kind, label) {
    $conn.className = "conn " + (kind === "live" ? "is-live" : kind === "preview" ? "is-preview" : "");
    clear($conn);
    $conn.appendChild(el("span", { class: "dot", "aria-hidden": "true" }));
    $conn.appendChild(el("span", { class: "who", text: label }));
  }

  function enterPreview(reason) {
    state.mode = "preview";
    state.api = new DemoApi(window.MPIRE_DEMO_DATA);
    $previewBanner.hidden = false;
    setConn("preview", "Preview — sample data" + (reason ? " (" + reason + ")" : ""));
    $signout.hidden = true;
  }

  function boot() {
    if (!CFG || !CFG.apiBase) { enterPreview("config.js not loaded"); return route(); }
    var base = CFG.apiBase || "/api";
    if (CFG.demo || CFG.backend === "memory" || CFG.backend !== "supabase") {
      state.api = new HttpApi(base, null, null);
      return state.api.get("/health").then(function (h) {
        state.mode = "memory";
        setConn("live", "Connected · " + (h && h.backend ? h.backend : "api") + (h && h.demo ? " (demo data)" : ""));
        $signout.hidden = true;
        route();
      }, function () { enterPreview("API unreachable"); route(); });
    }
    // supabase
    if (!window.supabase || !window.supabase.createClient || !CFG.supabaseUrl || !CFG.supabaseAnonKey) {
      enterPreview("auth library or config missing"); return route();
    }
    state.supa = window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey);
    state.api = new HttpApi(base, function () { return state.session ? state.session.access_token : null; }, function () {
      state.session = null; state.user = null; renderSignIn("Your session expired or was rejected (401). Sign in again.");
    });
    return state.api.get("/health").then(function (h) {
      state.mode = "supabase";
      return state.supa.auth.getSession().then(function (r) {
        applySession(r && r.data ? r.data.session : null);
        state.supa.auth.onAuthStateChange(function (_event, session) { applySession(session); route(); });
        route();
      });
    }, function () { enterPreview("API unreachable"); route(); });
  }

  function applySession(session) {
    state.session = session || null;
    state.user = session && session.user ? session.user : null;
    if (state.user) { setConn("live", state.user.email || "signed in"); $signout.hidden = false; }
    else { setConn("", "Not signed in"); $signout.hidden = true; }
  }

  $signout.addEventListener("click", function () {
    if (state.supa) state.supa.auth.signOut().then(function () { applySession(null); route(); });
  });

  function renderSignIn(message) {
    clear($main);
    var msg = el("div", { class: "notice notice-info", hidden: !message, text: message || "" });
    var email = el("input", { class: "input", type: "email", name: "email", autocomplete: "username", required: true });
    var pw = el("input", { class: "input", type: "password", name: "password", autocomplete: "current-password" });
    var submit = el("button", { class: "btn btn-primary", type: "submit", text: "Sign in" });
    var magic = el("button", { class: "btn", type: "button", text: "Send magic link" });
    var form = el("form", {
      onsubmit: function (ev) {
        ev.preventDefault();
        submit.disabled = true;
        state.supa.auth.signInWithPassword({ email: email.value, password: pw.value }).then(function (r) {
          submit.disabled = false;
          if (r.error) { msg.hidden = false; msg.textContent = r.error.message; return; }
          applySession(r.data.session); route();
        });
      }
    },
      el("label", { class: "field" }, el("span", { text: "Email" }), email),
      el("label", { class: "field" }, el("span", { text: "Password" }), pw),
      el("div", { class: "toolbar" }, submit, magic));
    magic.addEventListener("click", function () {
      if (!email.value) { msg.hidden = false; msg.textContent = "Enter your email first, then send the magic link."; return; }
      magic.disabled = true;
      state.supa.auth.signInWithOtp({ email: email.value, options: { emailRedirectTo: location.origin + location.pathname } }).then(function (r) {
        magic.disabled = false; msg.hidden = false;
        msg.textContent = r.error ? r.error.message : "Magic link sent to " + email.value + ". Open it in this browser to finish signing in.";
      });
    });
    $main.appendChild(el("div", { class: "auth" },
      el("div", {}, el("p", { class: "eyebrow", text: "Mpire audit console" }), el("h1", { text: "Sign in" }),
        el("p", { class: "ink-2", text: "Use your Supabase account. The dashboard shows derived, masked audit data only." })),
      msg, form));
  }

  // ------------------------------------------------------------------ theme
  (function themeInit() {
    var btn = document.getElementById("theme-toggle");
    var order = ["system", "light", "dark"];
    var saved = null;
    try { saved = localStorage.getItem("mpire-theme"); } catch (e) { saved = null; }
    var current = order.indexOf(saved) >= 0 ? saved : "system";
    function apply() {
      if (current === "system") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", current);
      btn.textContent = "Theme: " + current;
      btn.setAttribute("aria-label", "Theme: " + current + ". Activate to change.");
    }
    apply();
    btn.addEventListener("click", function () {
      current = order[(order.indexOf(current) + 1) % order.length];
      try { localStorage.setItem("mpire-theme", current); } catch (e) { /* storage unavailable */ }
      apply();
    });
  })();

  // ------------------------------------------------------------------ router
  var ROUTES = [
    { re: /^\/?$/, view: viewOverview, nav: "overview" },
    { re: /^\/overview$/, view: viewOverview, nav: "overview" },
    { re: /^\/loans$/, view: viewLoans, nav: "loans" },
    { re: /^\/runs\/([^/]+)\/([^/]+)$/, view: viewRun, nav: "loans" },
    { re: /^\/review$/, view: viewReviewQueue, nav: "review" },
    { re: /^\/requests$/, view: viewRunRequests, nav: "requests" },
    { re: /^\/search$/, view: viewSearch, nav: "search" }
  ];
  function parseHash() {
    var h = location.hash.replace(/^#/, "") || "/";
    var qi = h.indexOf("?"), path = qi >= 0 ? h.slice(0, qi) : h, query = {};
    if (qi >= 0) h.slice(qi + 1).split("&").forEach(function (kv) {
      if (!kv) return; var p = kv.split("="); query[decodeURIComponent(p[0])] = decodeURIComponent((p[1] || "").replace(/\+/g, " "));
    });
    return { path: path, query: query };
  }
  var routeSeq = 0;
  function route() {
    if (state.mode === "supabase" && !state.session) return renderSignIn(null);
    if (state.mode === "booting") return;
    var loc = parseHash(), matched = null, params = [];
    for (var i = 0; i < ROUTES.length; i++) {
      var m = loc.path.match(ROUTES[i].re);
      if (m) { matched = ROUTES[i]; params = m.slice(1).map(decodeURIComponent); break; }
    }
    document.querySelectorAll(".nav a").forEach(function (a) {
      if (matched && a.dataset.nav === matched.nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    clear($main);
    if (!matched) { $main.appendChild(emptyState("No such page: " + loc.path)); return; }
    var seq = ++routeSeq;
    var container = el("div", { class: "stack" });
    $main.appendChild(container);
    Promise.resolve(matched.view(container, params, loc.query)).catch(function (err) {
      if (seq !== routeSeq) return;
      clear(container); container.appendChild(errorBox(err));
    });
    refreshNavCounts();
  }
  window.addEventListener("hashchange", route);

  function refreshNavCounts() {
    if (!state.api) return;
    state.api.get("/summary").then(function (s) {
      var map = { review: s.review_queue, requests: s.queued_requests, loans: s.loans };
      document.querySelectorAll(".nav a").forEach(function (a) {
        var c = a.querySelector(".count"); if (!c) return;
        var v = map[a.dataset.nav]; c.textContent = v === undefined ? "" : String(v);
      });
    }, function () { /* counts are decorative */ });
  }

  function pageHead(title, lede, right) {
    return el("div", { class: "page-head" }, el("div", {}, el("h1", { text: title }), lede ? el("p", { class: "lede", text: lede }) : null), right || null);
  }
  function writeGuard(node, title) {
    if (state.api && state.api.preview) { node.disabled = true; node.title = title || PREVIEW_NOTE; node.setAttribute("aria-disabled", "true"); }
    return node;
  }

  // ------------------------------------------------------------------ overview
  function viewOverview(root) {
    root.appendChild(pageHead("Overview", "Latest run per loan. Every number here is derived from validated, masked audit output; nothing is a credit or compliance decision."));
    var tilesHost = el("div", {}), chartHost = el("div", {}), evalHost = el("div", {}), limitsHost = el("div", {});
    tilesHost.appendChild(loading());
    root.appendChild(tilesHost);
    root.appendChild(el("div", { class: "grid-2" },
      el("section", { class: "section card", "aria-labelledby": "h-results" },
        el("div", { class: "section-head" }, el("h2", { id: "h-results", text: "Finding results per latest run" }),
          el("span", { class: "muted", text: "one bar per loan, segments = findings" })), chartHost),
      el("section", { class: "section card", "aria-labelledby": "h-limits" },
        el("div", { class: "section-head" }, el("h2", { id: "h-limits", text: "Known limitations" }),
          el("span", { class: "muted", text: "union across latest runs" })), limitsHost)));
    root.appendChild(el("section", { class: "section", "aria-labelledby": "h-eval" },
      el("div", { class: "section-head" }, el("h2", { id: "h-eval", text: "Evaluation targets (Section 10)" }),
        el("span", { class: "muted", text: "latest eval report" })), evalHost));
    chartHost.appendChild(loading()); evalHost.appendChild(loading()); limitsHost.appendChild(loading());

    var api = state.api;
    var p1 = api.get("/summary").then(function (s) {
      clear(tilesHost); tilesHost.appendChild(renderTiles(s));
    }, function (e) { clear(tilesHost); tilesHost.appendChild(errorBox(e)); });
    var p2 = api.get("/runs/latest").then(function (runs) {
      clear(chartHost); clear(limitsHost);
      chartHost.appendChild(renderResultsChart(runs || []));
      var seen = {}, union = [];
      (runs || []).forEach(function (r) { (r.known_limitations || []).forEach(function (l) { if (!seen[l]) { seen[l] = 1; union.push(l); } }); });
      limitsHost.appendChild(union.length ? el("ul", { class: "limits" }, union.map(function (l) { return el("li", { text: l }); })) : emptyState("No limitations reported by the latest runs."));
    }, function (e) { clear(chartHost); clear(limitsHost); chartHost.appendChild(errorBox(e)); limitsHost.appendChild(errorBox(e)); });
    var p3 = api.get("/eval/latest").then(function (rep) {
      clear(evalHost); evalHost.appendChild(renderEval(rep));
    }, function (e) { clear(evalHost); evalHost.appendChild(errorBox(e)); });
    return Promise.all([p1, p2, p3]);
  }

  function renderTiles(s) {
    function tile(label, value, opts) {
      opts = opts || {};
      var t = el("div", { class: "tile" + (opts.hero ? " is-hero" : "") },
        el("div", { class: "label", text: label }),
        el("div", { class: "value", text: String(value === undefined || value === null ? "—" : value) }));
      if (opts.sub) t.appendChild(el("div", { class: "sub" }, opts.swatch ? el("span", { class: "swatch", style: "background:" + opts.swatch }) : null, opts.sub));
      return t;
    }
    return el("div", { class: "tiles", role: "list" },
      tile("Blocking findings open", s.blocking_open, { hero: true, sub: "across latest runs", swatch: "var(--critical)" }),
      tile("Loans", s.loans), tile("Runs", s.runs),
      tile("READY", s.ready, { sub: "submission gate", swatch: "var(--good)" }),
      tile("NOT_READY", s.not_ready, { sub: "submission gate", swatch: "var(--critical)" }),
      tile("HUMAN_REVIEW", s.human_review, { sub: "submission gate", swatch: "var(--warning)" }),
      tile("No gate yet", s.no_gate, { sub: "preapproval only" }),
      tile("Review queue", s.review_queue, { sub: "awaiting a reviewer" }),
      tile("Queued run requests", s.queued_requests, { sub: "run by a human, never automatically" }),
      tile("Pending actions", s.pending_actions, { sub: "DRAFT — approval required" }));
  }

  // stacked horizontal bar, one row per latest run
  var tooltipNode = null;
  function showTooltip(anchor, lines) {
    hideTooltip();
    tooltipNode = el("div", { class: "tooltip", role: "tooltip" });
    lines.forEach(function (l, i) { tooltipNode.appendChild(el("div", {}, i === 0 ? el("strong", { text: l }) : l)); });
    document.body.appendChild(tooltipNode);
    var r = anchor.getBoundingClientRect(), tw = tooltipNode.offsetWidth;
    var left = Math.min(Math.max(8, r.left + r.width / 2 - tw / 2), window.innerWidth - tw - 8);
    tooltipNode.style.left = left + "px";
    tooltipNode.style.top = (r.top - tooltipNode.offsetHeight - 8 > 0 ? r.top - tooltipNode.offsetHeight - 8 : r.bottom + 8) + "px";
  }
  function hideTooltip() { if (tooltipNode && tooltipNode.parentNode) tooltipNode.parentNode.removeChild(tooltipNode); tooltipNode = null; }

  function renderResultsChart(runs) {
    var withCounts = runs.filter(function (r) { return r.counts; });
    if (!withCounts.length) return emptyState("No runs with finding counts yet.");
    var chart = el("div", { class: "chart" });
    var rows = el("div", { class: "chart-rows", role: "img", "aria-label": chartAltText(withCounts) });
    withCounts.forEach(function (r) {
      var total = RESULTS.reduce(function (a, k) { return a + (r.counts[k] || 0); }, 0);
      rows.appendChild(el("span", { class: "rlabel", text: r.loan_id }));
      var bar = el("div", { class: "bar" });
      RESULTS.forEach(function (k) {
        var n = r.counts[k] || 0; if (!n) return;
        var seg = el("button", { class: "seg seg-" + k, type: "button", style: "flex:" + n + " 1 0",
          "aria-label": k + ": " + n + " of " + total + " findings in " + r.loan_id + " " + r.run_id });
        if (n / total >= 0.18) seg.appendChild(el("span", { class: "seg-label", text: String(n) }));
        var lines = [n + " " + k, r.loan_id + " · " + r.run_id, "of " + total + " findings"];
        seg.addEventListener("pointerenter", function () { showTooltip(seg, lines); });
        seg.addEventListener("focus", function () { showTooltip(seg, lines); });
        seg.addEventListener("pointerleave", hideTooltip);
        seg.addEventListener("blur", hideTooltip);
        seg.addEventListener("click", function () { location.hash = runHref(r.loan_id, r.run_id, "findings") + "&result=" + k; });
        bar.appendChild(seg);
      });
      rows.appendChild(bar);
      rows.appendChild(el("span", { class: "rtotal", text: total + " findings" }));
    });
    chart.appendChild(rows);
    chart.appendChild(el("div", { class: "legend", "aria-hidden": "true" }, RESULTS.map(function (k) {
      return el("span", { class: "key" }, el("i", { style: "background:var(--s-" + seriesVar(k) + ")" }), k);
    })));
    var tw = el("details", { class: "table-twin" }, el("summary", { text: "Table view" }),
      table([{ label: "Loan" }, { label: "Run" }].concat(RESULTS.map(function (k) { return { label: k, num: true }; })), withCounts, function (r) {
        return el("tr", {}, el("td", { class: "mono", text: r.loan_id }), el("td", { class: "mono", text: r.run_id }),
          RESULTS.map(function (k) { return el("td", { class: "num", text: String(r.counts[k] || 0) }); }));
      }));
    chart.appendChild(tw);
    return chart;
  }
  function seriesVar(k) { return { PASS: "pass", FAIL: "fail", MISSING: "missing", REVIEW: "review", NOT_APPLICABLE: "na" }[k]; }
  function chartAltText(runs) {
    return "Finding results per latest run. " + runs.map(function (r) {
      return r.loan_id + ": " + RESULTS.map(function (k) { return (r.counts[k] || 0) + " " + k; }).join(", ");
    }).join(". ") + ".";
  }

  function renderEval(rep) {
    var head = el("div", { class: "toolbar" });
    if (!rep) {
      return el("div", { class: "stack" }, el("div", { class: "notice", text: "No evaluation report has been uploaded yet. Every target shows as not reported." }),
        table([{ label: "Metric" }, { label: "Target" }, { label: "Observed" }, { label: "Status" }], EVAL_TARGETS, function (t) {
          return el("tr", {}, el("td", { text: t[1] }), el("td", { class: "ink-2", text: t[2] }), el("td", { class: "muted", text: "not reported" }), el("td", {}, el("span", { class: "tag", text: "not reported" })));
        }));
    }
    head.appendChild(rep.all_targets_met ? el("span", { class: "pill pill-READY", text: "all targets met" }) : el("span", { class: "pill pill-NOT_READY", text: "targets missed" }));
    head.appendChild(el("span", { class: "muted", text: "generated " + fmtDate(rep.generated_at) + (rep.eval_id ? " · " + rep.eval_id : "") }));
    var targets = rep.targets || {};
    var keys = EVAL_TARGETS.map(function (t) { return t[0]; });
    Object.keys(targets).forEach(function (k) { if (keys.indexOf(k) < 0) EVAL_TARGETS.push([k, k, "—"]); });
    return el("div", { class: "stack" }, head,
      table([{ label: "Metric" }, { label: "Target" }, { label: "Observed" }, { label: "Status" }], EVAL_TARGETS, function (t) {
        var v = targets[t[0]];
        return el("tr", {}, el("td", { text: t[1] }), el("td", { class: "ink-2", text: t[2] }),
          el("td", { class: v && v.observed ? "mono" : "muted", text: v && v.observed ? String(v.observed) : "not reported" }),
          el("td", {}, v && v.status ? tag(v.status) : el("span", { class: "tag", text: "not reported" })));
      }));
  }

  // ------------------------------------------------------------------ loans
  function viewLoans(root) {
    root.appendChild(pageHead("Loans", "One row per loan with its latest synced run. Open a row for findings, documents, and proposed actions."));
    var host = el("div", {}, loading()); root.appendChild(host);
    return state.api.get("/loans").then(function (loans) {
      clear(host);
      if (!loans || !loans.length) return host.appendChild(emptyState("No loans have been synced yet. Run /mortgage-file-audit and sync the output directory."));
      host.appendChild(table([{ label: "Loan" }, { label: "Description" }, { label: "Latest run" }, { label: "Status" },
        { label: "Blocking open", num: true }, { label: "Coverage", num: true }, { label: "Completed" }], loans, function (l) {
        var r = l.latest_run;
        var link = r ? el("a", { href: runHref(l.loan_id, r.run_id), class: "mono", text: l.loan_id }) : el("span", { class: "mono", text: l.loan_id });
        var tr = el("tr", { class: r ? "is-link" : "" },
          el("td", { class: "nowrap" }, link), el("td", { text: orDash(l.description) }),
          el("td", { class: "mono nowrap", text: r ? r.run_id : "no run yet" }),
          el("td", {}, r ? statusPill(r.overall_status) : el("span", { class: "muted", text: "—" })),
          el("td", { class: "num", text: r ? orDash(r.blocking_open) : "—" }),
          el("td", { class: "num", text: r && r.coverage_percent ? r.coverage_percent + " %" : "—" }),
          el("td", { class: "nowrap", text: r ? fmtDate(r.completed_at) : "—" }));
        if (r) tr.addEventListener("click", function (ev) { if (ev.target.tagName !== "A") location.hash = runHref(l.loan_id, r.run_id); });
        return tr;
      }));
    });
  }

  // ------------------------------------------------------------------ run detail
  var TABS = [
    ["findings", "Findings"], ["missing", "Missing documents"], ["conflicts", "Conflicts"], ["actions", "Proposed actions"],
    ["documents", "Documents"], ["reports", "Reports"], ["manifest", "Manifest"]
  ];
  function viewRun(root, params, query) {
    var loanId = params[0], runId = params[1];
    var host = el("div", {}, loading("Loading run " + runId + "…")); root.appendChild(host);
    return state.api.get("/runs/" + encodeURIComponent(loanId) + "/" + encodeURIComponent(runId)).then(function (detail) {
      clear(host);
      var b = detail.bundle, r = b.run;
      root.insertBefore(pageHead(loanId + " / " + runId, b.loan.description || "",
        el("div", { class: "toolbar" }, el("a", { class: "btn btn-sm", href: "#/loans", text: "All loans" }))), host);
      host.appendChild(el("div", { class: "card run-head" },
        el("div", { class: "toolbar" }, statusPill(r.overall_status),
          r.submission_present ? null : el("span", { class: "muted", text: "no submission gate in this run" }),
          r.preapproval_present ? el("span", { class: "tag", text: "PREAPPROVAL" }) : null,
          r.submission_present ? el("span", { class: "tag", text: "SUBMISSION_READINESS" }) : null),
        el("div", { class: "run-meta" },
          kv("Catalog", (r.catalog_version || "—") + " · reviewed: " + yesNo(r.catalog_reviewed)),
          kv("LOS export", yesNo(r.los_export_present)),
          kv("Completed", fmtDate(r.completed_at) + (r.completed_normally === false ? " (stopped: " + orDash(r.stop_condition) + ")" : "")),
          kv("Blocking open", orDash(r.blocking_open)), kv("Coverage", r.coverage_percent ? r.coverage_percent + " %" : "—"),
          kv("Skill", orDash(r.skill))),
        r.known_limitations && r.known_limitations.length
          ? el("div", {}, el("div", { class: "eyebrow", text: "Known limitations" }), el("ul", { class: "limits" }, r.known_limitations.map(function (l) { return el("li", { text: l }); })))
          : null));

      var counts = {
        findings: b.findings.length, missing: b.missing_documents.length, conflicts: b.conflicts.length,
        actions: b.proposed_actions.length, documents: b.documents.length, reports: b.reports.length, manifest: null
      };
      var current = TABS.some(function (t) { return t[0] === query.tab; }) ? query.tab : "findings";
      var tablist = el("div", { class: "tabs", role: "tablist", "aria-label": "Run sections" });
      var panel = el("div", { role: "tabpanel", id: "tabpanel" });
      TABS.forEach(function (t) {
        var btn = el("button", { role: "tab", type: "button", id: "tab-" + t[0], "aria-selected": String(t[0] === current), "aria-controls": "tabpanel", tabindex: t[0] === current ? "0" : "-1" }, t[1],
          counts[t[0]] !== null ? el("span", { class: "count", text: String(counts[t[0]]) }) : null);
        btn.addEventListener("click", function () { selectTab(t[0]); });
        btn.addEventListener("keydown", function (ev) {
          var idx = TABS.findIndex(function (x) { return x[0] === t[0]; });
          if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
            ev.preventDefault(); var n = TABS[(idx + (ev.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length][0];
            selectTab(n); document.getElementById("tab-" + n).focus();
          }
        });
        tablist.appendChild(btn);
      });
      function selectTab(name) {
        current = name;
        tablist.querySelectorAll("[role=tab]").forEach(function (bt) { var on = bt.id === "tab-" + name; bt.setAttribute("aria-selected", String(on)); bt.tabIndex = on ? 0 : -1; });
        panel.setAttribute("aria-labelledby", "tab-" + name);
        history.replaceState(null, "", runHref(loanId, runId, name));
        clear(panel);
        var fn = { findings: tabFindings, missing: tabMissing, conflicts: tabConflicts, actions: tabActions, documents: tabDocuments, reports: tabReports, manifest: tabManifest }[name];
        Promise.resolve(fn(panel, detail, query)).catch(function (e) { clear(panel); panel.appendChild(errorBox(e)); });
      }
      host.appendChild(tablist); host.appendChild(panel);
      selectTab(current);
    });
  }
  function kv(k, v) { return el("span", { class: "kv" }, el("span", { text: k }), el("span", { text: v })); }

  function tabFindings(panel, detail, query) {
    var b = detail.bundle;
    var fResult = select([["", "any result"]].concat(RESULTS.map(function (r) { return [r, r]; })), query.result || "");
    var fBlock = select([["", "blocking + advisory"], ["true", "blocking only"], ["false", "advisory only"]], "");
    var fType = select([["", "both audit types"], ["PREAPPROVAL", "PREAPPROVAL"], ["SUBMISSION_READINESS", "SUBMISSION_READINESS"]], "");
    var fRule = el("input", { class: "input", type: "search", placeholder: "e.g. PRE-EXAMPLE-002", "aria-label": "Rule id contains" });
    var filters = el("div", { class: "filters" },
      el("label", { class: "field" }, el("span", { text: "Result" }), fResult),
      el("label", { class: "field" }, el("span", { text: "Blocking" }), fBlock),
      el("label", { class: "field" }, el("span", { text: "Audit type" }), fType),
      el("label", { class: "field wide" }, el("span", { text: "Rule id contains" }), fRule));
    var host = el("div", {});
    panel.appendChild(filters); panel.appendChild(host);
    var runPath = "/runs/" + encodeURIComponent(b.run.loan_id) + "/" + encodeURIComponent(b.run.run_id) + "/findings";
    var seq = 0;
    function load() {
      var my = ++seq;
      host.style.opacity = "0.6";
      return state.api.get(runPath, { result: fResult.value, blocking: fBlock.value, audit_type: fType.value }).then(function (rows) {
        if (my !== seq) return;
        host.style.opacity = "";
        var needle = fRule.value.trim().toUpperCase();
        rows = (rows || []).filter(function (f) { return !needle || f.rule_id.toUpperCase().indexOf(needle) >= 0; });
        rows.sort(function (a, c) { return (c.blocking ? 1 : 0) - (a.blocking ? 1 : 0); });
        clear(host);
        if (!rows.length) return host.appendChild(emptyState("No findings match these filters."));
        host.appendChild(findingsTable(rows));
      });
    }
    [fResult, fBlock, fType].forEach(function (c) { c.addEventListener("change", load); });
    fRule.addEventListener("input", load);
    return load();
  }
  function select(options, value) {
    var s = el("select", { class: "input" });
    options.forEach(function (o) { var pair = Array.isArray(o) ? o : [o, o]; s.appendChild(el("option", { value: pair[0], text: pair[1] })); });
    s.value = value; return s;
  }
  function findingsTable(rows, withRun) {
    var cols = [{ label: "" }, { label: "Rule" }, { label: "Result" }, { label: "Blocking" }, { label: "Audit" }, { label: "Explanation" }, { label: "Role" }, { label: "Conf." }];
    if (withRun) cols.splice(1, 0, { label: "Loan / run" });
    return table(cols, rows, function (f, i) {
      var detailRow = el("tr", { class: "detail-row", hidden: true }, el("td", { colspan: String(cols.length) }, findingDetail(f)));
      var btn = el("button", { class: "expander", type: "button", "aria-expanded": "false", "aria-label": "Show details for " + f.finding_id + " " + f.rule_id });
      btn.addEventListener("click", function () { var open = detailRow.hidden; detailRow.hidden = !open; btn.setAttribute("aria-expanded", String(open)); });
      var tr = el("tr", {}, el("td", {}, btn),
        withRun ? el("td", { class: "nowrap" }, el("a", { href: runHref(f.loan_id, f.run_id, "findings"), class: "mono", text: f.loan_id }), el("br"), el("span", { class: "mono muted", text: f.run_id })) : null,
        el("td", { class: "nowrap" }, el("span", { class: "mono", text: f.rule_id }), el("br"), el("span", { class: "mono muted", text: f.finding_id })),
        el("td", {}, resultChip(f.result)), el("td", {}, blockingPill(f.blocking)),
        el("td", {}, el("span", { class: "tag", text: f.audit_type === "PREAPPROVAL" ? "PRE" : "SUB" })),
        el("td", { text: f.explanation }), el("td", { class: "nowrap", text: orDash(f.reviewer_role) }), el("td", { text: f.confidence }));
      return [tr, detailRow];
    });
  }
  function findingDetail(f) {
    var left = dl([
      ["Explanation", f.explanation],
      ["Discrepancy", orDash(f.discrepancy)],
      ["Proposed action", orDash(f.proposed_action)],
      ["Evidence ids", f.evidence_ids && f.evidence_ids.length ? el("span", { class: "mono", text: f.evidence_ids.join(", ") }) : el("span", { class: "muted", text: "none cited" })],
      ["Reviewer role", orDash(f.reviewer_role)],
      ["Confidence", f.confidence],
      ["Review reason", orDash(f.review_reason)],
      ["Guideline source", f.guideline_source ? valueNode(f.guideline_source) : el("span", { class: "muted", text: "none (no guideline lookups)" })]
    ]);
    var right;
    if (f.calculation) {
      var c = f.calculation;
      right = el("div", { class: "calc" }, el("div", { class: "eyebrow", text: "Calculation trail" }), dl([
        ["Method", (c.method || "—") + (c.method_version ? " v" + c.method_version : "")],
        ["Formula", el("code", { text: c.formula || "—" })],
        ["Inputs", valueNode(c.inputs || {})],
        ["Intermediates", valueNode(c.intermediate_values || {})],
        ["Output", el("span", { class: "mono", text: orDash(c.output) })],
        ["Warnings", c.warnings && c.warnings.length ? el("ul", { class: "warn-list" }, c.warnings.map(function (w) { return el("li", { text: w }); })) : el("span", { class: "muted", text: "none" })]
      ]));
    } else right = el("div", {}, el("div", { class: "eyebrow", text: "Calculation trail" }), el("p", { class: "muted", text: "No calculation for this finding." }));
    return el("div", { class: "detail-grid" }, left, right);
  }

  function tabMissing(panel, detail) {
    var rows = detail.bundle.missing_documents;
    if (!rows.length) return panel.appendChild(emptyState("No missing documents were reported in this run."));
    panel.appendChild(table([{ label: "#", num: true }, { label: "Audit" }, { label: "Document type" }, { label: "Borrower" }, { label: "Description" }, { label: "Rules" }], rows, function (m) {
      return el("tr", {}, el("td", { class: "num", text: String(m.seq) }), el("td", {}, el("span", { class: "tag", text: m.audit_type === "PREAPPROVAL" ? "PRE" : "SUB" })),
        el("td", { class: "mono", text: m.document_type }), el("td", { class: "mono", text: orDash(m.borrower_id) }), el("td", { text: m.description }),
        el("td", { class: "mono", text: (m.rule_ids || []).join(", ") || "—" }));
    }));
  }

  function tabConflicts(panel, detail) {
    var rows = detail.bundle.conflicts;
    if (!rows.length) return panel.appendChild(emptyState("No conflicts between the 1003, credit, documents, contract, AUS, and LOS were flagged."));
    rows.forEach(function (c) {
      panel.appendChild(el("div", { class: "card stack", style: "gap:10px;margin-bottom:12px" },
        el("div", { class: "toolbar" }, el("span", { class: "mono", text: c.conflict_id }), el("strong", { class: "mono", text: c.field }),
          el("span", { class: "tag", text: c.audit_type === "PREAPPROVAL" ? "PRE" : "SUB" }), el("span", { class: "muted mono", text: "rules: " + ((c.rule_ids || []).join(", ") || "—") })),
        el("div", { class: "conflict-values" }, (c.values || []).map(function (v) {
          return el("div", { class: "cv" }, el("div", { class: "src", text: orDash(v.source) }), el("div", { class: "val", text: orDash(v.value) }),
            el("div", { class: "ev", text: v.evidence_ids && v.evidence_ids.length ? v.evidence_ids.join(", ") : "no evidence cited" }));
        })),
        el("p", { class: "ink-2", text: c.explanation })));
    });
  }

  function tabActions(panel, detail) {
    var b = detail.bundle, actions = b.proposed_actions;
    panel.appendChild(el("div", { class: "notice notice-info", text: "Every proposed action is a DRAFT. Recording a decision here only stores the decision; nothing is written to the LOS or sent to anyone." }));
    if (!actions.length) { panel.appendChild(emptyState("No proposed actions in this run.")); return; }
    var decided = {};
    (detail.action_decisions || []).forEach(function (d) { decided[d.audit_type + "|" + d.action_id] = d; });
    var list = el("div", { class: "q-group", style: "margin-top:12px" });
    actions.forEach(function (a) { list.appendChild(actionCard(a, decided[a.audit_type + "|" + a.action_id])); });
    panel.appendChild(list);
    if (b.approvals_required && b.approvals_required.length) {
      panel.appendChild(el("h3", { style: "margin-top:20px", text: "Approvals required" }));
      panel.appendChild(table([{ label: "#", num: true }, { label: "Audit" }, { label: "Description" }, { label: "Approver" }, { label: "Rules" }], b.approvals_required, function (ap) {
        return el("tr", {}, el("td", { class: "num", text: String(ap.seq) }), el("td", {}, el("span", { class: "tag", text: ap.audit_type === "PREAPPROVAL" ? "PRE" : "SUB" })),
          el("td", { text: ap.description }), el("td", { class: "nowrap", text: ap.approver_role }), el("td", { class: "mono", text: (ap.rule_ids || []).join(", ") || "—" }));
      }));
    }
  }
  function actionCard(a, decision) {
    var card = el("div", { class: "q-item" },
      el("div", { class: "q-top" }, el("span", { class: "mono", text: a.action_id }), el("span", { class: "tag", text: a.action_type }),
        el("span", { class: "tag", text: a.audit_type === "PREAPPROVAL" ? "PRE" : "SUB" }), el("span", { class: "tag tag-DRAFT", text: a.status }),
        el("span", { class: "muted", text: "target " }), el("span", { class: "mono", text: a.target }),
        el("span", { class: "muted", text: "approver " + a.approver_role })),
      el("p", { text: a.description }),
      (a.before_value !== null && a.before_value !== undefined) || (a.after_value !== null && a.after_value !== undefined)
        ? el("div", { class: "before-after" }, el("span", { class: "muted", text: "before" }), el("span", { text: orDash(a.before_value) }), el("span", { class: "muted", text: "after" }), el("span", { text: orDash(a.after_value) }))
        : null,
      el("div", { class: "muted mono", style: "font-size:12px", text: "rules " + ((a.rule_ids || []).join(", ") || "—") + " · evidence " + ((a.evidence_ids || []).join(", ") || "—") }));
    var slot = el("div", {});
    card.appendChild(slot);
    function showDecided(d) {
      clear(slot);
      slot.appendChild(el("div", { class: "done" }, el("span", { text: "Decision:" }), tag(d.decision),
        el("span", { text: "by " + orDash(d.decided_by) + " · " + fmtDate(d.decided_at) }), d.note ? el("span", { class: "ink-2", text: "— " + d.note }) : null));
    }
    if (decision) showDecided(decision);
    else slot.appendChild(decisionForm(["ACCEPTED", "REJECTED", "DEFERRED"], function (choice, note) {
      return state.api.post("/action-decisions", { loan_id: a.loan_id, run_id: a.run_id, audit_type: a.audit_type, action_id: a.action_id, decision: choice, note: note });
    }, showDecided));
    return card;
  }
  /* Shared decision control: choose a decision, write a required note, submit. */
  function decisionForm(choices, submitFn, onDone) {
    var wrap = el("div", { class: "decide" });
    var row = el("div", { class: "row" });
    var chosen = null, buttons = [];
    var note = el("textarea", { class: "input", placeholder: "Required: why this decision (stored with your user id and the timestamp)", "aria-label": "Decision note", required: true });
    var noteWrap = el("div", { hidden: true }, note);
    var send = writeGuard(el("button", { class: "btn btn-primary btn-sm", type: "button", text: "Record decision" }));
    var hint = el("span", { class: "hint", text: state.api.preview ? PREVIEW_NOTE : "A note is required." });
    var err = el("div", { class: "notice notice-error", hidden: true, role: "alert" });
    choices.forEach(function (c) {
      var bt = writeGuard(el("button", { class: "btn btn-sm", type: "button", "aria-pressed": "false", text: c }));
      bt.addEventListener("click", function () {
        chosen = c; buttons.forEach(function (o) { o.setAttribute("aria-pressed", String(o === bt)); o.classList.toggle("btn-primary", o === bt); });
        noteWrap.hidden = false; note.focus();
      });
      buttons.push(bt); row.appendChild(bt);
    });
    send.addEventListener("click", function () {
      err.hidden = true;
      if (!chosen) { err.hidden = false; err.textContent = "Choose a decision first."; return; }
      if (!note.value.trim()) { err.hidden = false; err.textContent = "A note is required."; note.focus(); return; }
      send.disabled = true;
      submitFn(chosen, note.value.trim()).then(function (res) { onDone(res); }, function (e) { send.disabled = false; err.hidden = false; err.textContent = e.message; });
    });
    wrap.appendChild(row); wrap.appendChild(noteWrap); wrap.appendChild(el("div", { class: "row" }, send, hint)); wrap.appendChild(err);
    return wrap;
  }

  function tabDocuments(panel, detail) {
    var b = detail.bundle;
    var host = el("div", {}, loading()); panel.appendChild(host);
    return state.api.get("/runs/" + encodeURIComponent(b.run.loan_id) + "/" + encodeURIComponent(b.run.run_id) + "/documents").then(function (docs) {
      clear(host);
      if (!docs || !docs.length) return host.appendChild(emptyState("No documents were inventoried in this run."));
      host.appendChild(table([{ label: "Id" }, { label: "Filename" }, { label: "Type" }, { label: "Confidence" }, { label: "Pages", num: true }, { label: "Status" }, { label: "Duplicate of" }, { label: "Date" }, { label: "sha256" }], docs, function (d) {
        return el("tr", {}, el("td", { class: "mono nowrap", text: d.document_id }), el("td", { text: d.filename }), el("td", { class: "mono", text: d.document_type }),
          el("td", { text: d.classification_confidence }), el("td", { class: "num", text: orDash(d.page_count) }),
          el("td", {}, el("span", { class: "tag" + (d.status !== "OK" ? " tag-DRAFT" : ""), text: d.status })),
          el("td", { class: "mono", text: orDash(d.duplicate_of) }), el("td", { class: "nowrap", text: orDash(d.document_date) }),
          el("td", {}, el("code", { title: d.sha256, text: shortSha(d.sha256) })));
      }));
    });
  }

  function tabReports(panel, detail) {
    var b = detail.bundle, base = "/runs/" + encodeURIComponent(b.run.loan_id) + "/" + encodeURIComponent(b.run.run_id) + "/reports";
    var listHost = el("div", {}, loading()), body = el("div", {});
    panel.appendChild(listHost); panel.appendChild(body);
    return state.api.get(base).then(function (reports) {
      clear(listHost);
      if (!reports || !reports.length) return listHost.appendChild(emptyState("No Markdown reports in this run."));
      var bar = el("div", { class: "toolbar", style: "margin-bottom:12px" });
      reports.forEach(function (rp) {
        var bt = el("button", { class: "btn btn-sm", type: "button" }, el("span", { class: "mono", text: rp.name }), el("span", { class: "muted", text: " " + shortSha(rp.sha256) }));
        bt.addEventListener("click", function () {
          clear(body); body.appendChild(loading("Loading " + rp.name + "…"));
          state.api.getText(base + "/" + encodeURIComponent(rp.name)).then(function (md) { clear(body); body.appendChild(el("div", { class: "card" }, renderMarkdown(md))); },
            function (e) { clear(body); body.appendChild(errorBox(e)); });
        });
        bar.appendChild(bt);
      });
      listHost.appendChild(bar);
      bar.firstChild.click();
    });
  }

  function tabManifest(panel, detail) {
    var r = detail.bundle.run, m = r.manifest;
    var grid = el("div", { class: "detail-grid" });
    grid.appendChild(el("div", { class: "card" }, el("div", { class: "eyebrow", text: "Tool versions" }),
      r.tool_versions && Object.keys(r.tool_versions).length ? dl(Object.keys(r.tool_versions).map(function (k) { return [k, el("span", { class: "mono", text: String(r.tool_versions[k]) })]; })) : el("p", { class: "muted", text: "No tool versions recorded." })));
    grid.appendChild(el("div", { class: "card" }, el("div", { class: "eyebrow", text: "Run" }), dl([
      ["Skill", orDash(r.skill)], ["Started", fmtDate(r.started_at)], ["Completed", fmtDate(r.completed_at)],
      ["Completed normally", yesNo(r.completed_normally)], ["Stop condition", orDash(r.stop_condition)], ["Synced", fmtDate(r.synced_at)],
      ["Totals", r.totals ? valueNode(r.totals) : "—"]])));
    panel.appendChild(grid);
    if (!m) { panel.appendChild(el("div", { style: "margin-top:12px" }, emptyState("This run has no run_manifest.json."))); return; }
    ["inputs", "outputs"].forEach(function (k) {
      var rows = m[k] || [];
      panel.appendChild(el("h3", { style: "margin:18px 0 8px", text: k[0].toUpperCase() + k.slice(1) + " (" + rows.length + ")" }));
      if (!rows.length) return panel.appendChild(emptyState("No " + k + " listed."));
      panel.appendChild(table([{ label: "Path" }, { label: "Role" }, { label: "sha256" }], rows, function (x) {
        return el("tr", {}, el("td", { class: "mono", text: orDash(x.path) }), el("td", { text: orDash(x.role) }), el("td", {}, el("code", { text: orDash(x.sha256) })));
      }));
    });
    if (m.totals) { panel.appendChild(el("h3", { style: "margin:18px 0 8px", text: "Manifest totals" })); panel.appendChild(el("div", { class: "card" }, valueNode(m.totals))); }
  }

  // ------------------------------------------------------------------ review queue
  function viewReviewQueue(root, params, query) {
    var fRole = select([["", "all roles"]].concat(ROLES.map(function (r) { return [r, r]; })), query.role || "");
    root.appendChild(pageHead("Review queue", "Findings and review items that still need a human. Recording a decision removes the entry from the queue; it never changes the audit file."));
    root.appendChild(el("div", { class: "filters" }, el("label", { class: "field" }, el("span", { text: "Reviewer role" }), fRole)));
    var host = el("div", {}, loading()); root.appendChild(host);
    function load() {
      clear(host); host.appendChild(loading());
      return state.api.get("/review-queue", { reviewer_role: fRole.value }).then(function (entries) {
        clear(host);
        if (!entries || !entries.length) return host.appendChild(emptyState("The review queue is empty" + (fRole.value ? " for " + fRole.value : "") + "."));
        var groups = {};
        entries.forEach(function (e) { (groups[e.reviewer_role] = groups[e.reviewer_role] || []).push(e); });
        Object.keys(groups).sort(function (a, b) { return ROLES.indexOf(a) - ROLES.indexOf(b); }).forEach(function (role) {
          var list = el("div", { class: "q-group" });
          groups[role].forEach(function (e) { list.appendChild(queueCard(e)); });
          host.appendChild(el("section", { class: "section", style: "margin-bottom:22px" },
            el("div", { class: "section-head" }, el("h2", { text: role }), el("span", { class: "muted", text: groups[role].length + " waiting" })), list));
        });
      });
    }
    fRole.addEventListener("change", load);
    return load();
  }
  function queueCard(e) {
    var card = el("div", { class: "q-item" + (e.blocking ? " is-blocking" : "") },
      el("div", { class: "q-top" },
        el("a", { href: runHref(e.loan_id, e.run_id, e.kind === "FINDING" ? "findings" : "documents"), class: "mono", text: e.loan_id + " / " + e.run_id }),
        el("span", { class: "tag", text: e.kind }), e.audit_type ? el("span", { class: "tag", text: e.audit_type }) : null,
        el("span", { class: "mono", text: e.rule_id }), el("span", { class: "mono muted", text: e.target_id }), blockingPill(e.blocking)),
      el("p", { text: e.explanation }),
      e.reason ? el("p", { class: "ink-2" }, el("span", { class: "muted", text: "Reason: " }), e.reason) : null);
    card.appendChild(decisionForm(["CONFIRMED", "OVERRIDDEN", "NEEDS_INFO"], function (choice, note) {
      return state.api.post("/review-decisions", { loan_id: e.loan_id, run_id: e.run_id, audit_type: e.audit_type || null, target_id: e.target_id, decision: choice, note: note });
    }, function () {
      var parent = card.parentNode; if (parent) parent.removeChild(card);
      if (parent && !parent.children.length && parent.parentNode) parent.parentNode.replaceChild(emptyState("Nothing left for this role."), parent);
      refreshNavCounts();
    }));
    return card;
  }

  // ------------------------------------------------------------------ run requests
  function viewRunRequests(root, params, query) {
    root.appendChild(pageHead("Run requests", "A request is only a queued row. Nothing runs automatically: a human runs /mortgage-file-audit <fixture-directory> in Claude Code, then syncs the output directory, then marks the request COMPLETED with the run id."));
    var fStatus = select([["", "all statuses"], ["QUEUED", "QUEUED"], ["PICKED_UP", "PICKED_UP"], ["COMPLETED", "COMPLETED"], ["REJECTED", "REJECTED"]], query.status || "");
    var loanSel = select([["", "loading loans…"]], ""), note = el("textarea", { class: "input", placeholder: "Optional note for whoever runs the audit" });
    var createBtn = writeGuard(el("button", { class: "btn btn-primary", type: "submit", text: "Queue request" }));
    var formErr = el("div", { class: "notice notice-error", hidden: true, role: "alert" });
    var form = el("form", { class: "card stack", style: "gap:12px", onsubmit: function (ev) {
      ev.preventDefault(); formErr.hidden = true;
      if (!loanSel.value) { formErr.hidden = false; formErr.textContent = "Pick a loan."; return; }
      createBtn.disabled = true;
      state.api.post("/run-requests", { loan_id: loanSel.value, note: note.value.trim() || null }).then(function () {
        createBtn.disabled = false; note.value = ""; load(); refreshNavCounts();
      }, function (e) { createBtn.disabled = false; formErr.hidden = false; formErr.textContent = e.message; });
    } },
      el("h2", { text: "Request an audit run" }),
      el("div", { class: "filters", style: "margin:0" },
        el("label", { class: "field wide" }, el("span", { text: "Loan" }), loanSel),
        el("label", { class: "field wide" }, el("span", { text: "Note" }), note)),
      el("div", { class: "toolbar" }, createBtn, el("span", { class: "muted", text: state.api.preview ? PREVIEW_NOTE : "Creates a QUEUED row only." })),
      formErr);
    root.appendChild(el("div", { class: "grid-2" }, form,
      el("div", { class: "card" }, el("h2", { text: "How a request is fulfilled" }),
        el("ol", { class: "limits", style: "margin-top:8px" },
          el("li", { text: "Someone queues a request here (status QUEUED)." }),
          el("li", { text: "A licensed person marks it PICKED_UP and runs /mortgage-file-audit <dir> in Claude Code on a de-identified fixture directory." }),
          el("li", { text: "They sync output/audits/<loan>/<run> to the API (service key, never from this page)." }),
          el("li", { text: "They mark the request COMPLETED with the run id, or REJECTED with a note." })))));
    root.appendChild(el("div", { class: "filters" }, el("label", { class: "field" }, el("span", { text: "Status" }), fStatus)));
    var host = el("div", {}, loading()); root.appendChild(host);
    state.api.get("/loans").then(function (loans) {
      clear(loanSel); loanSel.appendChild(el("option", { value: "", text: loans && loans.length ? "choose a loan" : "no loans synced" }));
      (loans || []).forEach(function (l) { loanSel.appendChild(el("option", { value: l.loan_id, text: l.loan_id + (l.description ? " — " + l.description : "") })); });
    }, function () { clear(loanSel); loanSel.appendChild(el("option", { value: "", text: "loans unavailable" })); });
    function load() {
      clear(host); host.appendChild(loading());
      return state.api.get("/run-requests", { status: fStatus.value }).then(function (rows) {
        clear(host);
        if (!rows || !rows.length) return host.appendChild(emptyState("No run requests" + (fStatus.value ? " with status " + fStatus.value : "") + "."));
        host.appendChild(table([{ label: "Request" }, { label: "Loan" }, { label: "Status" }, { label: "Requested" }, { label: "Note" }, { label: "Run" }, { label: "Update" }], rows, function (r) {
          return el("tr", {}, el("td", { class: "mono nowrap", text: orDash(r.request_id) }), el("td", { class: "mono nowrap", text: r.loan_id }), el("td", {}, tag(r.status)),
            el("td", { class: "nowrap" }, fmtDate(r.requested_at), el("br"), el("span", { class: "muted", text: "by " + orDash(r.requested_by) })),
            el("td", { text: orDash(r.note) }),
            el("td", { class: "mono nowrap" }, r.run_id ? el("a", { href: runHref(r.loan_id, r.run_id), text: r.run_id }) : "—"),
            el("td", {}, requestUpdater(r, load)));
        }));
      });
    }
    fStatus.addEventListener("change", load);
    return load();
  }
  function requestUpdater(r, reload) {
    if (r.status === "COMPLETED" || r.status === "REJECTED") return el("span", { class: "muted", text: "closed" });
    var next = select([["PICKED_UP", "PICKED_UP"], ["COMPLETED", "COMPLETED"], ["REJECTED", "REJECTED"]], r.status === "QUEUED" ? "PICKED_UP" : "COMPLETED");
    var runId = el("input", { class: "input", placeholder: "run id (for COMPLETED)", "aria-label": "Run id", style: "width:170px" });
    var go = writeGuard(el("button", { class: "btn btn-sm", type: "button", text: "Apply" }));
    var err = el("div", { class: "notice notice-error", hidden: true, role: "alert", style: "margin-top:6px" });
    go.addEventListener("click", function () {
      err.hidden = true;
      if (next.value === "COMPLETED" && !runId.value.trim()) { err.hidden = false; err.textContent = "COMPLETED needs the run id."; return; }
      go.disabled = true;
      state.api.patch("/run-requests/" + encodeURIComponent(r.request_id), { status: next.value, run_id: runId.value.trim() || null })
        .then(function () { reload(); refreshNavCounts(); }, function (e) { go.disabled = false; err.hidden = false; err.textContent = e.message; });
    });
    return el("div", {}, el("div", { class: "toolbar" }, next, runId, go), err);
  }

  // ------------------------------------------------------------------ search
  function viewSearch(root, params, query) {
    var q = el("input", { class: "input", type: "search", placeholder: "rule id or words from an explanation", "aria-label": "Search findings", value: query.q || "" });
    var go = el("button", { class: "btn btn-primary", type: "submit", text: "Search" });
    root.appendChild(pageHead("Search findings", "Matches rule ids and finding text across every synced run."));
    var host = el("div", {});
    root.appendChild(el("form", { class: "filters", onsubmit: function (ev) { ev.preventDefault(); history.replaceState(null, "", "#/search?q=" + encodeURIComponent(q.value)); run(); } },
      el("label", { class: "field wide" }, el("span", { text: "Query" }), q), el("div", { class: "field" }, el("span", { text: " " }), go)));
    root.appendChild(host);
    function run() {
      var term = q.value.trim();
      clear(host);
      if (!term) return host.appendChild(emptyState("Type a rule id (e.g. SUB-EXAMPLE-002) or a word to search."));
      host.appendChild(loading("Searching…"));
      return state.api.get("/findings/search", { q: term, limit: 100 }).then(function (rows) {
        clear(host);
        if (!rows || !rows.length) return host.appendChild(emptyState("No findings match “" + term + "”."));
        host.appendChild(el("p", { class: "muted", style: "margin-bottom:8px", text: rows.length + " result" + (rows.length === 1 ? "" : "s") }));
        host.appendChild(findingsTable(rows, true));
      }, function (e) { clear(host); host.appendChild(errorBox(e)); });
    }
    return run();
  }

  // ------------------------------------------------------------------ markdown (minimal, DOM-building, no HTML passthrough)
  function renderMarkdown(src) {
    var root = el("div", { class: "md" });
    var lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
    var i = 0, m;
    function inline(text) {
      var frag = document.createDocumentFragment();
      var re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)/g, last = 0, mm;
      while ((mm = re.exec(text))) {
        if (mm.index > last) frag.appendChild(document.createTextNode(text.slice(last, mm.index)));
        if (mm[1]) frag.appendChild(el("code", { text: mm[1].slice(1, -1) }));
        else if (mm[2]) frag.appendChild(el("strong", { text: mm[2].slice(2, -2) }));
        else frag.appendChild(el("em", { text: mm[3].slice(1, -1) }));
        last = mm.index + mm[0].length;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      return frag;
    }
    function isTableSep(s) { return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(s); }
    function cells(s) { var t = s.trim().replace(/^\|/, "").replace(/\|$/, ""); return t.split("|").map(function (c) { return c.trim(); }); }
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) { i++; continue; }
      if ((m = line.match(/^```/))) {
        var buf = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; root.appendChild(el("pre", {}, el("code", { text: buf.join("\n") }))); continue;
      }
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) { root.appendChild(el("h" + m[1].length, {}, inline(m[2]))); i++; continue; }
      if (/^\s*>/.test(line)) {
        var qb = []; while (i < lines.length && /^\s*>/.test(lines[i])) { qb.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
        root.appendChild(el("blockquote", {}, inline(qb.join(" ")))); continue;
      }
      if (/^\s*\|/.test(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        var head = cells(line); i += 2; var body = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) { body.push(cells(lines[i])); i++; }
        root.appendChild(el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, head.map(function (c) { return el("th", { scope: "col" }, inline(c)); }))),
          el("tbody", {}, body.map(function (r) { return el("tr", {}, r.map(function (c) { return el("td", {}, inline(c)); })); })))));
        continue;
      }
      if ((m = line.match(/^\s*([-*+]|\d+\.)\s+/))) {
        var ordered = /\d/.test(m[1]), list = el(ordered ? "ol" : "ul");
        while (i < lines.length && (m = lines[i].match(/^\s*([-*+]|\d+\.)\s+(.*)$/))) { list.appendChild(el("li", {}, inline(m[2]))); i++; }
        root.appendChild(list); continue;
      }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { root.appendChild(el("hr")); i++; continue; }
      var para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*\||\s*>|\s*([-*+]|\d+\.)\s)/.test(lines[i])) { para.push(lines[i].trim()); i++; }
      if (para.length) root.appendChild(el("p", {}, inline(para.join(" ")))); else i++;
    }
    return root;
  }

  boot();
})();
