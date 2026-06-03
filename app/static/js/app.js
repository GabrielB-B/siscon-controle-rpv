function normalizeStatusLabel(value) {
    return String(value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .toUpperCase();
}

const ALL_STATUS_CLASSES = [
    "status-rpv-01", "status-rpv-02", "status-rpv-03", "status-rpv-04",
    "status-rpv-05", "status-rpv-06", "status-rpv-07", "status-rpv-08",
    "status-rpv-09", "status-rpv-10", "status-rpv-11", "status-rpv-12",
    "status-rpv-13", "status-rpv-14", "status-rpv-15", "status-rpv-16",
    "status-rpv-17",
    "status-irrf-01", "status-irrf-02", "status-irrf-03", "status-irrf-04",
    "status-irrf-05", "status-irrf-06", "status-irrf-07", "status-irrf-08",
];

function resolveStatusClass(label, selectName) {
    const normalized = normalizeStatusLabel(label);
    const isImposto = String(selectName || "").includes("imposto");

    if (isImposto) {
        const impostoMap = {
            "SEM TRATAMENTO": "status-irrf-01",
            "SEM IRRF": "status-irrf-02",
            "AGUARDANDO PGTO OB PRINCIPAL": "status-irrf-03",
            "PD IRRF - AGUARDANDO SEFAZ": "status-irrf-04",
            "PGTO IRRF - OB GERADA": "status-irrf-05",
            "CONCLUIDA": "status-irrf-06",
            "DEVOLVIDO": "status-irrf-07",
            "CANCELADO": "status-irrf-08",
        };
        return impostoMap[normalized] || "";
    }

    const rpvMap = {
        "SEM TRATAMENTO": "status-rpv-01",
        "GUIAS GERADAS": "status-rpv-02",
        "SE AGUARDANDO APROVACAO": "status-rpv-03",
        "SE APROVADA - GERAR NE": "status-rpv-04",
        "NE AGUARDANDO ASSINATURA": "status-rpv-05",
        "VD A LIQUIDAR": "status-rpv-06",
        "VD LIQUIDADA": "status-rpv-17",
        "PD EM LOTE CARREGADA": "status-rpv-07",
        "PD GERADA - SEFAZ": "status-rpv-08",
        "PAGAMENTO - OB GERADA": "status-rpv-09",
        "AGUARDANDO ASSINATURA DA OB": "status-rpv-10",
        "ASSINADO - LEVAR AO BANCO": "status-rpv-11",
        "AGUARDANDO RETORNO BANCO": "status-rpv-12",
        "PAGO": "status-rpv-13",
        "CONCLUIDA": "status-rpv-14",
        "DEVOLVIDO": "status-rpv-15",
        "CANCELADO": "status-rpv-16",
    };

    return rpvMap[normalized] || "";
}

function applyStatusColor(selectElement) {
    if (!selectElement) {
        return;
    }

    const selectedOption = selectElement.options[selectElement.selectedIndex];
    const label = selectedOption ? selectedOption.text : "";
    const selectName = selectElement.getAttribute("name") || "";

    selectElement.classList.add("status-colorized");
    ALL_STATUS_CLASSES.forEach((cssClass) => selectElement.classList.remove(cssClass));

    const resolvedClass = resolveStatusClass(label, selectName);
    if (resolvedClass) {
        selectElement.classList.add(resolvedClass);
    }
}

function isPostForm(form) {
    return String(form.getAttribute("method") || form.method || "").toUpperCase() === "POST";
}

function findAssociatedFormControl(form, selector) {
    if (!form || !selector) {
        return null;
    }

    const inlineControl = form.querySelector(selector);
    if (inlineControl) {
        return inlineControl;
    }

    const formId = String(form.getAttribute("id") || "").trim();
    if (!formId) {
        return null;
    }

    const escapedFormId = formId
        .replace(/\\/g, "\\\\")
        .replace(/"/g, '\\"');
    const linkedSelector = selector
        .split(",")
        .map((part) => `${part.trim()}[form="${escapedFormId}"]`)
        .join(", ");

    return linkedSelector ? document.querySelector(linkedSelector) : null;
}

function formHasConfirmBypass(form) {
    return form?.dataset?.confirmBypass === "1";
}

function resubmitConfirmedForm(form, submitter) {
    if (!form) {
        return;
    }

    form.dataset.confirmBypass = "1";
    window.setTimeout(() => {
        if (form.dataset.confirmBypass === "1") {
            delete form.dataset.confirmBypass;
        }
    }, 0);

    if (typeof form.requestSubmit === "function") {
        if (submitter && submitter.form === form) {
            form.requestSubmit(submitter);
            return;
        }

        form.requestSubmit();
        return;
    }

    form.submit();
}

function getConfirmBadgeLabel(variant) {
    if (variant === "danger") {
        return "ALR";
    }
    if (variant === "info") {
        return "INFO";
    }
    if (variant === "success") {
        return "OK";
    }
    return "ATN";
}

function getConfirmDialogElements() {
    const root = document.getElementById("app-confirm-dialog");
    if (!root) {
        return null;
    }

    return {
        root,
        panel: document.getElementById("app-confirm-dialog-panel"),
        badge: document.getElementById("app-confirm-badge"),
        title: document.getElementById("app-confirm-title"),
        message: document.getElementById("app-confirm-message"),
        cancel: document.getElementById("app-confirm-cancel"),
        submit: document.getElementById("app-confirm-submit"),
        backdrop: root.querySelector("[data-confirm-close]"),
    };
}

function openConfirmDialog(options = {}) {
    const title = String(options.title || "Confirmar acao");
    const message = String(options.message || "Deseja continuar?");
    const variant = ["danger", "warning", "info", "success"].includes(options.variant)
        ? options.variant
        : "warning";
    const cancelLabel = String(options.cancelLabel || "Voltar");
    const submitLabel = String(options.submitLabel || "Confirmar");
    const allowDismiss = options.allowDismiss !== false;
    const elements = getConfirmDialogElements();

    if (!elements) {
        return Promise.resolve(window.confirm(message));
    }

    return new Promise((resolve) => {
        let closed = false;
        const previouslyFocused = document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;

        const close = (confirmed) => {
            if (closed) {
                return;
            }

            closed = true;
            elements.root.classList.remove("is-open");
            elements.root.hidden = true;
            elements.root.dataset.variant = "";

            elements.cancel.removeEventListener("click", handleCancel);
            elements.submit.removeEventListener("click", handleSubmit);
            elements.backdrop?.removeEventListener("click", handleCancel);
            document.removeEventListener("keydown", handleKeyDown);

            if (previouslyFocused) {
                previouslyFocused.focus();
            }

            resolve(confirmed);
        };

        const handleCancel = () => close(false);
        const handleSubmit = () => close(true);
        const handleKeyDown = (event) => {
            if (event.key === "Escape" && allowDismiss) {
                event.preventDefault();
                close(false);
            }
        };

        elements.root.dataset.variant = variant;
        elements.badge.textContent = getConfirmBadgeLabel(variant);
        elements.title.textContent = title;
        elements.message.textContent = message;
        elements.cancel.textContent = cancelLabel;
        elements.submit.textContent = submitLabel;
        elements.submit.className = variant === "danger" ? "btn btn-danger" : "btn";

        elements.root.hidden = false;
        window.requestAnimationFrame(() => {
            elements.root.classList.add("is-open");
            elements.cancel.focus();
        });

        elements.cancel.addEventListener("click", handleCancel);
        elements.submit.addEventListener("click", handleSubmit);
        if (allowDismiss) {
            elements.backdrop?.addEventListener("click", handleCancel);
        }
        document.addEventListener("keydown", handleKeyDown);
    });
}


function bindStatusColors(root = document) {
    root.querySelectorAll(
        'select[name="situacao_empenho_id"], select[name="situacao_rpv_id"], select[name="situacao_imposto_id"], select[name="situacao_empenho_id_lote"], select[name="situacao_rpv_id_lote"], select[name="situacao_imposto_id_lote"]'
    ).forEach((selectElement) => {
        applyStatusColor(selectElement);
        selectElement.addEventListener("change", () => applyStatusColor(selectElement));
    });
}

function bindStatusColorsOnSave() {
    document.querySelectorAll("form").forEach((form) => {
        if (!isPostForm(form)) {
            return;
        }

        const statusSelect = form.querySelector(
            'select[name="situacao_empenho_id"], select[name="situacao_rpv_id"], select[name="situacao_imposto_id"], select[name="situacao_empenho_id_lote"], select[name="situacao_rpv_id_lote"], select[name="situacao_imposto_id_lote"]'
        );

        if (!statusSelect) {
            return;
        }

        form.addEventListener("submit", () => {
            bindStatusColors(form);
        });
    });
}

function toggleSidebar() {
    const shell = document.getElementById("app-shell");
    if (!shell) {
        return;
    }

    shell.classList.toggle("sidebar-hidden");

    if (shell.classList.contains("sidebar-hidden")) {
        localStorage.setItem("rpv_sidebar_hidden", "1");
    } else {
        localStorage.removeItem("rpv_sidebar_hidden");
    }

    scheduleWorkbenchOverflowRefresh();
}

function restoreSidebarPreference() {
    const shell = document.getElementById("app-shell");
    if (!shell || window.innerWidth <= 960) {
        return;
    }

    if (localStorage.getItem("rpv_sidebar_hidden") === "1") {
        shell.classList.add("sidebar-hidden");
    }

    scheduleWorkbenchOverflowRefresh();
}

function bindSidebarToggle() {
    document.querySelectorAll("[data-sidebar-toggle]").forEach((button) => {
        button.addEventListener("click", toggleSidebar);
    });
}

function bindAutoSubmitControls() {
    document.querySelectorAll("[data-auto-submit]").forEach((field) => {
        field.addEventListener("change", () => {
            if (field.form) {
                field.form.requestSubmit();
            }
        });
    });
}

function bindCopyButtons() {
    document.querySelectorAll("[data-copy-target]").forEach((button) => {
        button.addEventListener("click", async () => {
            const targetId = button.getAttribute("data-copy-target");
            const target = document.getElementById(targetId);
            if (!target) {
                return;
            }

            try {
                await navigator.clipboard.writeText(target.value || target.textContent || "");
                const originalLabel = button.textContent;
                button.textContent = "Copiado";
                window.setTimeout(() => {
                    button.textContent = originalLabel;
                }, 1400);
            } catch (error) {
                console.error("Falha ao copiar texto.", error);
            }
        });
    });
}

function bindBatchForms() {
    document.querySelectorAll("[data-batch-select-all]").forEach((master) => {
        const formId = master.getAttribute("data-batch-select-all");
        const items = Array.from(document.querySelectorAll(`[data-batch-checkbox="${formId}"]`));

        master.addEventListener("change", () => {
            items.forEach((item) => {
                item.checked = master.checked;
            });
        });

        items.forEach((item) => {
            item.addEventListener("change", () => {
                const checkedItems = items.filter((checkbox) => checkbox.checked).length;
                master.checked = checkedItems > 0 && checkedItems === items.length;
                master.indeterminate = checkedItems > 0 && checkedItems < items.length;
            });
        });
    });

    document.querySelectorAll("[data-batch-form]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            if (formHasConfirmBypass(form)) {
                return;
            }

            const formId = form.getAttribute("id");
            const items = Array.from(
                document.querySelectorAll(`[data-batch-checkbox="${formId}"]:checked`)
            );

            if (!items.length) {
                return;
            }

            const statusSelect = form.querySelector(
                'select[name="situacao_empenho_id_lote"], select[name="situacao_rpv_id_lote"]'
            );
            const currentStatus = normalizeStatusLabel(
                statusSelect?.options[statusSelect.selectedIndex]?.text || ""
            );
            const selectedCount = items.length;

            let confirmOptions = null;
            if (currentStatus === "CANCELADO") {
                const baseMessage = form.getAttribute("data-cancel-confirm")
                    || "Confirmar o cancelamento dos registros selecionados? Eles sairao dos totais e fluxos de pagamento.";
                confirmOptions = {
                    title: "Confirmar cancelamento em lote",
                    message: `${baseMessage} (${selectedCount} selecionado(s))`,
                    variant: "danger",
                    submitLabel: "Confirmar cancelamento",
                };
            } else {
                const message = form.getAttribute("data-batch-confirm");
                if (message) {
                    confirmOptions = {
                        title: "Confirmar atualizacao em lote",
                        message: `${message} (${selectedCount} selecionado(s))`,
                        variant: "warning",
                        submitLabel: "Aplicar atualizacao",
                    };
                }
            }

            if (!confirmOptions) {
                return;
            }

            event.preventDefault();
            const confirmed = await openConfirmDialog(confirmOptions);
            if (confirmed) {
                resubmitConfirmedForm(form, event.submitter);
            }
        });
    });
}

