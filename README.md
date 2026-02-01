# PrivateIndexer Client

This is the client container for the HumeHouse PrivateIndexer swarm system.
It pulls data from Radarr/Sonarr/Lidarr, creates torrents for media, and communicates with the PrivateIndexer server.
A built-in torrent client will automatically seed your media for you.
The built-in torrent client also provides qBittorrent-compatible API endpoints for usage with the *arr suite apps.
You can view a basic dashboard by visiting `http://hostname:8080/dashboard` from a browser to view status and other
statistics about your torrent client.

**NOTE:** This is a `non-root`/`rootless` container, make sure the permissions on your system match your configuration.

---

## Building

Clone this repository and simply run the following command in the directory with the `Dockerfile` and your image will be
built

```bash
docker compose build
```

You can also use the hosted pre-built image on at `ghcr.io/humehouse/privateindexer-client:latest` like in the example
below
(See [GitHub](https://github.com/HumeHouse/privateindexer-client/tags) for all version tags)

---

## Tips

- Ask your swarm operator for your API key before starting, the client will not start without validation.
- If you get an error during startup that your client is UNREACHABLE, this means you need to fix your port forwarding.
- Console logs are your friend. If you suspect an issue, check out the logs for errors and warnings.

---

## Quick Start Example (using Docker)

### Use the [example docker-compose.yml](docker-compose.yml) and adjust paths and environment variables to match your setup.

Here’s an example setup:

- My movie files are stored in `/data/media/movies` on the host
- My TV show files are stored in `/data/media/shows` on the host
- My downloads are stored in `/data/privateindexer/downloads` on the host
- My persistent data (torrents and database) for client is stored in `/humehouse/privateindexer` on the host

### 1. Configure Environment Variables

#### REQURIED VARIABLES

| Variable          | Description                                                                                                                       | Example                         |
|-------------------|-----------------------------------------------------------------------------------------------------------------------------------|---------------------------------|
| `DOWNLOADS_DIR`   | Path **inside the container** that downloads are saved to. (Make sure to mount it to the host somewhere - step 2.)                | `/data/downloads`               |
| `API_KEY`         | Your assigned API key (contact your server operator if you don’t have one).                                                       | `abcdef123456`                  |
| `INDEXER_API_URL` | URL pointing to your server operator's PrivateIndexer **Server** instance to respond to API requests and track uploaded torrents. | `https://indexer.humehouse.com` |
| `TRACKER_API_URL` | URL pointing to your server operator's PrivateIndexer **Tracker** instance to receive announcement requests.                      | `https://tracker.humehouse.com` |

#### *ARR APP VARIABLES (OPTIONAL, AT LEAST 1 REQUIRED)

| Variable         | Description                                                                                             | Example                        |
|------------------|---------------------------------------------------------------------------------------------------------|--------------------------------|
| `RADARR_URL`     | Full protocol and host string for connecting to Radarr. Port definitions are allowed at the end of URL. | `https://radarr.humehouse.com` |
| `RADARR_API_KEY` | Your Radarr API key, found under `Settings > General > Security`                                        | `abcdef123456`                 |
| `SONARR_URL`     | Same purpose as `RADARR_URL` but for Sonarr                                                             | `https://sonarr.humehouse.com` |
| `SONARR_API_KEY` | Your Sonarr API key, found under `Settings > General > Security`                                        | `abcdef123456`                 |
| `LIDARR_URL`     | Same purpose as `RADARR_URL` but for Lidarr                                                             | `https://lidarr.humehouse.com` |
| `LIDARR_API_KEY` | Your Lidarr API key, found under `Settings > General > Security`                                        |                                |

#### OPTIONAL VARIABLES

| Variable                    | Default Value     | Type                | Description                                                                                                                                                                  |
|-----------------------------|-------------------|---------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `MAX_THREADS `              | `8`               | `INTEGER`           | Number of threads to use for CPU & I/O bound tasks. Recommend matching CPU cores.                                                                                            |
| `SCAN_INTERVAL`             | `30`              | `INTEGER` (minutes) | Minutes between media library scans. We rebuild the media library from Radarr and Sonarr every scan, so be cautious setting too low.                                         |
| `SYNC_INTERVAL`             | `60`              | `INTEGER` (minutes) | Minutes between server sync task cycles. Sync cucles keep local database up-to-date with server database to ensure no torrents are missing.                                  |
| `SCAN_BATCH_SIZE`           | `128`             | `INTEGER`           | Number of items in the scan task to process at any given time. This should ideally be set to a multiple of your `MAX_THREADS` variable.                                      |
| `FASTRESUME_INTERVAL`       | `60`              | `INTEGER` (minutes) | How often (in minutes) to save fastresume data. *Setting this too low can negatively impact your disk performance.*                                                          |
| `ANNOUNCE_IP`               | **NONE**          | `TEXT` (IPv4)       | Rarely needed, advanced only. If for some reason your client is making requests from different IP than you appear to peers.                                                  |
| `TORRENTING_PORT`           | `6881`            | `INTEGER`           | Port accepting connections from other torrent clients. (Make sure to bind this to host and forward in router.)                                                               |
| `LOG_LEVEL`                 | `INFO`            | `KEYWORD` (level)   | Lowest log level to show in console. Can be `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` where `DEBUG` shows most amount of logs                                       |
| `PURGE_UNTRACKED_TORRENTS`  | `True`            | `BOOLEAN`           | Enable this to have the client purge any torent files that aren't currently tracked in the database - generally keep this enabled to help clean up old torrents.             |
| `PURGE_UNTRACKED_DOWNLOADS` | `True`            | `BOOLEAN`           | Enable this to let the scanner remove downloads which are not tracked in the database. This can free up disk space for media you no longer have tracked in *arr apps.        |
| `STALE_TORRENT_THRESHOLD`   | `30`              | `INTEGER` (days)    | Number of days a torrent is allowed to sit in downloading state before it is automatically purged from the torrent client.                                                   |
| `PURGE_DUPLICATE_SEEDS`     | `True`            | `BOOLEAN`           | Disable this to prevent purging individual torrents that are part of a tracked multi-file torrent. Enabling is useful for de-duplication.                                    |
| `CACHE_CLEAN_INTERVAL`      | `12`              | `INTEGER` (hours)   | Hours between cache clean task cycles. Cache clean is important to keep cache file size trimmed and up-to-date with what is actually on disk.                                |
| `CACHE_EXPIRATION`          | `7`               | `INTEGER` (days)    | Number of days until a cache entry is purged causing its value to be re-caclulated during the next scan. Longer lived cache entries can reduce processing.                   |
| `LEW_MEMORY_MODE`           | `False`           | `BOOLEAN`           | Enable this to set libtorrent into a low memory profile to help reduce the amount of RAM used by the torrent client. Lew caused this.                                        |
| `ALLOW_UTP_CONNECTIONS`     | `False`           | `BOOLEAN`           | Enable this to allow the client to connect to peers using uTP instead of TCP. This may result in lower transfer speeds.                                                      |
| `MEMORY_LOG_INTERVAL`       | `0`               | `INTEGER` (seconds) | Set this to something >0 to display thread memory utilization in the console at a desired interval. Most commonly used for debugging at ~5 seconds.                          |
| `TAG_SEARCH_RESULTS`        | `True`            | `BOOLEAN`           | Disable this to prevent server from tagging search results which contain your uploads with your user label. This is a privacy option to anonymize your uploads if preferred. |
| `TZ`                        | `America/Chicago` | `TEXT` (ISO 8601)   | Change this to your desired time zone. Check online for a list of ISO 8601 time zones.                                                                                       |
| `UID`                       | `1000`            | `INTEGER` (user)    | User ID on the system to run the app as. Make sure this user can read and write to the `app data` and `DOWNLOADS_DIR` directories.                                           |
| `GID`                       | `1000`            | `INTEGER` (group)   | Group ID on the system to run the app as. Make sure this group can read and write to the `app data` and `DOWNLOADS_DIR` directories.                                         |
| `MAX_UNCHOKE_SLOTS`         | `-1` (Unlimited)  | `INTEGER`           | Number of simultaneous seeding (uploading) torrents which are allowed to be active at any given time. Set to -1 for unlimited slots.                                         |
| `MAX_DOWNLOAD_SLOTS`        | `-1` (Unlimited)  | `INTEGER`           | Number of simultaneous leeching (downloading) torrents which are allowed to be active at any given time. Set to -1 for unlimited slots.                                      |

### 2. Configure Volumes

**NOTE:** Make sure the `/app/data` mountpoint and `DOWNLOADS_DIR` directories are readable and writable by your
configured `UID:GID`, otherwise the container will fail to start. Normally this just means 'don't create the
directories as root' as a general rule of thumb.

| Volume      | Description                                                                                                              | Example                                           |
|-------------|:-------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| `/app/data` | Persistent storage inside the app for storing torrent files (.torrent), fastresume data, and torrent metadata for cache. | `/humehouse/privateindexer/client/data:/app/data` |
| *Downloads* | Directory where downloads will be stored. `DOWNLOADS_DIR` MUST be accessible from this directory (Try to match them.)    | `/data/downloads:/data/downloads`                 |
| *Movies*    | Movie library location(s). **This should match Radarr's configuration exactly.**                                         | `/data/media/movies:/data/media/movies`           |
| *TV series* | TV library location(s). **This should match Sonarr's configuration exactly.**                                            | `/data/media/shows:/data/media/shows`             |
| *Music*     | Music library location(s). **This should match Lidarr's configuration exactly.**                                         | `/data/media/music:/data/media/music`             |

#### Optional log file mount

You can choose whether to mount `/app/logs` somewhere on the host if you would like to have persistent log files saved.

### 3. Port forwarding

The Torrenting Port

- You should bind **and port forward** the `TORRENTING_PORT` (default is 6881) to your Docker host to allow incoming
  connections for seeding.
- When you forward the port at your router, you only need to forward TCP traffic unless you enable the
  `ALLOW_UTP_CONNECTIONS` environment variable.
- NOTE: Map the same port you're using **INSIDE** the container to the port **OUTSIDE** the container on the
  host. Otherwise the client will start advertising a different port than it's actually reachable on.

The Webserver Port

- The built-in torrent client runs a web server on port 8080 inside the container to run the dashboard and for RESTful
  API control of the client.
- You can map the web server port to any port on the host or none at all if you connect from within the Docker
  network, such as using a reverse proxy like NGINX.

### 4. Start Client

Start container:

```bash
docker compose up -d
```

View logs and follow console:

```bash
docker compose logs client -f
```

### 5. Connect the indexer to Prowlarr

This is required if you would like to have PrivateIndexer torrents show up in your torrent search results or have
Radarr/Sonarr/Lidarr auto-search and pull torrents from the PrivateIndexer swarm.
You can still add the indexer to your *arr suite of apps individually, but Prowlarr is much easier as it will sync
automatically based on indexer status and type.

1. Navigate to the `Indexers` section of the settings in Prowlarr.
2. Click `+ Add Indexer` to add new indexer.
3. Find `Generic Torznab` in the list.
4. Change the name to something you can identify it with like `PrivateIndexer`.
5. Set the `URL` to your server operator's URL (e.g. David's server is located at `https://indexer.humehouse.com`)
6. Enter your assigned API key in the `API Key` section. This is the same as `API_KEY` in your environment variables.
7. Click the gear at the bottom to show advanced settings and set the `Indexer Priority` to something `lower`
   (such as 1) than your other indexers so your apps will generally prefer torrents from PrivateIndexer **before** using
   torrents from other indexers.
8. Click `Test` to make sure the connection is working
9. Click `Save` to add the client

### 6. Connect the download client to Radarr/Sonarr/Lidarr

The API was derived from the qBittorrent API and mocks all of the endpoints used by the *arr suite of apps.

1. Navigate to the `Download Clients` section of the settings in your app.
2. Click `+` to add new client.
3. Find `qBittorrent` in the list.
4. Change the name to something you can identify it with like `PrivateIndexer`.
5. Set the host to the name of your `privateindexer-client` container or a hostname that points to it (like via reverse
   proxy).
6. Set the port to whatever port you've mapped to the webserver port, default is 8080 if using the example below
7. Enter **any username** and the password is your assigned API key. This is the same as `API_KEY` in your environment
   variables.
8. Give the app a unique category like `radarr`/`sonarr` etc.
9. You may want to click the gear at the bottom to show advanced settings and set the `Client Priority` to something
   `higher` than your default download client so it doesn't try to download random torrents
10. **Uncheck** the `Remove Completed` option - the client will re-create the torrent anyway, so you'd only be causing
    more processing load later when the scan picks up the media.
11. Click `Test` to make sure the connection is working
12. Click `Save` to add the client

Now you are ready to configure your indexer to use your PrivateIndexer torrent client.
Make sure to use **ONLY** this client **ONLY** for PrivateIndexer downloads.
Downloads from any other source will be rejected by the PrivateIndexer download client and the tracker will reject
download requests and tracker announcements from any other type of download client.

1. Navigate to the `Indexers` section of the settings in your app.
2. Find your `PrivateIndexer (Prowlarr)` indexer entry or whatever you named it
3. Click the gear at the bottom to show advanced settings and set the `Download Client` to your newly created client
4. Click `Save`
5. For every other indexer in your app, make sure to select a **different** download client, otherwise the indexer may
   try to use `PrivateIndexer` to download non-PrivateIndexer torrents

### 7. Visit the web interface

With the provided example `docker-compose.yml` the container listens on port 8080 on all interfaces

Browse to `http://hostname:8080/dashboard` to view the dashboard

- Click on torrents to view their status
- Switch tabs using the menu docked to the bottom of the page to view general torrent info and peer info
- Filter through torrents by name using the `Filter` search box at the top of the 'Name' column
- Sort torrents by clicking any of the column headers

Your client stats are displayed at the top center

- Downloading: number of torrents you are actively downloading (leeching) to  **this local client**
- Seeding: number of torrents you are actively uploading (seeding) from **this local client**
- Ratio: your total downloaded data vs. your total uploaded data (most trackers want this to be at least 1, but we don't
  care)
- Torrents: number of torrents added to **this local client**
- Peers: number of external clients connected to **this local client** (can be seeds or leeches, or neither)

Your server stats are displayed in the top right corner

- Uploads: number of torrents you've sent to the server
- DL: amount of data you've downloaded from peers in the swarm in total
- L: number of torrents you are actively downloading (leeching) from the swarm (all locations)
- UL: amount of data you've uploaded to peers in the swarm in total
- S: number of torrents you are actively seeding to the swarm (all locations)
- Ratio: same as the client ratio above, except this value is tracked by the server, not the client
- Grabs: number of times **other users** have downloaded files that you have uploaded
