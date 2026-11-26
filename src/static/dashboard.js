'use strict';

const $ = id => document.getElementById(id);
const tableBody = $('torrents-container');
const pagination = $('pagination-controls');
const headers = [...document.querySelectorAll('th[data-column]')];
const deleteDialog = $('delete-modal');
const tabs = [$('general-tab'), $('peers-tab')];
const seedStates = new Set(['uploading', 'stalledUP']);
const serverStats = {
    'torrents-added': 'torrents_added_total', 'currently-seeding': 'currently_seeding',
    'total-upload': 'total_upload', 'currently-leeching': 'currently_leeching',
    'total-download': 'total_download', 'server-ratio': 'server_ratio',
    'grabs-total': 'grabs_total', popularity: 'popularity',
};
let torrents = [];
let selected = null;
let page = 1;
let rowsPerPage = Number($('rows-per-page').value);
let filterTimer;
let sort = {column: 'added_on', direction: 'desc'};
let pendingRemoval = false;

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function cell(row, value, className) {
    const td = row.insertCell();
    if (value instanceof Node) td.append(value);
    else td.textContent = value ?? '';
    if (className) td.className = className;
    return td;
}

function button(text, label) {
    const node = element('button', text, 'button');
    node.type = 'button';
    if (label) node.setAttribute('aria-label', label);
    return node;
}

function toast(message, error = false) {
    const item = element('div', null, `toast${error ? ' bad' : ''}`);
    const dismiss = button('Dismiss', 'Dismiss notification');
    item.append(element('p', message), dismiss);
    $('toaster').append(item);
    let timer;

    function remove() {
        clearTimeout(timer);
        if (item.contains(document.activeElement)) $('torrents').focus({preventScroll: true});
        item.remove();
    }

    function resume() {
        clearTimeout(timer);
        if (!item.matches(':hover, :focus-within')) timer = setTimeout(remove, 5000);
    }

    for (const event of ['pointerenter', 'focusin']) item.addEventListener(event, () => clearTimeout(timer));
    for (const event of ['pointerleave', 'focusout']) item.addEventListener(event, () => queueMicrotask(resume));
    dismiss.addEventListener('click', remove);
    resume();
}

