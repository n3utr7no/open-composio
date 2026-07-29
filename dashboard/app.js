const API_BASE = "http://127.0.0.1:8000/api";
const USER_ID = "default_user";

// --- Utilities ---
function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    const div = document.createElement("div");
    div.textContent = String(str);
    return div.innerHTML;
}

function showToast(message, type = "info") {
    let container = document.getElementById("toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "toast-container";
        container.style.cssText = "position:fixed;top:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:10px;";
        document.body.appendChild(container);
    }

    const icons = { success: "fa-circle-check", warning: "fa-triangle-exclamation", error: "fa-circle-xmark", info: "fa-circle-info" };
    const colors = { success: "#10b981", warning: "#f59e0b", error: "#ef4444", info: "#6366f1" };

    const toast = document.createElement("div");
    toast.style.cssText = `display:flex;align-items:center;gap:10px;padding:14px 20px;border-radius:12px;background:rgba(15,15,30,0.92);border:1px solid ${colors[type]}40;color:#f3f4f6;font-size:14px;font-family:'Plus Jakarta Sans',sans-serif;backdrop-filter:blur(12px);box-shadow:0 8px 24px rgba(0,0,0,0.4);animation:toastIn 0.3s ease;max-width:400px;`;
    toast.innerHTML = `<i class="fa-solid ${icons[type]}" style="color:${colors[type]};font-size:18px;"></i><span>${escapeHtml(message)}</span>`;

    container.appendChild(toast);
    setTimeout(() => {
        toast.style.animation = "toastOut 0.3s ease forwards";
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// Inject toast animations
const toastStyle = document.createElement("style");
toastStyle.textContent = `
@keyframes toastIn { from { opacity:0; transform:translateX(30px); } to { opacity:1; transform:translateX(0); } }
@keyframes toastOut { from { opacity:1; transform:translateX(0); } to { opacity:0; transform:translateX(30px); } }
`;
document.head.appendChild(toastStyle);

// State
let apps = [];
let currentApp = null;
let currentAction = null;
let logs = JSON.parse(localStorage.getItem("open_composio_logs") || "[]");

// Elements
const appsGrid = document.getElementById("apps-grid");
const appModal = document.getElementById("app-modal");
const closeModalBtn = document.getElementById("close-modal-btn");
const modalAppName = document.getElementById("modal-app-name");
const modalAppDesc = document.getElementById("modal-app-desc");
const modalAppStatus = document.getElementById("modal-app-status");
const modalAppIcon = document.getElementById("modal-app-icon");
const authFieldsContainer = document.getElementById("auth-fields-container");
const connectBtn = document.getElementById("connect-btn");
const disconnectBtn = document.getElementById("disconnect-btn");
const connectionForm = document.getElementById("connection-form");

// Tabs
const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanes = document.querySelectorAll(".tab-pane");
const tabActionsHeader = document.getElementById("tab-actions-header");

// Actions Testing
const actionListItems = document.getElementById("action-list-items");
const actionDetailsContainer = document.getElementById("action-details-container");
const actionRunForm = document.getElementById("action-run-form");
const actionParamsContainer = document.getElementById("action-params-container");
const runActionBtn = document.getElementById("run-action-btn");
const executionOutputBox = document.getElementById("execution-output-box");
const executionResultJson = document.getElementById("execution-result-json");

// Logs
const logsList = document.getElementById("logs-list");
const clearLogsBtn = document.getElementById("clear-logs-btn");

// Navigation
const navIntegrations = document.getElementById("nav-integrations");
const navLogs = document.getElementById("nav-logs");
const integrationsView = document.getElementById("integrations-view");
const logsView = document.getElementById("logs-view");

// Init
async function init() {
    setupEventListeners();
    await fetchApps();
    renderLogs();
}

// Event Listeners
function setupEventListeners() {
    // Navigation
    navIntegrations.addEventListener("click", (e) => {
        e.preventDefault();
        setActiveSection("integrations");
    });
    navLogs.addEventListener("click", (e) => {
        e.preventDefault();
        setActiveSection("logs");
    });

    // Modal
    closeModalBtn.addEventListener("click", closeModal);
    document.querySelector(".modal-overlay").addEventListener("click", closeModal);

    // Tabs
    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const tabId = btn.getAttribute("data-tab");
            
            // No-auth apps can always test actions; auth apps must be connected first
            if (tabId === "tab-actions" && currentApp.auth_type !== "none" && !currentApp.connected) {
                showToast("Please connect the integration first to test actions!", "warning");
                return;
            }

            tabButtons.forEach(b => b.classList.remove("active"));
            tabPanes.forEach(p => p.classList.remove("active"));

            btn.classList.add("active");
            document.getElementById(tabId).classList.add("active");

            if (tabId === "tab-actions") {
                loadAppActions(currentApp.id);
            }
        });
    });

    // Connection Form
    connectionForm.addEventListener("submit", handleConnectionSubmit);
    disconnectBtn.addEventListener("click", handleDisconnect);

    // Action Run Form
    actionRunForm.addEventListener("submit", handleActionRun);

    // Clear Logs
    clearLogsBtn.addEventListener("click", () => {
        logs = [];
        localStorage.setItem("open_composio_logs", JSON.stringify(logs));
        renderLogs();
    });
}

