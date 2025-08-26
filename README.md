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

| Variable           | Default Value     | Description                                                                                                    | Example              |
|--------------------|-------------------|----------------------------------------------------------------------------------------------------------------|----------------------|
| `MOVIE_DIR`        | *None (required)* | Path inside the container to your movie media library. (Make sure to mount it to the host somewhere - step 3.) | `/data/media/movies` |
| `MOVIE_EXTENSIONS` | `mp4,mkv,m4v,avi` | File extensions (comma-separated) to whitelist for torrent creation during scans.                              |                      |
| `SCANNER_THREADS`  | `8`               | Number of async threads for scanning media. Recommend matching CPU cores.                                      |                      |
| `SCAN_INTERVAL`    | `15`              | Minutes between media library scans.                                                                           |                      |
| `API_KEY`          | *None (required)* | Your assigned API key (contact David if you don’t have one).                                                   | `abcdef123456`       |
| `TORRENTING_PORT`  | `6881`            | Port accepting connections from other torrent clients. (Make sure to bind this to host and forward in router)  |                      |

### 3. Configure Volumes

| Volume             | Description                                                                                               | Example                                           |
|--------------------|:----------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| `/app/data`        | Persistent storage inside the app for storing torrent files (.torrent) and torrent metadata for cache.    | `/humehouse/privateindexer/client/data:/app/data` |
| `/app/downloads`   | Directory where downloads will be stored. Mount this to where your other download clients put their files | `/data/torrents/downloads:/app/downloads`         |
| *Depends on setup* | Movie library location. Directory specified in `MOVIE_DIR` MUST be accessible from this directory         | `/data/media/movies:/data/media/movies`           |

### 4. Port forwarding

You should bind and port forward the TORRENTING_PORT to your Docker host to allow incoming connections for seeding.

### NOTE: Map the same port you're using **INSIDE** the container to the port **OUTSIDE** the container on the host

Otherwise the client will start advertising a different port than it's actually reachable on.

The built-in torrent client runs a webserver on port 80 inside the container for RESTful API control of the client

### 6. Start Client

Start container:

```bash
docker compose up -d
```

View logs of client:

```bash
docker logs -f privateindexer-client
```

---

## Tips

- Ask David for your API key before starting.
- Logs will show when the scanner finds and registers torrents with the PrivateIndexer server.

---

## Example

Here’s an example setup:

- My movie files are stored in `/data/media/movies` on the host
- My persistent data for the client is stored in `/humehouse/privateindexer` on the host

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
    environment:
      MOVIE_DIR: /data/media/movies
      MOVIE_EXTENSIONS: mp4,mkv,m4v,avi
      SCANNER_THREADS: 16 # 16 threads
      SCAN_INTERVAL: 30 # 30 minutes
      API_KEY: keyhere
      TORRENTING_PORT: 6881
    volumes:
      - /humehouse/privateindexer/client_data:/app/data # mount the persistent data storage location to the host somewhere
      - /data/torrents/downloads:/app/downloads # mount the torrent downloads location to the host somewhere
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