async function json(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

async function fetchMainData() {
    let next = 5000;
    try {
        const data = await json('/dashboard/maindata');
        if (!Array.isArray(data?.torrents)) throw new Error('Expected a torrent list');
        torrents = data.torrents.filter(item => item && typeof item === 'object');
        if (selected !== null) {
            const current = torrents.find(item => item.infohash === selected.infohash);
            if (current) selected = current;
            else if (!pendingRemoval && !deleteDialog.open) selected = null;
        }
        renderTable();
        renderDetails();
        updateClientStats(data.client_stats ?? {});
        updateScanner(data.scanner_status);
    } catch (error) {
        next = 30000;
        console.error('Error fetching client data:', error);
        toast('Error fetching data from client API', true);
        if ($('table-summary').textContent === 'Loading torrents...') $('table-summary').textContent = 'Could not load torrents. Retrying automatically.';
    } finally {
        setTimeout(fetchMainData, next);
    }
}

async function fetchUserStats() {
    let next = 30000;
    try {
        const data = await json('/dashboard/user');
        for (const [id, key] of Object.entries(serverStats)) {
            const value = data[key];
            $(id).textContent = value == null ? '?' : id === 'server-ratio' ? formatRatio(value) :
                id === 'total-upload' || id === 'total-download' ? formatBytes(value) : value;
        }
    } catch (error) {
        next = 60000;
        console.error('Error fetching server statistics:', error);
        for (const id of Object.keys(serverStats)) $(id).textContent = '?';
        toast('Error fetching user stats from server', true);
    } finally {
        setTimeout(fetchUserStats, next);
    }
}

function updateClientStats(stats) {
    const values = {
        'client-leeching': stats.downloading_torrents,
        'client-seeding': stats.seeding_torrents,
        'client-total': torrents.length,
        'client-peers': stats.peers_connected,
        'total-dl': formatBytes(stats.total_download),
        'total-ul': formatBytes(stats.total_upload),
        'dl-rate': formatSpeed(stats.download_speed),
        'ul-rate': formatSpeed(stats.upload_speed),
        'global-ratio': formatRatio(stats.client_ratio ?? 0)
    };
    for (const [id, value] of Object.entries(values)) $(id).textContent = value ?? '0';
}

function updateScanner(scanner) {
    const state = scanner?.state;
    const total = Number(scanner?.total_items) || 0;
    const done = Number(scanner?.done_items) || 0;
    const percent = total > 0 ? (done / total) * 100 : 0;
    const inProgress = state === 2 || state === 3;
    $('scan-state').textContent = ['Idle', 'Pre-scan', 'Scanning', 'Processing', 'Post-processing'][state] ?? 'Status unavailable';
    $('scan-state').classList.toggle('blue', state >= 1 && state <= 4);
    $('scan-summary').hidden = $('scan-progress').hidden = !inProgress;
    $('scan-summary').textContent = `${done} / ${total} (${percent.toFixed(1)}%)`;
    $('scan-progress').value = percent;
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

function formatBits(bits) {
    if (!bits || bits <= 0) return "0 b";
    const units = ["b", "Kb", "Mb", "Gb", "Tb"];
    const i = Math.floor(Math.log(bits) / Math.log(1000));
    return (bits / Math.pow(1000, i)).toFixed(1) + " " + units[i];
}

function formatSpeed(bytesPerSec) {
    const bitsPerSec = bytesPerSec * 8;
    return formatBits(bitsPerSec) + "ps";
}

function formatTime(seconds) {
    if (seconds < 0 || seconds === -1) return "∞";

    const d = Math.floor(seconds / 86400);
    seconds %= 86400;

    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);

    const time = [h, m, s].map(v => String(v).padStart(2, '0')).join(":");
    return d > 0 ? `${d}d ${time}` : time;
}

function formatRatio(ratio) {
    return ratio === -1 ? "∞" : ratio.toFixed(2);
}

function formatCategory(category) {
    switch (category) {
        case 2000:
            return "Radarr";
        case 3000:
            return "Lidarr";
        case 5000:
            return "Sonarr";
        default:
            return "Unknown"
    }
}

const normalized = value => String(value ?? '').toLowerCase();
const pairs = (connected, total) => `${connected ?? 0} (${total ?? 0})`;
const date = seconds => new Date((seconds ?? 0) * 1000).toLocaleString();
const sortValue = {
    name: row => normalized(row.name),
    seeds: row => row.connected_seeds + row.total_seeds,
    peers: row => row.connected_peers + row.total_peers,
};

function progressCell(value, label) {
    const percent = (Number(value) || 0) * 100;
    const wrapper = element('div', null, 'progress');
    const bar = element('progress', null, percent >= 100 ? 'complete' : null);
    bar.max = 100;
    bar.value = percent;
    bar.setAttribute('aria-label', label);
    wrapper.append(bar, element('span', `${percent.toFixed(1)}%`));
    return wrapper;
}

function renderTable(resetScroll = false) {
    const focusedRow = document.activeElement?.closest('#torrents-container tr');
    const focusedHash = focusedRow?.dataset.infohash;
    const activePage = document.activeElement?.dataset.pageAction;
    const query = normalized($('filter-name').value).trim();
    const filtered = torrents.filter(item => normalized(item.name).includes(query));
    if (sort.column) {
        const get = sortValue[sort.column] ?? (row => row[sort.column]);
        const sign = sort.direction === 'asc' ? 1 : -1;
        filtered.sort((a, b) => get(a) < get(b) ? -sign : get(a) > get(b) ? sign : 0);
    }
    for (const header of headers) {
        const label = header.firstElementChild;
        const direction = header.dataset.column === sort.column ? sort.direction : null;
        if (direction) header.setAttribute('aria-sort', direction === 'asc' ? 'ascending' : 'descending');
        else header.removeAttribute('aria-sort');
        label.textContent = label.dataset.label + (direction ? (direction === 'asc' ? ' ▲' : ' ▼') : '');
    }
    const pages = Math.ceil(filtered.length / rowsPerPage);
    page = Math.min(page, pages || 1);
    const start = (page - 1) * rowsPerPage;
    const end = Math.min(start + rowsPerPage, filtered.length);
    $('table-summary').textContent = filtered.length ? `Showing ${start + 1}–${end} of ${filtered.length}${query ? ` matching (${torrents.length} total)` : ''}` :
        torrents.length ? 'No torrents match the current filter.' : 'No torrents.';
    const fragment = document.createDocumentFragment();
    let focusTarget;
    for (let i = start; i < end; i++) {
        const item = filtered[i];
        const row = element('tr');
        row.dataset.infohash = item.infohash ?? '';
        const isSelected = selected !== null && selected.infohash === item.infohash;
        row.classList.toggle('selected-row', isSelected);
        const name = element('button', item.name ?? 'Unnamed torrent');
        name.type = 'button';
        name.title = name.textContent;
        name.setAttribute('aria-pressed', String(isSelected));
        if (focusedHash && row.dataset.infohash === focusedHash) focusTarget = name;
        cell(row, name, 'name');
        cell(row, element('span', formatCategory(item.category), `badge category-${Number(item.category) || 0}`));
        cell(row, formatBytes(item.size));
        cell(row, progressCell(item.progress, 'Torrent progress'));
        const state = /^[A-Za-z]+$/.test(item.state) ? item.state : 'unknown';
        cell(row, element('span', formatState(item.state), `badge state-${state}`));
        cell(row, pairs(item.connected_seeds, item.total_seeds));
        cell(row, pairs(item.connected_peers, item.total_peers));
        cell(row, formatSpeed(item.download_speed));
        cell(row, formatSpeed(item.upload_speed));
        cell(row, formatTime(item.eta ?? -1));
        cell(row, date(item.added_on));
        row.addEventListener('click', () => {
            selected = item;
            const previous = tableBody.querySelector('.selected-row');
            if (previous) {
                previous.classList.remove('selected-row');
                previous.querySelector('button').setAttribute('aria-pressed', 'false');
            }
            row.classList.add('selected-row');
            name.setAttribute('aria-pressed', 'true');
            renderDetails();
        });
        fragment.append(row);
    }
    if (!filtered.length) cell(fragment.appendChild(element('tr')), $('table-summary').textContent, 'empty').colSpan = 11;
    tableBody.replaceChildren(fragment);
    renderPagination(pages, activePage);
    if (focusTarget) focusTarget.focus({preventScroll: true});
    else if (focusedRow) $('torrents-table-container').focus({preventScroll: true});
    if (resetScroll) $('torrents-table-container').scrollTop = 0;
}

function renderPagination(pages, activePage) {
    pagination.hidden = pages < 2;
    const links = document.createDocumentFragment();
    let focusTarget;

    function add(text, label, action, target, disabled = false) {
        const link = button(text, label);
        link.dataset.pageAction = action;
        link.disabled = disabled;
        if (action === String(page)) link.setAttribute('aria-current', 'page');
        if (action === activePage && !disabled) focusTarget = link;
        link.addEventListener('click', () => {
            page = target;
            renderTable(true);
        });
        links.append(link);
    }

    if (pages >= 2) {
        add('‹', 'Previous page', 'previous', page - 1, page === 1);
        for (let i = 1; i <= pages; i++) add(String(i), `Page ${i}`, String(i), i);
        add('›', 'Next page', 'next', page + 1, page === pages);
    }
    pagination.replaceChildren(links);
    if (activePage) (focusTarget ?? pagination.querySelector('[aria-current]') ?? $('rows-per-page')).focus({preventScroll: true});
}

function detailsGroup(entries) {
    const list = element('dl');
    for (const [label, value, className] of entries) {
        const line = element('div');
        const detail = element('dd', null, className);
        detail.append(value instanceof Node ? value : String(value ?? 'N/A'));
        line.append(element('dt', label), detail);
        list.append(line);
    }
    return list;
}

const flags = {
    D: 'downloading', d: 'interested-choked', U: 'uploading', u: 'not-uploading', S: 'snubbed',
    K: 'not-interested', '?': 'not-interested', E: 'encrypted', e: 'encrypted'
};

function renderDetails() {
    const general = $('general-content');
    const peers = $('peers-content');
    $('delete-button').hidden = !selected || seedStates.has(selected.state);
    $('delete-button').disabled = pendingRemoval;
    if (!selected) {
        $('selected-name').textContent = 'Select a torrent to inspect its details.';
        general.textContent = 'Select a torrent to view details.';
        peers.textContent = 'Select a torrent to view peers.';
        return;
    }
    const item = selected;
    $('selected-name').textContent = item.name ?? 'Unnamed torrent';
    const tracker = item.tracker ?? {};
    const next = tracker.next_announce ? formatTime(tracker.next_announce - Math.floor(Date.now() / 1000)) : 'N/A';
    const trackerStatus = element('span');
    trackerStatus.append(element('strong', tracker.verified ? 'Working' : 'Not Working', tracker.verified ? 'good' : 'bad'), ` (Next announce ${next})`);
    const grid = element('div', null, 'detail-grid');
    grid.append(detailsGroup([['Session download', formatBytes(item.session_download)], ['Download speed', formatSpeed(item.download_speed)],
            ['Progress', `${((Number(item.progress) || 0) * 100).toFixed(1)}%`], ['Info hash', item.infohash || 'N/A', 'mono']]),
        detailsGroup([['Session upload', formatBytes(item.session_upload)], ['Upload speed', formatSpeed(item.upload_speed)],
            ['Seeds (total)', pairs(item.connected_seeds, item.total_seeds)], ['Peers (total)', pairs(item.connected_peers, item.total_peers)]]),
        detailsGroup([['Tracker status', trackerStatus], ['Added on', date(item.added_on)], ['Save path', item.save_path, 'mono']]));
    general.replaceChildren(grid);
    if (!Array.isArray(item.peers) || !item.peers.length) {
        peers.textContent = 'No connected peers.';
        return;
    }
    const table = element('table');
    table.setAttribute('aria-label', 'Connected peers');
    const head = table.createTHead().insertRow();
    for (const text of ['IP', 'Port', 'Client', 'Flags', 'Down speed', 'Up speed', 'Progress']) {
        const th = element('th', text);
        th.scope = 'col';
        head.append(th);
    }
    const body = table.createTBody();
    for (const peer of item.peers) {
        const row = body.insertRow();
        cell(row, peer.ip);
        cell(row, peer.port);
        cell(row, peer.client);
        const flagCell = cell(row, '', 'flags');
        for (const [flag, meaning] of peer.flags ?? []) {
            const badge = element('span', flag, `badge flag-${Object.hasOwn(flags, flag) ? flags[flag] : 'general'}`);
            badge.title = meaning;
            flagCell.append(badge);
        }
        cell(row, formatSpeed(peer.download_speed));
        cell(row, formatSpeed(peer.upload_speed));
        cell(row, progressCell(peer.progress, 'Peer progress'));
    }
    peers.replaceChildren(table);
}

function activateTab(tab) {
    for (const current of tabs) {
        const active = current === tab;
        current.setAttribute('aria-selected', String(active));
        current.tabIndex = active ? 0 : -1;
        $(current.getAttribute('aria-controls')).hidden = !active;
    }
}

for (const tab of tabs) {
    tab.addEventListener('click', () => activateTab(tab));
    tab.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1) : tabs.find(item => item !== tab);
        activateTab(next);
        next.focus();
    });
}