function bindViewPresets() {
    document.querySelectorAll("[data-view-scope]").forEach((scope) => {
        const scopeId = scope.getAttribute("data-view-scope");
        const defaultView = scope.getAttribute("data-view-default") || "resumida";
        const buttons = Array.from(scope.querySelectorAll("[data-view-option]"));
        const allowedViews = buttons.map((button) => button.getAttribute("data-view-option"));
        const storageKey = `rpv_view_${scopeId}`;

        const applyView = (viewName) => {
            const nextView = allowedViews.includes(viewName) ? viewName : defaultView;
            scope.classList.remove("view-resumida", "view-fiscal", "view-pagamento");
            scope.classList.add(`view-${nextView}`);

            buttons.forEach((button) => {
                const isActive = button.getAttribute("data-view-option") === nextView;
                button.classList.toggle("is-active", isActive);
                button.setAttribute("aria-pressed", isActive ? "true" : "false");
            });

            try {
                window.localStorage.setItem(storageKey, nextView);
            } catch (error) {
                console.warn("Nao foi possivel salvar a preferencia de visualizacao.", error);
            }

            scheduleWorkbenchOverflowRefresh();
        };

        let savedView = defaultView;
        try {
            savedView = window.localStorage.getItem(storageKey) || defaultView;
        } catch (error) {
            savedView = defaultView;
        }
        applyView(savedView);

        buttons.forEach((button) => {
            button.addEventListener("click", () => {
                applyView(button.getAttribute("data-view-option"));
            });
        });
    });
}

