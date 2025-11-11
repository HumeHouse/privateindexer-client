let selectedTorrentId = null;
let sortConfig = {column: null, direction: null};
let torrentsCache = [];
let currentPage = 1;
let rowsPerPage = 500;
let filterDebounceTimeout = null;

// catch ready event on document
$(document).ready(() => {
    // start loops
    fetchMainData();
    fetchUserStats();

    // listen on the filter box for user input
    $('#filter-name').on('input', function () {
        clearTimeout(filterDebounceTimeout);

        // 1 second debounce
        filterDebounceTimeout = setTimeout(() => {
            // reset pagination to first page
            currentPage = 1;
            applySortingAndRender();
        }, 1000);
    });

    // listen for clicks on the delete button and call delete function
    $('#confirm-delete-button').on("click", deleteTorrent);

    // listen for rows count dropdown change
    $("#rows-per-page").on("change", updateRowsPerPage);

    // listen for header clicks to trigger sorting
    $(".sortable .sort-label").css("cursor", "pointer").click(function (e) {
        e.stopPropagation();
        const column = $(this).closest("th").data("column");

        if (sortConfig.column === column) {
            if (sortConfig.direction === "asc") {
                sortConfig.direction = "desc";
            } else if (sortConfig.direction === "desc") {
                sortConfig.column = null;
                sortConfig.direction = null;
            } else {
                sortConfig.direction = "asc";
            }
        } else {
            sortConfig.column = column;
            sortConfig.direction = "asc";
        }

        // sort then display the torrent data in the page
        applySortingAndRender();
    });
});

function toast(message, color, duration = 5) {
    const toastID = "toast-" + Math.random().toString(36).substring(3, 9);
    let toastContent;
    toastContent = `
        <div id="${toastID}" class="toast align-items-center border-0 text-bg-${color}" role="alert">
          <div class="d-flex">
            <div class="toast-body fw-bold">${message}</div>
          </div>
        </div>
    `;
    $("#toaster").append(toastContent);
    const toastObject = new bootstrap.Toast($(`#${toastID}`), {"delay": duration * 1000});
    toastObject.show();
}

function updateRowsPerPage() {
    rowsPerPage = parseInt($(this).val());
    currentPage = 1;
    applySortingAndRender();
}

function fetchMainData() {
    fetch("/dashboard/maindata")
        .then(response => {
            if (response.ok) {
                return response.json();
            } else {
                throw new Error(`PrivateIndexer client API error: ${response.status}`);
            }
        })
        .then(data => {
            // keep the data stored in variable for later use
            torrentsCache = data["torrents"];

            // sort then display the torrent data in the page
            applySortingAndRender();

            // update selected torrent info panel
            if (selectedTorrentId) {
                const updatedTorrent = data["torrents"].find(t => t["infohash_v1"] === selectedTorrentId);
                if (updatedTorrent) populateInfoPanel(updatedTorrent);
            }

            // update client-visible stats
            updateClientStats(data["torrents"], data["server_state"]);

            // update the scanner progress
            updateScannerStats(data["scanner_status"]);

            // 5 second interval
            setTimeout(fetchMainData, 5000);
        })
        .catch(e => {
            console.error("Error fetching data from client API, delayed 30s:", e);
            toast("Error fetching data from client API", "danger");

            // delay next interval 30 seconds
            setTimeout(fetchMainData, 30000);
        });
}

function fetchUserStats() {
    fetch("/dashboard/user")
        .then(response => {
            if (response.ok) {
                return response.json();
            } else {
                throw new Error(`PrivateIndexer server error: ${response.status}`);
            }
        })
        .then(data => {
            // update user stats elements
            $("#torrents-added").text(data["torrents_added_total"]);
            $("#currently-seeding").text(data["currently_seeding"]);
            $("#total-upload").text(formatBytes(data["total_upload"]));
            $("#currently-leeching").text(data["currently_leeching"]);
            $("#total-download").text(formatBytes(data["total_download"]));
            $("#server-ratio").text(formatRatio(data["server_ratio"]));
            $("#peers-total").text(data["peers_on_user_torrents"]);
            $("#grabs-total").text(data["grabs_total"]);

            // 10 second interval
            setTimeout(fetchUserStats, 10000);
        })
        .catch(e => {
            console.error("Error fetching user stats from server, delayed 60s:", e);
            toast("Error fetching user stats from server", "danger");

            // reset all displayed values to "?"
            $("#torrents-added, #currently-seeding, #total-upload, #currently-leeching, #total-download, #server-ratio, #peers-total, #grabs-total")
                .text("?");

            // delay next interval 60 seconds
            setTimeout(fetchUserStats, 60000);
        });
}

