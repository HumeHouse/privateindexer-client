# PrivateIndexer Client

This is the client container for the HumeHouse PrivateIndexer.
It scans your local media library, creates torrents, and communicates with the PrivateIndexer server.
There is also a built-in torrent client that will automatically start seeding all your media for you.
The build-in torrent client also provides qBittorrent-compatible API endpoints for usage with the *arr suite apps.
You can view a basic dashboard by visiting `http://hostname:8080/dashboard` from a browser if you use the example.

---

## Building

Clone this repository and simply run the following command in the directory with the `Dockerfile` and your image will be
built

```bash
docker compose build
```

You can also use the hosted pre-built image on at `ghcr.io/humehouse/privateindexer-client:latest`
(See [GitHub](https://github.com/HumeHouse/privateindexer-client/tags) for all version tags)

---

## Quick Start (using Docker)

### Use the provided `docker-compose.yml` and adjust paths and environment variables to match your setup.

### 1. Configure Environment Variables

#### REQURIED VARIABLES

| Variable        | Description                                                                                                    | Example              |
|-----------------|----------------------------------------------------------------------------------------------------------------|----------------------|
| `DOWNLOADS_DIR` | Path inside the container that downloads are saved to. (Make sure to mount it to the host somewhere - step 2.) | `/data/downloads`    |
| `MOVIE_DIR`     | Path inside the container to your movie media library. (Make sure to mount it to the host somewhere - step 2.) | `/data/media/movies` |
| `API_KEY`       | Your assigned API key (contact David if you don’t have one).                                                   | `abcdef123456`       |

#### OPTIONAL VARIABLES

| Variable              | Default Value     | Description                                                                                                                            |
|-----------------------|-------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| `MOVIE_EXTENSIONS`    | `mp4,mkv,m4v,avi` | File extensions (comma-separated) to whitelist for torrent creation during scans.                                                      |
| `MAX_THREADS `        | `8`               | Number of threads to use for CPU & I/O bound tasks. Recommend matching CPU cores.                                                      |
| `SCAN_INTERVAL`       | `30`              | Minutes between media library scans.                                                                                                   |
| `FASTRESUME_INTERVAL` | `60`              | How often (in minutes) to save fastresume data. *Setting this too low can negatively impact your disk performance.*                    |
| `TORRENTING_PORT`     | `6881`            | Port accepting connections from other torrent clients. (Make sure to bind this to host and forward in router.)                         |
| `LOG_LEVEL`           | `INFO`            | Lowest log level to show in console. Can be `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` where `DEBUG` shows most amount of logs |
| `EXCLUDE_REGEX`       | **NONE**          | Regular expression to compare against filenames and exclude upon match. This has no default and is ignored if omitted.                 |

### 2. Configure Volumes

| Volume      | Description                                                                                                              | Example                                           |
|-------------|:-------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| `/app/data` | Persistent storage inside the app for storing torrent files (.torrent), fastresume data, and torrent metadata for cache. | `/humehouse/privateindexer/client/data:/app/data` |
| *Downloads* | Directory where downloads will be stored. `DOWNLOADS_DIR` MUST be accessible from this directory (Try to match them.)    | `/data/downloads:/data/downloads`                 |
| *Movies*    | Movie library location. `MOVIE_DIR` MUST be accessible from this directory (Try to match them.)                          | `/data/media/movies:/data/media/movies`           |

### 3. Port forwarding

- The Torrenting Port
    - You should bind **and port forward** the `TORRENTING_PORT` to your Docker host to allow incoming connections for
      seeding.
    - When you forward the port at your router, make sure to use **both UDP and TCP** to maximize connection potential.
    - NOTE: Map the same port you're using **INSIDE** the container to the port **OUTSIDE** the container on the host.
      Otherwise the client will start advertising a different port than it's actually reachable on.

- The Webserver Port
    - The built-in torrent client runs a web server on port 80 inside the container for RESTful API control of the
      client.
    - You can map the web server port to any port on the host or none at all if you connect from within the Docker
      network like if using nginx reverse proxy.

### 4. Start Client

Start container:

```bash
docker compose up -d
```

View logs of client:

```bash
docker logs -f privateindexer-client
```

### 5. Connect the indexer to Prowlarr

This is required if you would like to have PrivateIndexer torrents show up in your torrent search results.
You can still add the indexer to your *arr suite of apps individually, but Prowlarr is much easier as it will sync
automatically.

1. Navigate to the `Indexers` section of the settings in Prowlarr.
2. Click `+ Add Indexer` to add new indexer.
3. Find `Generic Torznab` in the list.
4. Change the name to something you can identify it with like `PrivateIndexer`.
5. Set the `URL` to `https://indexer.humehouse.com`
6. Enter your assigned API key in the `API Key` section. This is the same as `API_KEY` in your environment variables.
7. Click the gear at the bottom to show advanced settings and set the `Indexer Priority` to something `lower` than your
   other indexers so your apps will generally prefer torrents from PrivateIndexer **before** using torrents from other
   indexers.
8. Click `Test` to make sure the connection is working
9. Click `Save` to add the client

### 6. Connect the download client to Radarr/Sonarr/*arr

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
10. **Uncheck** both `Remove Completed` and `Remove Failed` - the client has no sense of either of these options and
    will
    only cause errors if you leave these on.
11. Click `Test` to make sure the connection is working
12. Click `Save` to add the client

Now you are ready to configure your indexer to use your PrivateIndexer torrent client.
Make sure to use **ONLY** this client **ONLY** for PrivateIndexer downloads.
Downloads from any other source will be rejected by the PrivateIndexer download client.

1. Navigate to the `Indexers` section of the settings in your app.
2. Find your `PrivateIndexer (Prowlarr)` indexer entry or whatever you named it
3. Click the gear at the bottom to show advanced settings and set the `Download Client` to your newly created client
4. Click `Save`
5. For every other indexer in your app, make sure to select a **different** download client, otherwise the indexer may
   try to use `PrivateIndexer` to download non-PrivateIndexer torrents

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
    stop_grace_period: 5m # careful not to let Docker kill the container, it could prevent fastresume data from being saved during shutdown
    environment:
      DOWNLOADS_DIR: /data/privateindexer/downloads
      MOVIE_DIR: /data/media/movies
      MOVIE_EXTENSIONS: mp4,mkv,m4v,avi
      MAX_THREADS: 16 # 16 threads
      FASTRESUME_INTERVAL: 60 # save fastresume data every hour
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