function refreshWorkbenchOverflowState() {
    document.querySelectorAll(".table-wrap").forEach((wrap) => {
        const workbenchTable = wrap.querySelector(".workbench-table");
        if (!workbenchTable) {
            wrap.classList.remove("has-horizontal-overflow");
            return;
        }

        const hasOverflow = wrap.scrollWidth > wrap.clientWidth + 2;
        wrap.classList.toggle("has-horizontal-overflow", hasOverflow);
    });
}

function scheduleWorkbenchOverflowRefresh() {
    window.requestAnimationFrame(() => {
        refreshWorkbenchOverflowState();
        window.setTimeout(refreshWorkbenchOverflowState, 260);
    });
}

function getAppUserScope() {
    const shell = document.getElementById("app-shell");
    return shell ? shell.getAttribute("data-user-scope") || "anon" : "anon";
}

function getFilterPanelStorageKey(panelId) {
    return `rpv_filter_panel_${getAppUserScope()}_${panelId}`;
}

function bindFilterPanelState() {
    document.querySelectorAll("[data-filter-panel]").forEach((panel) => {
        const panelId = panel.getAttribute("data-filter-panel");
        if (!panelId) {
            return;
        }

        try {
            const savedState = window.localStorage.getItem(getFilterPanelStorageKey(panelId));
            if (savedState === "1") {
                panel.open = true;
            } else if (savedState === "0" && !panel.hasAttribute("open")) {
                panel.open = false;
            }
        } catch (error) {
            console.warn("Nao foi possivel restaurar o estado do painel de filtros.", error);
        }

        panel.addEventListener("toggle", () => {
            try {
                window.localStorage.setItem(
                    getFilterPanelStorageKey(panelId),
                    panel.open ? "1" : "0"
                );
            } catch (error) {
                console.warn("Nao foi possivel salvar o estado do painel de filtros.", error);
            }
        });
    });
}

function normalizePhoneDigits(value) {
    let digits = String(value || "").replace(/\D/g, "");

    if (digits.startsWith("55") && (digits.length === 12 || digits.length === 13)) {
        digits = digits.slice(2);
    }

    return digits.slice(0, 11);
}

function formatPhoneValue(value) {
    const digits = normalizePhoneDigits(value);

    if (digits.length > 10) {
        return `(${digits.slice(0, 2)}) ${digits.slice(2, 7)}-${digits.slice(7)}`;
    }

    if (digits.length > 6) {
        return `(${digits.slice(0, 2)}) ${digits.slice(2, 6)}-${digits.slice(6)}`;
    }

    if (digits.length > 2) {
        return `(${digits.slice(0, 2)}) ${digits.slice(2)}`;
    }

    if (digits.length > 0) {
        return `(${digits}`;
    }

    return "";
}

function bindPhoneMask() {
    document.querySelectorAll("[data-phone-field]").forEach((field) => {
        const applyMask = () => {
            field.value = formatPhoneValue(field.value);
        };

        applyMask();
        field.addEventListener("input", applyMask);
        field.addEventListener("blur", applyMask);
    });
}

function normalizeCurrencyDigits(value, maxDigits = 14) {
    const digits = String(value || "").replace(/\D/g, "");
    const limit = Number.isFinite(Number(maxDigits)) ? Number(maxDigits) : 14;
    return digits.slice(0, limit > 0 ? limit : 14);
}

function formatCurrencyValue(value, maxDigits = 14) {
    const digits = normalizeCurrencyDigits(value, maxDigits);
    if (!digits) {
        return "";
    }

    const padded = digits.padStart(3, "0");
    const integerDigits = padded.slice(0, -2).replace(/^0+(?=\d)/, "") || "0";
    const decimalDigits = padded.slice(-2);
    const integerFormatted = integerDigits.replace(/\B(?=(\d{3})+(?!\d))/g, ".");

    return `${integerFormatted},${decimalDigits}`;
}

function currencyDigitsToNumber(value, maxDigits = 14) {
    const digits = normalizeCurrencyDigits(value, maxDigits);
    if (!digits) {
        return 0;
    }

    return Number(digits) / 100;
}