function deleteTorrent() {
    if (selectedTorrentId === null) {
        return;
    }
    fetch("/dashboard/delete_torrent?" + new URLSearchParams({
        "torrent_hash": selectedTorrentId,
        "remove_downloads": $("#delete-file-checkbox").is(":checked")
    }).toString(), {
        method: "POST"
    })
        .then(response => {
            if (response.ok) {
                toast("Torrent deleted successfully", "success");
                selectedTorrentId = null;
            } else {
                toast("Failed to delete torrent, check logs", "danger");
            }
        })
        .catch(e => {
            console.error("Error deleting torrent:", e);
            toast("Error while trying to delete torrent, check logs", "danger");
        });
}

// adds up/down arrows to columns when sorting
function updateSortIndicators() {
    $("table thead th.sortable").each(function () {
        const $label = $(this).find(".sort-label");
        let text = $label.text().trim().replace(/ ▲| ▼/, "  ");
        if (sortConfig.column === $(this).data("column")) {
            if (sortConfig.direction === "asc") text += " ▲"; else if (sortConfig.direction === "desc") text += " ▼";
        }
        $label.text(text);
    });
}

function updateScannerStats(scanner_status) {
    let state = scanner_status["state"];
    let total = scanner_status["total_items"];
    let done = scanner_status["done_items"];

    let scannerStatus;

    if (state === 1) {
        // scan task is in the pre-processing state
        scannerStatus = `
                <div>
                    <span class="fw-bold text-warning">
                        Scan: Pre-scan
                        <div class="spinner-border spinner-border-sm text-warning ms-2" role="status"></div>
                    </span>
                </div>
            `;
    } else if (state === 2) {
        // scan task is in the scanning state
        let percent = ((done / total) * 100).toFixed(1);
        scannerStatus = `
                <div>
                    <span class="fw-bold text-info">
                        Scan: Scanning
                        <div class="spinner-border spinner-border-sm text-info ms-2" role="status"></div>
                    </span><br>
                    <span class="fw-bold">${done} / ${total}</span>
                    <span class="text-muted">(${percent}%)</span>
                    <div class="progress mt-1" style="height: 10px;">
                        <div class="progress-bar bg-info" role="progressbar" style="width: ${percent}%;">
                        </div>
                    </div>
                </div>
            `;
    } else if (state === 3) {
        // scan task is in the scanning state
        let percent = ((done / total) * 100).toFixed(1);
        scannerStatus = `
                <div>
                    <span class="fw-bold text-primary">
                        Scan: Processing
                        <div class="spinner-border spinner-border-sm text-primary ms-2" role="status"></div>
                    </span><br>
                    <span class="fw-bold">${done} / ${total}</span>
                    <span class="text-muted">(${percent}%)</span>
                    <div class="progress mt-1" style="height: 10px;">
                        <div class="progress-bar bg-primary" role="progressbar" style="width: ${percent}%;">
                        </div>
                    </div>
                </div>
            `;
    } else if (state === 4) {
        // scan task is in the post-scan state
        scannerStatus = `
                <div>
                    <span class="fw-bold text-success">
                        Scan: Post-processing
                        <div class="spinner-border spinner-border-sm text-success ms-2" role="status"></div>
                    </span>
                </div>
            `;
    } else {
        // nothing is scanning, just show idle
        scannerStatus = `
                <div>
                    <span class="fw-bold text-secondary">Scan: Idle</span>
                </div>
            `;
    }

    $("#scanner-status").html(scannerStatus);
}