function setActiveSection(section) {
    if (section === "integrations") {
        navIntegrations.classList.add("active");
        navLogs.classList.remove("active");
        integrationsView.classList.add("active");
        logsView.classList.remove("active");
    } else {
        navIntegrations.classList.remove("active");
        navLogs.classList.add("active");
        integrationsView.classList.remove("active");
        logsView.classList.add("active");
    }
}

// Fetch & Render Apps
async function fetchApps() {
    try {
        const response = await fetch(`${API_BASE}/apps?user_id=${USER_ID}`);
        if (!response.ok) throw new Error("Failed to fetch apps from backend");
        const data = await response.json();
        apps = data.apps;
        renderAppsGrid();
    } catch (error) {
        console.error("Error fetching apps:", error);
        appsGrid.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-triangle-exclamation"></i>
                <p>Failed to connect to backend server. Make sure the backend server is running on localhost:8000.</p>
            </div>
        `;
    }
}

function getAppIcon(appId) {
    switch (appId) {
        case "github": return "fa-brands fa-github";
        case "web_search": return "fa-solid fa-magnifying-glass";
        case "weather": return "fa-solid fa-cloud-sun";
        default: return "fa-solid fa-plug";
    }
}

function renderAppsGrid() {
    appsGrid.innerHTML = "";
    apps.forEach(app => {
        const card = document.createElement("div");
        card.className = "app-card";
        card.innerHTML = `
            <div class="app-card-header">
                <div class="app-icon">
                    <i class="${getAppIcon(app.id)}"></i>
                </div>
                <span class="status-badge ${app.connected ? 'connected' : 'disconnected'}">
                    ${app.connected ? 'Connected' : 'Disconnected'}
                </span>
            </div>
            <div class="app-card-body">
                <h3></h3>
                <p></p>
            </div>
            <button class="btn ${app.connected ? 'btn-secondary' : 'btn-primary'} connect-card-btn">
                ${app.connected ? 'Manage' : 'Connect'}
            </button>
        `;
        // Set text safely to prevent XSS
        card.querySelector(".app-card-body h3").textContent = app.name;
        card.querySelector(".app-card-body p").textContent = app.description;
        
        card.addEventListener("click", () => openAppModal(app));
        appsGrid.appendChild(card);
    });
}

// App Modal Operations
function openAppModal(app) {
    currentApp = app;
    modalAppName.textContent = app.name;
    modalAppDesc.textContent = app.description;
    
    // Status Badge
    modalAppStatus.className = `status-badge ${app.connected ? 'connected' : 'disconnected'}`;
    modalAppStatus.textContent = app.connected ? 'Connected' : 'Disconnected';
    
    // Icon
    modalAppIcon.innerHTML = `<i class="${getAppIcon(app.id)}"></i>`;

    // Render connection fields based on auth type
    renderAuthFields(app);

    // Reset tabs
    tabButtons.forEach(b => b.classList.remove("active"));
    tabPanes.forEach(p => p.classList.remove("active"));
    tabButtons[0].classList.add("active");
    tabPanes[0].classList.add("active");

    // No-auth apps can always test actions
    if (app.connected || app.auth_type === "none") {
        tabActionsHeader.classList.remove("hidden");
    } else {
        tabActionsHeader.classList.add("hidden");
    }

    // Reset action playground
    actionListItems.innerHTML = "";
    actionDetailsContainer.innerHTML = `<p class="select-prompt">Select an action from the list to test it.</p>`;
    actionRunForm.classList.add("hidden");
    executionOutputBox.classList.add("hidden");

    appModal.classList.add("active");
}

function closeModal() {
    appModal.classList.remove("active");
    currentApp = null;
}

function renderAuthFields(app) {
    authFieldsContainer.innerHTML = "";
    
    if (app.auth_type === "none") {
        authFieldsContainer.innerHTML = `<p class="app-description" style="margin-bottom:0">This integration doesn't require authentication.</p>`;
        connectBtn.textContent = app.connected ? "Reconnect" : "Connect Integration";
        disconnectBtn.className = app.connected ? "btn btn-danger" : "btn btn-danger hidden";
        return;
    }

    if (app.auth_config && app.auth_config.fields) {
        app.auth_config.fields.forEach(field => {
            const group = document.createElement("div");
            group.className = "form-group";
            group.innerHTML = `
                <label for="auth-${field.name}">${field.label}</label>
                <input 
                    type="${field.type}" 
                    id="auth-${field.name}" 
                    name="${field.name}" 
                    placeholder="${field.placeholder || ''}" 
                    ${field.required ? 'required' : ''}
                >
            `;
            authFieldsContainer.appendChild(group);
        });
    }

    connectBtn.textContent = app.connected ? "Update Connection" : "Save Connection";
    disconnectBtn.className = app.connected ? "btn btn-danger" : "btn btn-danger hidden";
}