function formatCurrencyPreview(value) {
    try {
        return new Intl.NumberFormat("pt-BR", {
            style: "currency",
            currency: "BRL",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(Number(value || 0));
    } catch (error) {
        return "R$ 0,00";
    }
}

function bindCurrencyMask() {
    document.querySelectorAll("[data-currency-field]").forEach((field) => {
        const maxDigits = Number(field.getAttribute("data-currency-max-digits") || "14");

        const applyMask = () => {
            field.value = formatCurrencyValue(field.value, maxDigits);
        };

        const blockInvalidKeys = (event) => {
            const allowedKeys = new Set([
                "Backspace",
                "Delete",
                "Tab",
                "Enter",
                "Escape",
                "ArrowLeft",
                "ArrowRight",
                "ArrowUp",
                "ArrowDown",
                "Home",
                "End",
            ]);

            if (
                allowedKeys.has(event.key)
                || event.ctrlKey
                || event.metaKey
            ) {
                return;
            }

            if (!/^\d$/.test(event.key)) {
                event.preventDefault();
            }
        };

        applyMask();
        field.addEventListener("keydown", blockInvalidKeys);
        field.addEventListener("input", applyMask);
        field.addEventListener("blur", applyMask);
    });
}

function bindCotasLaunchPreview() {
    const form = document.querySelector("[data-cotas-launch-form]");
    if (!form) {
        return;
    }

    const totalElement = form.querySelector("[data-cotas-total-preview]");
    const fields = Array.from(form.querySelectorAll("[data-currency-field]"));
    if (!totalElement || !fields.length) {
        return;
    }

    const refreshTotal = () => {
        const total = fields.reduce((accumulator, field) => {
            const maxDigits = Number(field.getAttribute("data-currency-max-digits") || "14");
            return accumulator + currencyDigitsToNumber(field.value, maxDigits);
        }, 0);

        totalElement.textContent = formatCurrencyPreview(total);
    };

    refreshTotal();
    fields.forEach((field) => {
        field.addEventListener("input", refreshTotal);
        field.addEventListener("change", refreshTotal);
        field.addEventListener("blur", refreshTotal);
    });
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function bindIrrfCalculators() {
    document.querySelectorAll("[data-irrf-calculator]").forEach((root) => {
        if (root.dataset.irrfCalcBound === "1") {
            return;
        }

        const button = root.querySelector('[data-irrf-action="calculate"]');
        const panel = root.querySelector("[data-irrf-panel]");
        const form = root.tagName === "FORM" ? root : root.closest("form");
        const endpoint = root.getAttribute("data-irrf-calc-url") || "";

        if (!button || !panel || !form || !endpoint) {
            return;
        }

        root.dataset.irrfCalcBound = "1";

        const findField = (fieldName) => root.querySelector(`[data-irrf-input="${fieldName}"]`);
        const csrfField = form.querySelector('input[name="csrf_token"]');
        const valorIrrfField = findField("valor_irrf");
        const semIrrfField = findField("sem_irrf");

        const readFieldValue = (fieldName) => {
            const field = findField(fieldName);
            if (!field) {
                return "";
            }

            if (field.type === "checkbox") {
                return field.checked;
            }

            return String(field.value || "").trim();
        };

        const renderPanel = (variant, summary, payload = {}) => {
            const details = Array.isArray(payload.detalhes) ? payload.detalhes : [];
            const memory = Array.isArray(payload.memoria_calculo) ? payload.memoria_calculo : [];
            const detailsHtml = details.length
                ? `
                    <div class="irrf-calc-grid">
                        ${details.map((item) => `
                            <div class="irrf-calc-item">
                                <span class="irrf-calc-label">${escapeHtml(item.label || "")}</span>
                                <span class="irrf-calc-value">${escapeHtml(item.valor || "")}</span>
                            </div>
                        `).join("")}
                    </div>
                `
                : "";

            const inlineNote = payload.aplicavel && payload.sugerir_sem_irrf
                ? (
                    semIrrfField
                        ? "A marcacao de sem IRRF foi ajustada como sugestao. Revise antes de salvar."
                        : "A sugestao ficou sem retencao efetiva. Revise manualmente o fluxo deste item antes de salvar."
                )
                : "";

            const memoryHtml = memory.length
                ? `
                    <ul class="irrf-calc-memory">
                        ${memory.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
                    </ul>
                `
                : "";

            const meta = payload.versao_regra
                ? `<div class="irrf-calc-meta">Regra aplicada: ${escapeHtml(payload.versao_regra)}</div>`
                : "";

            const note = inlineNote
                ? `<div class="irrf-calc-inline-note">${escapeHtml(inlineNote)}</div>`
                : "";

            panel.hidden = false;
            panel.className = `irrf-calc-panel is-${variant}`;
            panel.innerHTML = `
                <div class="irrf-calc-summary">${escapeHtml(summary)}</div>
                ${detailsHtml}
                ${note}
                ${memoryHtml}
                ${meta}
            `;
        };

        button.addEventListener("click", async () => {
            button.disabled = true;
            renderPanel("loading", "Calculando a sugestao operacional de IRRF...");

            try {
                const response = await fetch(endpoint, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrfField ? csrfField.value : "",
                    },
                    body: JSON.stringify({
                        competencia: readFieldValue("competencia"),
                        valor_bruto: readFieldValue("valor_bruto"),
                        documento: readFieldValue("documento"),
                        tipo_documento: readFieldValue("tipo_documento"),
                        sem_irrf: readFieldValue("sem_irrf") === true,
                    }),
                });

                if (!response.ok) {
                    throw new Error("Nao foi possivel consultar o calculo de IRRF agora.");
                }

                const payload = await response.json();
                if (payload.aplicavel && valorIrrfField) {
                    valorIrrfField.value = payload.valor_irrf_input || "";
                }
                if (payload.aplicavel && semIrrfField) {
                    semIrrfField.checked = Boolean(payload.sugerir_sem_irrf);
                }

                const variant = !payload.aplicavel
                    ? "warning"
                    : (String(payload.valor_irrf || "0") === "0.00" ? "info" : "success");

                renderPanel(variant, payload.resumo || "Calculo concluido.", payload);
            } catch (error) {
                renderPanel(
                    "warning",
                    error instanceof Error
                        ? error.message
                        : "Nao foi possivel calcular o IRRF sugerido agora."
                );
            } finally {
                button.disabled = false;
            }
        });
    });
}

function getSavedFilterStorageKey(scopeId) {
    return `rpv_saved_filters_${getAppUserScope()}_${scopeId}`;
}

function readSavedFilters(scopeId) {
    const storageKey = getSavedFilterStorageKey(scopeId);

    try {
        const rawValue = window.localStorage.getItem(storageKey);
        if (!rawValue) {
            return { items: [], favoriteId: "" };
        }

        const parsed = JSON.parse(rawValue);
        return {
            items: Array.isArray(parsed.items) ? parsed.items : [],
            favoriteId: String(parsed.favoriteId || ""),
        };
    } catch (error) {
        console.warn("Nao foi possivel ler os filtros salvos.", error);
        return { items: [], favoriteId: "" };
    }
}

function writeSavedFilters(scopeId, payload) {
    const storageKey = getSavedFilterStorageKey(scopeId);
    window.localStorage.setItem(storageKey, JSON.stringify(payload));
}

function buildSavedFilterQuery(params) {
    const query = new URLSearchParams();

    Object.keys(params || {})
        .sort()
        .forEach((key) => {
            const value = params[key];
            if (value === undefined || value === null || value === "") {
                return;
            }
            query.set(key, value);
        });

    return query;
}

function collectSavedFilterParams(form) {
    const params = {};
    if (!form) {
        return params;
    }

    form.querySelectorAll("input, select, textarea").forEach((field) => {
        if (!field.name || field.disabled || field.type === "hidden") {
            return;
        }

        if ((field.type === "checkbox" || field.type === "radio") && !field.checked) {
            return;
        }

        const value = String(field.value || "").trim();
        if (!value) {
            return;
        }

        params[field.name] = value;
    });

    return params;
}

function bindDeclarativeConfirmForms() {
    document.querySelectorAll("form[data-confirm-message]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            if (formHasConfirmBypass(form)) {
                return;
            }

            event.preventDefault();
            const confirmed = await openConfirmDialog({
                title: form.getAttribute("data-confirm-title") || "Confirmar acao",
                message: form.getAttribute("data-confirm-message") || "Deseja continuar?",
                variant: form.getAttribute("data-confirm-variant") || "warning",
                cancelLabel: form.getAttribute("data-confirm-cancel-label") || "Voltar",
                submitLabel: form.getAttribute("data-confirm-submit-label") || "Confirmar",
            });

            if (confirmed) {
                resubmitConfirmedForm(form, event.submitter);
            }
        });
    });
}