function updateClientStats(torrents, stats) {
    let seeding = 0, leeching = 0, totalPeers = 0;

    torrents.forEach(torrent => {
        let t_state = torrent["state"];
        if (["uploading", "stalledUP"].includes(t_state)) seeding++;
        if (["downloading", "stalledDL", "metaDL"].includes(t_state)) leeching++;
        totalPeers += (torrent["num_seeds"] || 0) + (torrent["num_leechs"] || 0);
    });

    // update torrent counts
    $("#client-seeding").text(seeding);
    $("#client-leeching").text(leeching);
    $("#client-total").text(torrents.length);
    $("#client-peers").text(totalPeers);

    // update session stats
    $("#session-dl").text(formatBytes(stats["dl_info_data"]));
    $("#total-dl").text(formatBytes(stats["alltime_dl"]));
    $("#dl-rate").text(formatSpeed(stats["dl_info_speed"]));
    $("#session-ul").text(formatBytes(stats["up_info_data"]));
    $("#total-ul").text(formatBytes(stats["alltime_ul"]));
    $("#ul-rate").text(formatSpeed(stats["ul_info_speed"]));
    $("#global-ratio").text(formatRatio(stats["global_ratio"]));
}

function applySortingAndRender() {
    let data = [...torrentsCache];

    // sort data by actively sorted columns in sortConfig
    const sortExtractors = {
        name: row => row["name"].toLowerCase(),
        size: row => row["size"],
        progress: row => row["progress"],
        state: row => row["state"],
        num_seeds: row => row["num_seeds"] + row["num_complete"],
        peers: row => row["num_leechs"] + row["num_incomplete"],
        dlspeed: row => row["dlspeed"],
        upspeed: row => row["upspeed"],
        eta: row => row["eta"],
        ratio: row => row["ratio"],
        added_on: row => row["added_on"]
    };

    if (sortConfig.column && sortConfig.direction) {
        const extractor = sortExtractors[sortConfig.column];
        if (extractor) {
            data.sort((a, b) => {
                const valA = extractor(a);
                const valB = extractor(b);

                if (valA < valB) return sortConfig.direction === "asc" ? -1 : 1;
                if (valA > valB) return sortConfig.direction === "asc" ? 1 : -1;
                return 0;
            });
        }
    }

    // add data to table and update the arrows in headers
    updateSortIndicators();
    renderTable(data);
}

function formatState(state) {
    const mapping = {
        "error": "Error",
        "downloading": "Downloading",
        "stalledDL": "Downloading (stalled)",
        "uploading": "Seeding",
        "stalledUP": "Seeding (stalled)",
        "metaDL": "Downloading Metadata",
        "checkingDL": "Downloading (checking)",
        "checkingResumeData": "Checking Resume Data",
    };
    return mapping[state] || "Unknown";
}

function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return "0 B";
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(1) + " " + units[i];
}

function formatSpeed(bytesPerSec) {
    return formatBytes(bytesPerSec) + "/s";
}

function formatTime(seconds) {
    if (seconds < 0 || seconds === 8640000) return "∞";
    const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
    return [h, m, s].map(v => String(v).padStart(2, '0')).join(":");
}

function formatRatio(ratio) {
    return ratio === 8640000 ? "∞" : ratio.toFixed(2);
}

