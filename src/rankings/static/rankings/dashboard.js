/* Pool overview dashboard: fetch aggregated metrics as JSON and draw them with
   Chart.js. Each card owns loading / empty / error / content states; a single
   fetch feeds every card, so a network failure flips them all to error. */
(function () {
    "use strict";

    var root = document.getElementById("dashboard-root");
    if (!root) {
        return;
    }

    var PALETTE = [
        "#38bdf8", "#a78bfa", "#34d399", "#f472b6", "#fbbf24",
        "#f87171", "#22d3ee", "#c084fc", "#a3e635", "#60a5fa",
    ];
    var USER_COLOR = "#fb923c";
    var GRID_COLOR = "rgba(115, 115, 115, 0.18)";
    var TICK_COLOR = "#a3a3a3";

    // Hall da Fama: cada trofeu é um card gerado deste config. `fmt` formata o
    // valor que vem do backend; `accent` colore o nome do trofeu.
    var HALL = [
        { key: "exact_scores", emoji: "🎯", label: "Rei dos Placares", hint: "placares exatos cravados", accent: "text-orange-400", fmt: function (v) { return v; } },
        { key: "biggest_climb", emoji: "📈", label: "Maior Escalada", hint: "posições ganhas numa arrancada", accent: "text-emerald-400", fmt: function (v) { return "+" + v; } },
        { key: "longest_streak", emoji: "🔥", label: "Pegando Fogo", hint: "jogos seguidos pontuando", accent: "text-red-400", fmt: function (v) { return v; } },
        { key: "best_day", emoji: "☀️", label: "Dia Iluminado", hint: "maior pontuação num só dia", accent: "text-yellow-400", fmt: function (v) { return v + " pts"; }, sub: function (e) { return e && e.day ? "em " + fmtDia(e.day) : ""; } },
        { key: "pe_frio", emoji: "🥶", label: "Pé Frio", hint: "jogos finalizados sem pontuar", accent: "text-sky-400", fmt: function (v) { return v; } },
        { key: "maior_queda", emoji: "📉", label: "Tobogã", hint: "despencou no ranking", accent: "text-purple-400", fmt: function (v) { return "-" + v; } },
        { key: "lanterna", emoji: "🔦", label: "Lanterna", hint: "segurando a tocha", accent: "text-neutral-300", fmt: function (v) { return "#" + v; } },
        { key: "ioio", emoji: "🪀", label: "Ioiô", hint: "oscilou muito (soma das mudanças de posição)", accent: "text-pink-400", fmt: function (v) { return v; } },
    ];

    function card(name) {
        return root.querySelector('[data-card="' + name + '"]');
    }

    function setState(el, state) {
        if (!el) {
            return;
        }
        el.querySelectorAll("[data-state]").forEach(function (node) {
            node.hidden = node.getAttribute("data-state") !== state;
        });
    }

    function errorAll() {
        ["progress", "evolution", "utilization", "hall"].forEach(function (name) {
            setState(card(name), "error");
        });
    }

    // Run a component renderer in isolation: one failing card never blanks the
    // others. On error, flip that card to its error state and keep going.
    function safe(name, fn) {
        try {
            fn();
        } catch (err) {
            console.error("dashboard: failed to render '" + name + "'", err);
            setState(card(name), "error");
        }
    }

    // Draw a chart without letting a Chart.js failure (e.g. the CDN was blocked)
    // throw out of the renderer — the surrounding text/KPIs stay on screen.
    function drawChart(canvasId, config) {
        if (typeof window.Chart !== "function") {
            console.error("dashboard: Chart.js not loaded; skipping chart '" + canvasId + "'");
            return;
        }
        try {
            new window.Chart(document.getElementById(canvasId), config);
        } catch (err) {
            console.error("dashboard: chart '" + canvasId + "' failed", err);
        }
    }

    function fmtPercent(value) {
        return (Number(value) || 0).toFixed(1).replace(".", ",") + "%";
    }

    function fmtDia(iso) {
        var parts = String(iso || "").split("-");
        return parts.length === 3 ? parts[2] + "/" + parts[1] : "";
    }

    function renderProgress(data) {
        var el = card("progress");
        var p = data.progress || {};
        el.querySelector('[data-field="percent"]').textContent = fmtPercent(p.percent);
        el.querySelector('[data-field="counts"]').textContent =
            (p.finished_matches || 0) + " de " + (p.total_matches || 0) + " jogos";
        el.querySelector('[data-field="phase"]').textContent = p.current_phase || "—";
        el.querySelector('[data-field="next-match"]').textContent =
            p.next_match && p.next_match.label ? p.next_match.label : "—";
        setState(el, "content");

        var remaining = Math.max((p.total_matches || 0) - (p.finished_matches || 0), 0);
        drawChart("chart-progress", {
            type: "doughnut",
            data: {
                labels: ["Concluídos", "Restantes"],
                datasets: [
                    {
                        data: [p.finished_matches || 0, remaining],
                        backgroundColor: [USER_COLOR, "#404040"],
                        borderWidth: 0,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "75%",
                plugins: { legend: { display: false }, tooltip: { enabled: true } },
            },
        });
    }

    function renderKpis(data) {
        var el = card("kpis");
        var k = data.kpis || {};
        el.querySelector('[data-field="position"]').textContent = k.position ? "#" + k.position : "—";
        el.querySelector('[data-field="points"]').textContent = (k.points || 0) + " pts";
        el.querySelector('[data-field="gap"]').textContent = k.is_leader ? "Líder" : (k.gap_to_leader || 0) + " pts";
        el.querySelector('[data-field="utilization"]').textContent = fmtPercent(k.utilization);
    }

    var evolutionChart = null;

    function buildEvolutionConfig(serie) {
        return {
            type: "line",
            data: {
                datasets: [
                    {
                        label: serie.label,
                        data: serie.points.map(function (point) {
                            return { x: point.round, y: point.position, pts: point.points };
                        }),
                        borderColor: USER_COLOR,
                        backgroundColor: USER_COLOR,
                        borderWidth: 4,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        tension: 0.45,
                        cubicInterpolationMode: "monotone",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "nearest", intersect: false },
                scales: {
                    x: {
                        type: "linear",
                        title: { display: true, text: "Rodada", color: TICK_COLOR },
                        ticks: { precision: 0, color: TICK_COLOR },
                        grid: { color: GRID_COLOR },
                    },
                    y: {
                        reverse: true,
                        min: 1,
                        title: { display: true, text: "Posição", color: TICK_COLOR },
                        ticks: { precision: 0, color: TICK_COLOR },
                        grid: { color: GRID_COLOR },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                var raw = ctx.raw || {};
                                return ctx.dataset.label + ": #" + raw.y + " (" + raw.pts + " pts)";
                            },
                        },
                    },
                },
            },
        };
    }

    function renderEvolution(data) {
        var el = card("evolution");
        var evo = data.evolution || {};
        var all = evo.all || [];
        var select = el.querySelector("[data-evolution-select]");
        if (!all.length) {
            setState(el, "empty");
            return;
        }
        setState(el, "content");

        select.textContent = "";
        all.forEach(function (serie) {
            var opt = document.createElement("option");
            opt.value = String(serie.participant_id);
            opt.textContent = serie.label;
            select.appendChild(opt);
        });

        var current = evo.current_participant_id;
        var hasCurrent = all.some(function (serie) {
            return String(serie.participant_id) === String(current);
        });
        select.value = String(hasCurrent ? current : all[0].participant_id);

        function draw() {
            var serie = all.find(function (s) {
                return String(s.participant_id) === String(select.value);
            });
            if (!serie || typeof window.Chart !== "function") {
                return;
            }
            if (evolutionChart) {
                evolutionChart.destroy();
            }
            try {
                evolutionChart = new window.Chart(
                    document.getElementById("chart-evolution"),
                    buildEvolutionConfig(serie)
                );
            } catch (err) {
                console.error("dashboard: chart 'chart-evolution' failed", err);
            }
        }

        select.onchange = draw;
        draw();
    }

    function renderUtilization(data) {
        var el = card("utilization");
        var util = data.utilization || {};
        var rows = util.rows || [];
        if (!util.has_data || !rows.length) {
            setState(el, "empty");
            return;
        }
        setState(el, "content");
        el.querySelector("[data-state='content'] .relative").style.setProperty("--bars", rows.length);

        drawChart("chart-utilization", {
            type: "bar",
            data: {
                labels: rows.map(function (r) {
                    return r.label;
                }),
                datasets: [
                    {
                        data: rows.map(function (r) {
                            return r.percent;
                        }),
                        backgroundColor: rows.map(function (r) {
                            return r.is_current_user ? USER_COLOR : "#38bdf8";
                        }),
                        borderRadius: 4,
                    },
                ],
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        beginAtZero: true,
                        ticks: { color: TICK_COLOR, callback: function (v) { return v + "%"; } },
                        grid: { color: GRID_COLOR },
                    },
                    y: { ticks: { color: TICK_COLOR }, grid: { display: false } },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                return fmtPercent(ctx.parsed.x);
                            },
                        },
                    },
                },
            },
        });
    }

    // Build one trophy card. Winner name/value go in via textContent (never
    // innerHTML) since they carry user data. No winner → dimmed card with "—".
    function buildTrophy(cfg, entry) {
        var has = !!entry;
        var cardEl = document.createElement("div");
        cardEl.className =
            "flex flex-col items-center gap-1 rounded-xl border border-neutral-800 bg-neutral-950/60 p-4 text-center" +
            (has ? "" : " opacity-40");

        var emoji = document.createElement("span");
        emoji.className = "text-3xl";
        emoji.textContent = cfg.emoji;

        var label = document.createElement("p");
        label.className = "mt-1 text-xs font-semibold uppercase tracking-wide " + cfg.accent;
        label.textContent = cfg.label;

        var winner = document.createElement("p");
        winner.className = "text-sm font-semibold text-neutral-100";
        winner.textContent = has ? entry.username : "—";

        var value = document.createElement("span");
        value.className = "rounded-full bg-neutral-800 px-2.5 py-0.5 text-sm font-bold text-neutral-100";
        value.textContent = has ? cfg.fmt(entry.value) : "—";

        var subText = cfg.sub ? cfg.sub(entry) : "";
        var sub = null;
        if (has && subText) {
            sub = document.createElement("p");
            sub.className = "text-[11px] font-medium text-neutral-300";
            sub.textContent = subText;
        }

        var hint = document.createElement("p");
        hint.className = "text-[11px] text-neutral-400";
        hint.textContent = cfg.hint;

        cardEl.appendChild(emoji);
        cardEl.appendChild(label);
        cardEl.appendChild(winner);
        cardEl.appendChild(value);
        if (sub) {
            cardEl.appendChild(sub);
        }
        cardEl.appendChild(hint);
        return cardEl;
    }

    function renderHall(data) {
        var el = card("hall");
        var hof = data.hall_of_fame || {};
        var grid = el.querySelector("[data-hof-grid]");
        grid.textContent = "";
        HALL.forEach(function (cfg) {
            grid.appendChild(buildTrophy(cfg, hof[cfg.key]));
        });
        setState(el, "content");
    }

    fetch(root.getAttribute("data-url"), { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (response) {
            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }
            return response.json();
        })
        .then(function (data) {
            safe("progress", function () { renderProgress(data); });
            safe("kpis", function () { renderKpis(data); });
            safe("evolution", function () { renderEvolution(data); });
            safe("utilization", function () { renderUtilization(data); });
            safe("hall", function () { renderHall(data); });
            if (window.lucide) {
                window.lucide.createIcons();
            }
        })
        .catch(function (err) {
            // Only reached when the fetch itself fails (network/HTTP error).
            console.error("dashboard: data request failed", err);
            errorAll();
        });
})();