function bindLegacyInlineConfirms() {
    document.querySelectorAll('form[onsubmit*="confirm("]').forEach((form) => {
        const inlineHandler = String(form.getAttribute("onsubmit") || "").trim();
        const match = inlineHandler.match(/confirm\((['"])(.*?)\1\)/);
        if (!match) {
            return;
        }

        form.removeAttribute("onsubmit");
        if (!form.hasAttribute("data-confirm-message")) {
            form.setAttribute("data-confirm-message", match[2]);
        }
        if (!form.hasAttribute("data-confirm-title")) {
            form.setAttribute("data-confirm-title", "Confirmar acao");
        }
        if (!form.hasAttribute("data-confirm-variant")) {
            const hasDangerButton = !!form.querySelector(".btn-danger");
            form.setAttribute("data-confirm-variant", hasDangerButton ? "danger" : "warning");
        }
        if (!form.hasAttribute("data-confirm-submit-label")) {
            const submitButton = form.querySelector('[type="submit"]');
            const submitLabel = String(submitButton?.textContent || "").trim();
            if (submitLabel) {
                form.setAttribute("data-confirm-submit-label", submitLabel);
            }
        }
    });
}

// Mantemos esta redefinicao perto do bootstrap para garantir que formularios GET,
// como os filtros, nunca entrem no fluxo de confirmacao de cancelamento.
function bindCancelStatusConfirmation() {
    document.querySelectorAll("form").forEach((form) => {
        if (form.hasAttribute("data-batch-form") || !isPostForm(form)) {
            return;
        }

        const statusSelect = findAssociatedFormControl(
            form,
            'select[name="situacao_empenho_id"], select[name="situacao_rpv_id"]'
        );

        if (!statusSelect) {
            return;
        }

        const initialStatus = normalizeStatusLabel(
            statusSelect.options[statusSelect.selectedIndex]?.text || ""
        );

        form.addEventListener("submit", async (event) => {
            if (formHasConfirmBypass(form)) {
                return;
            }

            const currentStatus = normalizeStatusLabel(
                statusSelect.options[statusSelect.selectedIndex]?.text || ""
            );

            if (currentStatus !== "CANCELADO" || initialStatus === "CANCELADO") {
                return;
            }

            event.preventDefault();
            const confirmed = await openConfirmDialog({
                title: "Confirmar cancelamento",
                message: form.getAttribute("data-cancel-confirm")
                    || "Confirmar o cancelamento deste registro? Ele saira dos totais e fluxos de pagamento.",
                variant: "danger",
                submitLabel: "Confirmar cancelamento",
            });
            if (confirmed) {
                resubmitConfirmedForm(form, event.submitter);
            }
        });
    });
}

function bindManualPaymentDateConfirmation() {
    document.querySelectorAll("form[data-payment-date-confirm]").forEach((form) => {
        const paymentInput = form.querySelector('input[name="data_pagamento"]');
        const confirmField = form.querySelector(
            'input[name="confirmar_data_pagamento_manual"]'
        );

        if (!paymentInput || !confirmField || !isPostForm(form)) {
            return;
        }

        form.addEventListener("submit", async (event) => {
            if (formHasConfirmBypass(form)) {
                return;
            }

            const initialValue = String(paymentInput.dataset.initialValue || "").trim();
            const currentValue = String(paymentInput.value || "").trim();
            const changedManually = currentValue && currentValue !== initialValue;

            if (!changedManually) {
                confirmField.value = "0";
                return;
            }

            event.preventDefault();
            const confirmed = await openConfirmDialog({
                title: form.getAttribute("data-payment-date-title")
                    || "Confirmar data manual de pagamento",
                message: form.getAttribute("data-payment-date-message")
                    || "Deseja confirmar a alteracao manual da data de pagamento?",
                variant: "warning",
                submitLabel: form.getAttribute("data-payment-date-submit-label")
                    || "Confirmar data manual",
            });

            if (!confirmed) {
                confirmField.value = "0";
                return;
            }

            confirmField.value = "1";
            resubmitConfirmedForm(form, event.submitter);
        });
    });
}

function bindSensitiveValueEdits() {
    document.querySelectorAll("[data-sensitive-value-edit]").forEach((scope) => {
        const input = scope.querySelector("[data-sensitive-value-input]");
        const button = scope.querySelector("[data-sensitive-value-action]");
        const form = input?.form || scope.closest("form");
        const confirmField = form?.querySelector('input[name="confirmar_edicao_valor_bruto"]');

        if (!input || !button || !form || !confirmField) {
            return;
        }

        const initialValue = String(input.dataset.initialValue || input.value || "").trim();
        const title = scope.getAttribute("data-sensitive-value-title")
            || "Confirmar edicao do valor bruto";
        const message = scope.getAttribute("data-sensitive-value-message")
            || "Alterar o valor bruto depois do cadastro muda os totais e o historico. Deseja liberar a edicao?";

        const unlockInput = () => {
            input.readOnly = false;
            input.classList.add("is-sensitive-editing");
            confirmField.value = "1";
            button.textContent = "Valor liberado";
            button.disabled = true;
            input.focus();
            input.select();
        };

        button.addEventListener("click", async () => {
            if (!input.readOnly) {
                return;
            }

            const confirmed = await openConfirmDialog({
                title,
                message,
                variant: "warning",
                submitLabel: "Liberar edicao",
            });

            if (confirmed) {
                unlockInput();
            }
        });

        form.addEventListener("submit", async (event) => {
            if (formHasConfirmBypass(form)) {
                return;
            }

            const currentValue = String(input.value || "").trim();
            if (currentValue === initialValue || confirmField.value === "1") {
                return;
            }

            event.preventDefault();
            const confirmed = await openConfirmDialog({
                title,
                message: "O valor bruto foi alterado. Confirma salvar essa correcao e registrar no historico?",
                variant: "warning",
                submitLabel: "Salvar correcao",
            });

            if (!confirmed) {
                return;
            }

            confirmField.value = "1";
            resubmitConfirmedForm(form, event.submitter);
        });
    });
}

function openNativeDatePicker(field) {
    if (!field || typeof field.showPicker !== "function" || field.disabled || field.readOnly) {
        return;
    }

    try {
        field.showPicker();
    } catch (error) {
        // Alguns navegadores recusam fora de gesto direto ou nao implementam o metodo.
    }
}

function bindDateFieldPickers() {
    document.querySelectorAll('input[type="date"], input[type="month"]').forEach((field) => {
        field.addEventListener("click", () => {
            openNativeDatePicker(field);
        });

        field.addEventListener("keydown", (event) => {
            if (!["Enter", " ", "ArrowDown"].includes(event.key)) {
                return;
            }

            event.preventDefault();
            openNativeDatePicker(field);
        });
    });
}

function bindSavedFilters() {
    document.querySelectorAll("[data-saved-filters]").forEach((panel) => {
        const scopeId = panel.getAttribute("data-saved-filters-scope");
        const formId = panel.getAttribute("data-saved-form-id");
        const baseUrl = panel.getAttribute("data-saved-filters-base-url") || window.location.pathname;
        const filterForm = document.getElementById(formId);
        const select = panel.querySelector("[data-saved-filters-list]");
        const nameInput = panel.querySelector("[data-saved-filters-name]");
        const saveButton = panel.querySelector("[data-saved-filters-save]");
        const applyButton = panel.querySelector("[data-saved-filters-apply]");
        const favoriteButton = panel.querySelector("[data-saved-filters-favorite-toggle]");
        const deleteButton = panel.querySelector("[data-saved-filters-delete]");
        const favoriteLabel = panel.querySelector("[data-saved-filters-favorite]");
        const statusLabel = panel.querySelector("[data-saved-filters-status]");

        if (!scopeId || !filterForm || !select || !nameInput || !saveButton) {
            return;
        }

        const updateStatus = (message = "") => {
            if (statusLabel) {
                statusLabel.textContent = message;
            }
        };

        const saveState = (nextState) => {
            try {
                writeSavedFilters(scopeId, nextState);
                return true;
            } catch (error) {
                console.warn("Nao foi possivel gravar os filtros salvos.", error);
                updateStatus("Nao foi possivel salvar neste navegador.");
                return false;
            }
        };

        let state = readSavedFilters(scopeId);

        const getSelectedItem = () => {
            const selectedId = String(select.value || "");
            return state.items.find((item) => String(item.id) === selectedId) || null;
        };

        const renderSavedFilters = (preferredId = "") => {
            const selectedId = String(preferredId || select.value || "");
            select.innerHTML = "";

            const emptyOption = document.createElement("option");
            emptyOption.value = "";
            emptyOption.textContent = state.items.length ? "Escolha um filtro salvo" : "Nenhum filtro salvo";
            select.appendChild(emptyOption);

            state.items.forEach((item) => {
                const option = document.createElement("option");
                option.value = String(item.id);
                option.textContent = state.favoriteId === String(item.id) ? `* ${item.name}` : item.name;
                if (String(item.id) === selectedId) {
                    option.selected = true;
                }
                select.appendChild(option);
            });

            const selectedItem = getSelectedItem();
            nameInput.value = selectedItem ? selectedItem.name : "";

            if (applyButton) {
                applyButton.disabled = !selectedItem;
            }

            if (favoriteButton) {
                favoriteButton.disabled = !selectedItem;
                favoriteButton.textContent = selectedItem && state.favoriteId === String(selectedItem.id)
                    ? "Remover favorito"
                    : "Favoritar";
            }

            if (deleteButton) {
                deleteButton.disabled = !selectedItem;
            }

            if (favoriteLabel) {
                const favoriteItem = state.items.find((item) => String(item.id) === String(state.favoriteId));
                favoriteLabel.textContent = favoriteItem
                    ? `Favorito ao abrir: ${favoriteItem.name}`
                    : "Nenhum favorito definido.";
            }
        };

        const currentSearch = new URLSearchParams(window.location.search);
        if (
            panel.getAttribute("data-saved-filters-autoload") === "true" &&
            state.favoriteId &&
            !currentSearch.toString() &&
            !currentSearch.has("sem_favorito")
        ) {
            const favoriteItem = state.items.find((item) => String(item.id) === String(state.favoriteId));
            if (favoriteItem && Object.keys(favoriteItem.params || {}).length) {
                const favoriteQuery = buildSavedFilterQuery(favoriteItem.params || {});
                if (favoriteQuery.toString()) {
                    window.location.replace(`${baseUrl}?${favoriteQuery.toString()}`);
                    return;
                }
            }
        }

        const applySavedFilter = (item) => {
            if (!item) {
                updateStatus("Escolha um filtro salvo para aplicar.");
                return;
            }

            const query = buildSavedFilterQuery(item.params || {});
            const nextUrl = query.toString() ? `${baseUrl}?${query.toString()}` : baseUrl;
            if (nextUrl === `${window.location.pathname}${window.location.search}`) {
                updateStatus(`Filtro "${item.name}" ja esta em uso.`);
                return;
            }

            window.location.assign(nextUrl);
        };

        renderSavedFilters();

        select.addEventListener("change", () => {
            const selectedItem = getSelectedItem();
            nameInput.value = selectedItem ? selectedItem.name : "";
            renderSavedFilters(select.value);
            updateStatus(selectedItem ? `Pronto para aplicar "${selectedItem.name}".` : "");
        });

        saveButton.addEventListener("click", () => {
            const selectedItem = getSelectedItem();
            const nextName = String(nameInput.value || "").trim() || (selectedItem ? selectedItem.name : "");

            if (!nextName) {
                updateStatus("Informe um nome para salvar o filtro atual.");
                nameInput.focus();
                return;
            }

            const params = collectSavedFilterParams(filterForm);
            const existingItem = state.items.find(
                (item) => item.name.trim().toLowerCase() === nextName.toLowerCase()
            );

            if (existingItem) {
                existingItem.name = nextName;
                existingItem.params = params;
                existingItem.updatedAt = new Date().toISOString();
                if (!saveState(state)) {
                    return;
                }
                renderSavedFilters(existingItem.id);
                updateStatus(`Filtro "${nextName}" atualizado.`);
                return;
            }

            const nextItem = {
                id: `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
                name: nextName,
                params,
                updatedAt: new Date().toISOString(),
            };

            state.items = [nextItem].concat(state.items);
            if (!saveState(state)) {
                return;
            }
            renderSavedFilters(nextItem.id);
            updateStatus(`Filtro "${nextName}" salvo.`);
        });

        if (applyButton) {
            applyButton.addEventListener("click", () => {
                applySavedFilter(getSelectedItem());
            });
        }

        if (favoriteButton) {
            favoriteButton.addEventListener("click", () => {
                const selectedItem = getSelectedItem();
                if (!selectedItem) {
                    updateStatus("Escolha um filtro salvo para marcar como favorito.");
                    return;
                }

                state.favoriteId = state.favoriteId === String(selectedItem.id)
                    ? ""
                    : String(selectedItem.id);

                if (!saveState(state)) {
                    return;
                }
                renderSavedFilters(selectedItem.id);
                updateStatus(
                    state.favoriteId
                        ? `Filtro "${selectedItem.name}" marcado para abrir automaticamente.`
                        : "Favorito removido."
                );
            });
        }

        if (deleteButton) {
            deleteButton.addEventListener("click", async () => {
                const selectedItem = getSelectedItem();
                if (!selectedItem) {
                    updateStatus("Escolha um filtro salvo para excluir.");
                    return;
                }

                const confirmed = await openConfirmDialog({
                    title: "Excluir filtro salvo",
                    message: `Excluir o filtro salvo "${selectedItem.name}"?`,
                    variant: "warning",
                    submitLabel: "Excluir filtro",
                });
                if (!confirmed) {
                    return;
                }

                state.items = state.items.filter((item) => String(item.id) !== String(selectedItem.id));
                if (state.favoriteId === String(selectedItem.id)) {
                    state.favoriteId = "";
                }

                if (!saveState(state)) {
                    return;
                }
                renderSavedFilters();
                updateStatus(`Filtro "${selectedItem.name}" excluido.`);
            });
        }
    });
}

function isDraftFieldEligible(field) {
    if (!field || !field.name || field.disabled || field.hasAttribute("data-draft-ignore")) {
        return false;
    }

    const type = String(field.type || "").toLowerCase();
    return !["hidden", "submit", "button", "reset", "image", "file"].includes(type);
}

function normalizeDraftPayload(payload) {
    const normalized = {};

    Object.keys(payload || {})
        .sort()
        .forEach((name) => {
            const value = payload[name];
            if (Array.isArray(value)) {
                normalized[name] = value.map((item) => String(item ?? "")).sort();
                return;
            }

            normalized[name] = String(value ?? "").trim();
        });

    return normalized;
}

function areDraftPayloadsEqual(leftPayload, rightPayload) {
    return JSON.stringify(normalizeDraftPayload(leftPayload))
        === JSON.stringify(normalizeDraftPayload(rightPayload));
}

function serializeDraftForm(form) {
    const payload = {};

    form.querySelectorAll("input, select, textarea").forEach((field) => {
        if (!isDraftFieldEligible(field)) {
            return;
        }

        const name = String(field.name || "").trim();
        const type = String(field.type || "").toLowerCase();

        if (type === "checkbox") {
            if (!Object.prototype.hasOwnProperty.call(payload, name)) {
                payload[name] = [];
            }

            if (field.checked) {
                payload[name].push(field.value || "on");
            }
            return;
        }

        if (type === "radio") {
            if (!Object.prototype.hasOwnProperty.call(payload, name)) {
                payload[name] = "";
            }

            if (field.checked) {
                payload[name] = field.value;
            }
            return;
        }

        payload[name] = field.value;
    });

    return payload;
}

function applyDraftPayload(form, payload) {
    const groupedFields = new Map();

    form.querySelectorAll("input, select, textarea").forEach((field) => {
        if (!isDraftFieldEligible(field)) {
            return;
        }

        const name = String(field.name || "").trim();
        if (!groupedFields.has(name)) {
            groupedFields.set(name, []);
        }
        groupedFields.get(name).push(field);
    });

    groupedFields.forEach((fields, name) => {
        if (!fields.length) {
            return;
        }

        const sampleField = fields[0];
        const type = String(sampleField.type || "").toLowerCase();

        if (type === "checkbox") {
            const selectedValues = Array.isArray(payload[name])
                ? payload[name].map((value) => String(value ?? ""))
                : (payload[name] ? [String(payload[name])] : []);

            fields.forEach((field) => {
                field.checked = selectedValues.includes(String(field.value || "on"));
                field.dispatchEvent(new Event("change", { bubbles: true }));
            });
            return;
        }

        if (type === "radio") {
            const selectedValue = String(payload[name] ?? "");
            fields.forEach((field) => {
                field.checked = selectedValue === String(field.value || "");
                field.dispatchEvent(new Event("change", { bubbles: true }));
            });
            return;
        }

        const nextValue = payload[name] == null ? "" : String(payload[name]);
        sampleField.value = nextValue;
        sampleField.dispatchEvent(new Event("input", { bubbles: true }));
        sampleField.dispatchEvent(new Event("change", { bubbles: true }));
    });
}

function buildDraftStorageKey(form) {
    const draftKey = String(form.getAttribute("data-draft-key") || "").trim();
    if (!draftKey) {
        return "";
    }

    return `rpv_form_draft_${getAppUserScope()}_${draftKey}`;
}

function buildDraftSubmitMarkerKey(storageKey) {
    return `${storageKey}__submitted`;
}

function readStoredDraft(storageKey) {
    if (!storageKey) {
        return null;
    }

    try {
        const rawValue = window.localStorage.getItem(storageKey);
        return rawValue ? JSON.parse(rawValue) : null;
    } catch (error) {
        console.warn("Nao foi possivel ler o rascunho local do formulario.", error);
        return null;
    }
}

function writeStoredDraft(storageKey, payload) {
    if (!storageKey) {
        return;
    }

    try {
        window.localStorage.setItem(storageKey, JSON.stringify(payload));
    } catch (error) {
        console.warn("Nao foi possivel salvar o rascunho local do formulario.", error);
    }
}

function removeStoredDraft(storageKey) {
    if (!storageKey) {
        return;
    }

    try {
        window.localStorage.removeItem(storageKey);
    } catch (error) {
        console.warn("Nao foi possivel limpar o rascunho local do formulario.", error);
    }
}

function readDraftSubmitMarker(markerKey) {
    if (!markerKey) {
        return "";
    }

    try {
        return String(window.sessionStorage.getItem(markerKey) || "");
    } catch (error) {
        return "";
    }
}

function writeDraftSubmitMarker(markerKey) {
    if (!markerKey) {
        return;
    }

    try {
        window.sessionStorage.setItem(markerKey, "1");
    } catch (error) {
        console.warn("Nao foi possivel registrar o envio do formulario.", error);
    }
}

function removeDraftSubmitMarker(markerKey) {
    if (!markerKey) {
        return;
    }

    try {
        window.sessionStorage.removeItem(markerKey);
    } catch (error) {
        console.warn("Nao foi possivel limpar o marcador de envio do formulario.", error);
    }
}

function pageHasSuccessFlash() {
    return !!document.querySelector(".flash.flash-success");
}

function createDraftNotice(form, label, onDiscard) {
    const wrapper = document.createElement("div");
    wrapper.className = "flash-stack";
    wrapper.setAttribute("data-draft-notice", "1");

    const flash = document.createElement("div");
    flash.className = "flash flash-info";
    flash.setAttribute("role", "status");

    const badge = document.createElement("div");
    badge.className = "flash-badge";
    badge.setAttribute("aria-hidden", "true");
    badge.textContent = "INFO";

    const copy = document.createElement("div");
    copy.className = "flash-copy";

    const title = document.createElement("strong");
    title.className = "flash-title";
    title.textContent = "Rascunho restaurado";

    const message = document.createElement("div");
    message.className = "flash-message";
    message.textContent = `${label} recuperou um rascunho local salvo neste navegador. Revise os campos e clique em Salvar quando concluir.`;

    const actions = document.createElement("div");
    actions.className = "content-top-14";

    const discardButton = document.createElement("button");
    discardButton.type = "button";
    discardButton.className = "btn btn-secondary btn-sm";
    discardButton.textContent = "Descartar rascunho";
    discardButton.addEventListener("click", onDiscard);

    actions.appendChild(discardButton);
    copy.appendChild(title);
    copy.appendChild(message);
    copy.appendChild(actions);
    flash.appendChild(badge);
    flash.appendChild(copy);
    wrapper.appendChild(flash);

    form.prepend(wrapper);
    return wrapper;
}

function bindFormDrafts() {
    document.querySelectorAll("form[data-draft-key]").forEach((form) => {
        const storageKey = buildDraftStorageKey(form);
        if (!storageKey) {
            return;
        }

        const submitMarkerKey = buildDraftSubmitMarkerKey(storageKey);
        const formLabel = String(form.getAttribute("data-draft-label") || "Este formulario").trim();
        const initialSnapshot = serializeDraftForm(form);
        let suppressDraftPersistence = false;
        let noticeElement = null;

        const persistDraft = () => {
            if (suppressDraftPersistence) {
                return;
            }

            writeStoredDraft(storageKey, serializeDraftForm(form));
        };

        const restoreSnapshot = (snapshot) => {
            suppressDraftPersistence = true;
            applyDraftPayload(form, snapshot);
            suppressDraftPersistence = false;
        };

        form.querySelectorAll("input, select, textarea").forEach((field) => {
            if (!isDraftFieldEligible(field)) {
                return;
            }

            field.addEventListener("input", persistDraft);
            field.addEventListener("change", persistDraft);
        });

        form.addEventListener("submit", (event) => {
            if (event.defaultPrevented) {
                return;
            }

            persistDraft();
            writeDraftSubmitMarker(submitMarkerKey);
        });

        const hadSubmitted = readDraftSubmitMarker(submitMarkerKey) === "1";
        if (hadSubmitted) {
            removeDraftSubmitMarker(submitMarkerKey);
            if (pageHasSuccessFlash()) {
                removeStoredDraft(storageKey);
                return;
            }
        }

        const storedDraft = readStoredDraft(storageKey);
        if (!storedDraft) {
            return;
        }

        if (areDraftPayloadsEqual(storedDraft, initialSnapshot)) {
            removeStoredDraft(storageKey);
            return;
        }

        restoreSnapshot(storedDraft);
        noticeElement = createDraftNotice(form, formLabel, () => {
            removeStoredDraft(storageKey);
            removeDraftSubmitMarker(submitMarkerKey);
            restoreSnapshot(initialSnapshot);
            noticeElement?.remove();
            noticeElement = null;
        });
    });
}

window.toggleSidebar = toggleSidebar;

window.addEventListener("DOMContentLoaded", () => {
    restoreSidebarPreference();
    bindSidebarToggle();
    bindStatusColors(document);
    bindStatusColorsOnSave();
    bindFilterPanelState();
    bindAutoSubmitControls();
    bindCopyButtons();
    bindLegacyInlineConfirms();
    bindDeclarativeConfirmForms();
    bindBatchForms();
    bindCancelStatusConfirmation();
    bindManualPaymentDateConfirmation();
    bindSensitiveValueEdits();
    bindDateFieldPickers();
    bindViewPresets();
    bindPhoneMask();
    bindCurrencyMask();
    bindCotasLaunchPreview();
    bindIrrfCalculators();
    bindSavedFilters();
    bindFormDrafts();
    scheduleWorkbenchOverflowRefresh();
});

window.addEventListener("resize", scheduleWorkbenchOverflowRefresh);