function renderTable(torrents) {
    const $tbody = $("#torrents-container");
    $tbody.empty();

    const filter = $('#filter-name').val().toLowerCase() || '';
    let filtered = torrents.filter(t => t["name"].toLowerCase().includes(filter));

    if (!torrents || torrents.length === 0) {
        $tbody.append(`<tr><td colspan="11" class="text-center">No torrents</td></tr>`);
        return;
    }

    const totalItems = filtered.length;
    const totalPages = Math.ceil(totalItems / rowsPerPage);
    if (currentPage > totalPages) currentPage = totalPages;
    const start = (currentPage - 1) * rowsPerPage;
    const end = start + rowsPerPage;
    const pageItems = filtered.slice(start, end);

    pageItems.forEach(torrent => {
        const rowClass = "state-" + torrent["state"];
        const progress = (torrent["progress"] * 100).toFixed(1);

        const selectedClass = torrent["infohash_v1"] === selectedTorrentId ? "selected-row" : "";

        const row = $(`<tr class="${selectedClass}" data-infohash="${torrent["infohash_v1"]}"></tr>`);

        row.toggleClass("d-none", !torrent["name"].toLowerCase().includes(filter));

        row.html(`
                <td>${torrent["name"]}</td>
                <td>${formatBytes(torrent["size"])}</td>
                <td>
                    <div class="progress" style="height:1.5rem;">
                        <div class="progress-bar bg-success" role="progressbar" style="width: ${progress}%">${progress}%</div>
                    </div>
                </td>
                <td class="${rowClass}">${formatState(torrent["state"])}</td>
                <td>${torrent["num_seeds"]} (${torrent["num_complete"]})</td>
                <td>${torrent["num_leechs"]} (${torrent["num_incomplete"]})</td>
                <td>${formatSpeed(torrent["dlspeed"])}</td>
                <td>${formatSpeed(torrent["upspeed"])}</td>
                <td>${formatTime(torrent["eta"])}</td>
                <td>${formatRatio(torrent["ratio"])}</td>
                <td>${new Date(torrent["added_on"] * 1000).toLocaleString()}</td>
            `);

        $tbody.append(row);

        // click handler
        $tbody.find("tr").last().click(() => {
            selectedTorrentId = torrent["infohash_v1"];
            $tbody.find("tr").removeClass("selected-row");
            $tbody.find(`tr[data-infohash='${selectedTorrentId}']`).addClass("selected-row");
            populateInfoPanel(torrent);
        });
    });

    // update pagination controls
    const $pagination = $("#pagination-controls");
    $pagination.empty();

    if (totalPages > 1) {
        // previous page button
        $pagination.append(`
                <li class="page-item ${currentPage === 1 ? "disabled" : ""}">
                    <a class="page-link" href="#">«</a>
                </li>
            `);

        // page number buttons
        for (let i = 1; i <= totalPages; i++) {
            $pagination.append(`
                <li class="page-item ${i === currentPage ? "active" : ""}">
                    <a class="page-link" href="#">${i}</a>
                </li>
            `);
        }

        // next page button
        $pagination.append(`
                <li class="page-item ${currentPage === totalPages ? "disabled" : ""}">
                    <a class="page-link" href="#">»</a>
                </li>
            `);

        // add event listener
        $pagination.find(".page-link").on("click", function (e) {
            e.preventDefault();
            const text = $(this).text();
            if (text === "«" && currentPage > 1) currentPage--; else if (text === "»" && currentPage < totalPages) currentPage++; else if (!isNaN(parseInt(text))) currentPage = parseInt(text);
            applySortingAndRender();
        });
    }
}