// Handle Connection
async function handleConnectionSubmit(e) {
    e.preventDefault();
    const formData = new FormData(connectionForm);
    const authData = {};
    formData.forEach((value, key) => {
        authData[key] = value;
    });

    try {
        const response = await fetch(`${API_BASE}/connections/${currentApp.id}?user_id=${USER_ID}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(authData)
        });

        if (!response.ok) throw new Error("Connection failed");
        
        showToast("Connection successfully saved!", "success");
        await fetchApps();
        // Update currentApp state
        const updated = apps.find(a => a.id === currentApp.id);
        openAppModal(updated);
    } catch (error) {
        console.error(error);
        showToast("Failed to save connection.", "error");
    }
}

async function handleDisconnect() {
    if (!confirm(`Are you sure you want to disconnect ${currentApp.name}?`)) return;

    try {
        const response = await fetch(`${API_BASE}/connections/${currentApp.id}?user_id=${USER_ID}`, {
            method: "DELETE"
        });

        if (!response.ok) throw new Error("Disconnect failed");

        showToast("Disconnected successfully.", "success");
        await fetchApps();
        const updated = apps.find(a => a.id === currentApp.id);
        openAppModal(updated);
    } catch (error) {
        console.error(error);
        showToast("Failed to disconnect.", "error");
    }
}

// Fetch & Load Actions
async function loadAppActions(appId) {
    actionListItems.innerHTML = "Loading...";
    try {
        const response = await fetch(`${API_BASE}/apps/${appId}/actions`);
        if (!response.ok) throw new Error("Failed to load actions");
        const data = await response.json();
        
        renderActionsSidebar(data.actions);
    } catch (error) {
        actionListItems.innerHTML = `<p class="select-prompt">Error loading actions.</p>`;
    }
}

function renderActionsSidebar(actionsList) {
    actionListItems.innerHTML = "";
    if (actionsList.length === 0) {
        actionListItems.innerHTML = `<p class="select-prompt">No actions available.</p>`;
        return;
    }

    actionsList.forEach(action => {
        const btn = document.createElement("button");
        btn.className = "action-item-btn";
        btn.innerHTML = `
            <span class="action-item-title">${action.name}</span>
            <span class="action-item-desc">${action.description}</span>
        `;
        btn.addEventListener("click", () => selectAction(action));
        actionListItems.appendChild(btn);
    });
}

function selectAction(action) {
    currentAction = action;
    
    // Highlight active action
    document.querySelectorAll(".action-item-btn").forEach(btn => {
        if (btn.querySelector(".action-item-title").textContent === action.name) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    // Render action details
    actionDetailsContainer.innerHTML = `
        <div class="action-info-card">
            <h4>${action.name}</h4>
            <p>${action.description}</p>
        </div>
    `;

    // Render parameters fields
    renderActionParams(action);
}

function renderActionParams(action) {
    actionParamsContainer.innerHTML = "";
    executionOutputBox.classList.add("hidden");
    
    const schema = action.parameters_schema;
    if (!schema || !schema.properties || Object.keys(schema.properties).length === 0) {
        actionParamsContainer.innerHTML = `<p class="app-description">No parameters required for this action.</p>`;
        actionRunForm.classList.remove("hidden");
        return;
    }

    const required = schema.required || [];
    for (const [key, prop] of Object.entries(schema.properties)) {
        const group = document.createElement("div");
        group.className = "form-group";
        
        let inputHtml = "";
        const isRequired = required.includes(key);

        if (prop.type === "string") {
            inputHtml = `<input type="text" id="param-${key}" name="${key}" placeholder="${prop.description || ''}" ${isRequired ? 'required' : ''}>`;
        } else if (prop.type === "integer" || prop.type === "number") {
            inputHtml = `<input type="number" id="param-${key}" name="${key}" placeholder="${prop.description || ''}" ${isRequired ? 'required' : ''}>`;
        } else if (prop.type === "boolean") {
            inputHtml = `
                <select id="param-${key}" name="${key}">
                    <option value="true">True</option>
                    <option value="false">False</option>
                </select>
            `;
        } else {
            inputHtml = `<textarea id="param-${key}" name="${key}" placeholder="${prop.description || ''}" rows="3" ${isRequired ? 'required' : ''}></textarea>`;
        }

        group.innerHTML = `
            <label for="param-${key}">${key} ${isRequired ? '<span style="color:var(--danger-color)">*</span>' : ''}</label>
            <p style="font-size:11px; color:var(--text-secondary); margin-bottom:6px">${prop.description || ''}</p>
            ${inputHtml}
        `;
        actionParamsContainer.appendChild(group);
    }

    actionRunForm.classList.remove("hidden");
}

// Handle Action Run
async function handleActionRun(e) {
    e.preventDefault();
    runActionBtn.disabled = true;
    runActionBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Executing...`;
    
    const formData = new FormData(actionRunForm);
    const params = {};
    
    // Parse params based on schema types
    const schema = currentAction.parameters_schema;
    formData.forEach((value, key) => {
        const prop = schema.properties[key];
        if (prop) {
            if (prop.type === "integer" || prop.type === "number") {
                params[key] = Number(value);
            } else if (prop.type === "boolean") {
                params[key] = value === "true";
            } else {
                params[key] = value;
            }
        }
    });

    try {
        const payload = {
            user_id: USER_ID,
            params: params
        };

        const response = await fetch(`${API_BASE}/execute/${currentApp.id}/${currentAction.name}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        
        executionResultJson.textContent = JSON.stringify(data, null, 2);
        executionOutputBox.classList.remove("hidden");

        // Log the execution
        logExecution(currentApp.id, currentAction.name, params, data);
    } catch (error) {
        console.error(error);
        executionResultJson.textContent = JSON.stringify({ error: "Failed to execute action." }, null, 2);
        executionOutputBox.classList.remove("hidden");
    } finally {
        runActionBtn.disabled = false;
        runActionBtn.innerHTML = `<i class="fa-solid fa-play"></i> Run Action`;
    }
}

// Log Executions
function logExecution(appId, actionName, input, output) {
    const logItem = {
        app_id: appId,
        action_name: actionName,
        timestamp: new Date().toLocaleTimeString(),
        input: input,
        output: output
    };
    logs.unshift(logItem);
    localStorage.setItem("open_composio_logs", JSON.stringify(logs));
    renderLogs();
}

function renderLogs() {
    if (logs.length === 0) {
        logsList.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-receipt"></i>
                <p>No actions have been executed yet.</p>
            </div>
        `;
        return;
    }

    logsList.innerHTML = "";
    logs.forEach(log => {
        const card = document.createElement("div");
        card.className = "log-card";
        card.innerHTML = `
            <div class="log-meta">
                <span class="log-action-badge">${log.app_id}.${log.action_name}</span>
                <span class="log-time">${log.timestamp}</span>
            </div>
            <div class="log-body">
                <div class="log-block">
                    <h5>Input Params</h5>
                    <pre>${JSON.stringify(log.input, null, 2)}</pre>
                </div>
                <div class="log-block">
                    <h5>Output Response</h5>
                    <pre>${JSON.stringify(log.output, null, 2)}</pre>
                </div>
            </div>
        `;
        logsList.appendChild(card);
    });
}

// Run
window.addEventListener("DOMContentLoaded", init);