$('delete-button').addEventListener('click', () => {
    if (!selected?.infohash || seedStates.has(selected.state) || pendingRemoval) return;
    $('delete-name').textContent = selected.name ?? 'Unnamed torrent';
    deleteDialog.showModal();
});
$('cancel-delete').addEventListener('click', () => deleteDialog.close());
$('delete-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (pendingRemoval || !selected?.infohash) return;
    if (seedStates.has(selected.state)) {
        deleteDialog.close();
        toast('This torrent is now seeding and cannot be deleted here.', true);
        return;
    }
    const hash = selected.infohash;
    const params = new URLSearchParams({torrent_hash: hash, remove_downloads: $('delete-file-checkbox').checked});
    pendingRemoval = true;
    deleteDialog.close();
    $('torrents').focus({preventScroll: true});
    renderDetails();
    try {
        const response = await fetch(`/dashboard/delete_torrent?${params}`, {method: 'POST'});
        if (!response.ok) {
            toast('Failed to delete torrent, check logs', true);
            return;
        }
        torrents = torrents.filter(item => item.infohash !== hash);
        if (selected?.infohash === hash) selected = null;
        renderTable();
        toast('Torrent deleted successfully');
    } catch (error) {
        console.error('Error deleting torrent:', error);
        toast('Error while trying to delete torrent, check logs', true);
    } finally {
        pendingRemoval = false;
        renderDetails();
    }
});

$('filter-name').addEventListener('input', () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
        page = 1;
        renderTable(true);
    }, 1000);
});
$('rows-per-page').addEventListener('change', event => {
    rowsPerPage = Number(event.target.value);
    page = 1;
    renderTable(true);
});
for (const header of headers) {
    const label = header.firstElementChild;
    label.dataset.label = label.textContent;
    label.addEventListener('click', () => {
        const column = header.dataset.column;
        sort = sort.column !== column ? {column, direction: 'asc'} : sort.direction === 'asc' ?
            {column, direction: 'desc'} : {column: null, direction: null};
        renderTable();
    });
}
void fetchMainData();
void fetchUserStats();