// populate bottom panel
function populateInfoPanel(torrent) {
    // show delete button if torrent is not seeding
    $("#delete-button").toggleClass("d-none", ["uploading", "stalledUP"].includes(torrent["state"]));

    // general info
    const generalHtml = `
        <div class="row g-0">
            <div class="col-md-4">
                <dl class="row mb-2 g-0">
                    <dt class="col-4 text-end">Time Active:</dt><dd class="ms-2 col-7">${formatTime(torrent["time_active"])}</dd>
                    <dt class="col-4 text-end">Downloaded:</dt><dd class="ms-2 col-7">${formatBytes(torrent["downloaded"])} (${formatBytes(torrent["downloaded_session"])} this session)</dd>
                    <dt class="col-4 text-end">Download Speed:</dt><dd class="ms-2 col-7">${formatSpeed(torrent["dlspeed"])}</dd>
                    <dt class="col-4 text-end">Ratio:</dt><dd class="ms-2 col-7">${formatRatio(torrent["ratio"])}</dd>
                    <dt class="col-4 text-end">Info Hash v1:</dt><dd class="ms-2 col-7">${torrent["infohash_v1"]}</dd>
                    <dt class="col-4 text-end">Info Hash v2:</dt><dd class="ms-2 col-7">${torrent["infohash_v2"] || "N/A"}</dd>
                </dl>
            </div>
            <div class="col-md-4">
                <dl class="row mb-2 g-0">
                    <dt class="col-4 text-end">ETA:</dt><dd class="ms-2 col-7">${formatTime(torrent["eta"])}</dd>
                    <dt class="col-4 text-end">Uploaded:</dt><dd class="ms-2 col-7">${formatBytes(torrent["uploaded"])} (${formatBytes(torrent["uploaded_session"])} this session)</dd>
                    <dt class="col-4 text-end">Seeds:</dt><dd class="ms-2 col-7">${torrent["num_seeds"]} / ${torrent["num_complete"]}</dd>
                    <dt class="col-4 text-end">Peers:</dt><dd class="ms-2 col-7">${torrent["num_leechs"]} / ${torrent["num_incomplete"]}</dd>
                </dl>
            </div>
            <div class="col-md-4">
                <dl class="row mb-2 g-0">
                    <dt class="col-4 text-end">Progress:</dt><dd class="ms-2 col-7">${(torrent["progress"] * 100).toFixed(1)}%</dd>
                    <dt class="col-4 text-end">Added On:</dt><dd class="ms-2 col-7">${new Date(torrent["added_on"] * 1000).toLocaleString()}</dd>
                    <dt class="col-4 text-end">Save Path:</dt><dd class="ms-2 col-7">${torrent["save_path"]}</dd>
                </dl>
            </div>
        </div>`;

    $("#general-content").html(generalHtml);

    // trackers table
    let trackerHtml = `<table class="table table-sm table-striped">
        <thead><tr><th>URL</th><th>Working</th><th>Next Announce</th></tr></thead>
        <tbody>`;
    torrent["trackers"].forEach(tracker => {
        let working = !!tracker["verified"];
        trackerHtml += `<tr>
                <td>${tracker["url"]}</td>
                <td class="${working ? "bg-success" : "bg-danger"}">${working ? 'Yes' : 'No'}</td>
                <td>${tracker["next_announce"] ? new Date(tracker["next_announce"] * 1000).toLocaleString() : 'N/A'}</td>
            </tr>`;
    });
    trackerHtml += `</tbody></table>`;
    $("#tracker-content").html(trackerHtml);

    // peers table
    let peersHtml = `<table class="table table-sm table-striped">
        <thead><tr><th>IP</th><th>Port</th><th>Client</th><th>Flags</th><th>Down Speed</th><th>Up Speed</th><th>Progress</th></tr></thead>
        <tbody>`;
    torrent["peers"].forEach(peer => {
        const progress = (peer["progress"] * 100).toFixed(1);
        let flagsList = "";
        let peerFlags = peer["flags"];
        if (peerFlags && peerFlags.length > 0) {
            peerFlags.forEach(flag => {
                let className;
                switch (flag[0]) {
                    case "D":
                        className = "flag-downloading";
                        break;
                    case "d":
                        className = "flag-interested-choked";
                        break;
                    case "U":
                        className = "flag-uploading";
                        break;
                    case "u":
                        className = "flag-not-uploading";
                        break;
                    case "S":
                        className = "flag-snubbed";
                        break;
                    case "K":
                    case "?":
                        className = "flag-not-interested";
                        break;
                    case "E":
                    case "e":
                        className = "flag-encrypted";
                        break;
                    default:
                        className = "flag-general";
                        break;
                }
                flagsList += `<span style="cursor:help;" class="badge me-1 ${className}" title="${flag[1]}">${flag[0]}</span>`;
            });
        }
        peersHtml += `<tr>
                <td>${peer["ip"]}</td>
                <td>${peer["port"]}</td>
                <td>${peer["client"]}</td>
                <td>${flagsList}</td>
                <td>${formatSpeed(peer["down_speed"])}</td>
                <td>${formatSpeed(peer["up_speed"])}</td>
                <td>
                    <div class="progress" style="height:1.5rem;">
                    <div class="progress-bar bg-success" role="progressbar" style="width: ${progress}%">${progress}%</div>
                    </div>
                </td>
            </tr>`;
    });
    peersHtml += `</tbody></table>`;
    $("#peers-content").html(peersHtml);
}