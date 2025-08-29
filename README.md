# PrivateIndexer Client

This is the client container for the HumeHouse PrivateIndexer.
It scans your local media library, creates torrents, and communicates with the PrivateIndexer server.
There is also a built-in torrent client that will automatically start seeding all your media for you.
The build-in torrent client also provides qBittorrent-compatible API endpoints for usage with the *arr suite apps.
You can view a basic dashboard by visiting `http://container-ip:80/dashboard` from a browser.

---

## Building

Simply run the following command in the directory with the `Dockerfile` and your image will be built

```bash
docker compose build
```

You can also use the hosted pre-built image on at `ghcr.io/humehouse/privateindexer-client:latest`
(See [Releases](https://github.com/HumeHouse/privateindexer-client/releases) for all tags)

---

## Quick Start

### 1. Modify the `docker-compose.yml`

Use the provided `docker-compose.yml` and adjust paths and environment variables to match your setup.

### 2. Configure Environment Variables

| Variable           | Default Value     | Description                                                                                                                            | Example              |
|--------------------|-------------------|----------------------------------------------------------------------------------------------------------------------------------------|----------------------|
| `DOWNLOADS_DIR`    | *None (required)* | Path inside the container to your movie media library. (Make sure to mount it to the host somewhere - step 3.)                         | `/data/downloads`    |
| `MOVIE_DIR`        | *None (required)* | Path inside the container to your movie media library. (Make sure to mount it to the host somewhere - step 3.)                         | `/data/media/movies` |
| `MOVIE_EXTENSIONS` | `mp4,mkv,m4v,avi` | File extensions (comma-separated) to whitelist for torrent creation during scans.                                                      |                      |
| `SCANNER_THREADS`  | `8`               | Number of async threads for scanning media. Recommend matching CPU cores.                                                              |                      |
| `SCAN_INTERVAL`    | `15`              | Minutes between media library scans.                                                                                                   |                      |
| `API_KEY`          | *None (required)* | Your assigned API key (contact David if you don’t have one).                                                                           | `abcdef123456`       |
| `TORRENTING_PORT`  | `6881`            | Port accepting connections from other torrent clients. (Make sure to bind this to host and forward in router.)                         |                      |
| `LOG_LEVEL`        | `INFO`            | Lowest log level to show in console. Can be `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` where `DEBUG` shows most amount of logs |                      |

### 3. Configure Volumes

| Volume      | Description                                                                                                              | Example                                           |
|-------------|:-------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| `/app/data` | Persistent storage inside the app for storing torrent files (.torrent), fastresume data, and torrent metadata for cache. | `/humehouse/privateindexer/client/data:/app/data` |
| *Downloads* | Directory where downloads will be stored. `DOWNLOADS_DIR` MUST be accessible from this directory (Try to match them.)    | `/data/downloads:/data/downloads`                 |
| *Movies*    | Movie library location. `MOVIE_DIR` MUST be accessible from this directory (Try to match them.)                          | `/data/media/movies:/data/media/movies`           |

### 4. Port forwarding

You should bind and port forward the TORRENTING_PORT to your Docker host to allow incoming connections for seeding.

### NOTE: Map the same port you're using **INSIDE** the container to the port **OUTSIDE** the container on the host

Otherwise the client will start advertising a different port than it's actually reachable on.

The built-in torrent client runs a webserver on port 80 inside the container for RESTful API control of the client

### 5. Start Client

Start container:

```bash
docker compose up -d
```

View logs of client:

```bash
docker logs -f privateindexer-client
```

### 6. Connect to Prowlarr/Radarr/Sonarr

The API was derived from the qBittorrent API and mocks all of the endpoints used by the *arr suite of apps.

1. Navigate to the `Download Clients` section of the settings in your app.
2. Click `+` to add new client.
3. Find `qBittorrent` in the list.
4. Change the name to something you can identify it with like `PrivateIndexer`.
5. Set the host to the name of your `privateindexer-client` container or a hostname that points to it (like via reverse
   proxy).
6. Set the port to `8080` (or whatever port you've mapped to port 80 inside the container).
7. Enter any username and the password is your assigned API key. The key is called `API_KEY` in your environment
   variables.
8. Give the app a unique category like `radarr`/`sonarr`/`prowlarr` etc.
9. You may want to click the gear at the bottom to show advanced settings and set the `Client Priority` to something
   higher than your default download client
10. **Uncheck** both `Remove Completed` and `Remove Failed` - the app has no sense of either of these options and will
    only cause errors if you leave these on.
11. Click `Test` to make sure the connection is working
12. Click `Save` to add the client

Now you are ready to configure your indexers to use **ONLY** this client **ONLY** for PrivateIndexer downloads

1. Navigate to the `Indexers` section of the settings in your app.
2. Find your `PrivateIndexer` indexer entry
3. Click the gear at the bottom to show advanced settings and set the `Download Client` to your newly created client
4. Click `Save`
5. For every other indexer in your app, make sure to select a **different** download client, otherwise the indexer may
   try to use `PrivateIndexer` to download non-private torrents or cause other problems

You're done!

### 7. Visit the web interface

With the provided `docker-compose.yml` the container listens on port 8080 on all interfaces

Navigate to `https://your-hostname:8080/dashboard` to view the dashboard

- Click on torrents to view their status
- Switch tabs using the menu docked to the bottom of the page to view general info, tracker info, and peer info
- Filter through torrents by name using the `Filter` search box at the top of the 'Name' column

- Your client stats are displayed at the top center
    - Uploading: number of torrents you are actively uploading (seeding) from **this local client**
    - Downloading: number of torrents you are actively downloading (leeching) to  **this local client**
    - Total: number of torrents added to **this local client**
    - Peers: number of external clients connected to **this local client** (can be seeds or leeches)

- Your server stats are displayed in the top right corner
    - Uploaded: number of torrents you've sent to the server
    - S: number of torrents you are actively seeding to the swarm (all locations)
    - L: number of torrents you are actively downloading (leeching) from the swarm  (all locations)
    - Swarm: number of active users who are seeding torrents that you have uploaded
    - Grabs: number of times other users have downloaded files that you have uploaded

---

## Tips

- Ask David for your API key before starting.
- Logs will show when the scanner finds and registers torrents with the PrivateIndexer server.

---

## Example

Here’s an example setup:

- My movie files are stored in `/data/media/movies` on the host
- My downloads are stored in `/data/privateindexer/downloads` on the host
- My persistent data (torrents and database) for client is stored in `/humehouse/privateindexer` on the host

```yaml
networks:
  privateindexer-net:
    name: privateindexer-net
    driver: bridge
services:
  client:
    image: ghcr.io/humehouse/privateindexer-client:latest
    container_name: privateindexer-client
    restart: unless-stopped
    stop_grace_period: 1m # this may be necessary if you are downloading tons of torrents - the save task during shutdown can be heavy
    environment:
      DOWNLOADS_DIR: /data/privateindexer/downloads
      MOVIE_DIR: /data/media/movies
      MOVIE_EXTENSIONS: mp4,mkv,m4v,avi
      SCANNER_THREADS: 16 # 16 threads
      SCAN_INTERVAL: 30 # 30 minutes
      API_KEY: keyhere
      TORRENTING_PORT: 6881
    volumes:
      - /humehouse/privateindexer/client_data:/app/data # mount the persistent data storage location to the host somewhere
      - /data/privateindexer/downloads:/data/privateindexer/downloads # mount the downloads location on the host to the DOWNLOADS_DIR in the container
      - /data/media/movies:/data/media/movies # mount the movies directory on the host to the MOVIE_DIR in the container
    networks:
      - privateindexer-net
    ports:
      - "6881:6881"
      - "6881:6881/udp"
      - "8080:80"
    logging:
      options:
        max-size: 10m
        max-file: 5
```
