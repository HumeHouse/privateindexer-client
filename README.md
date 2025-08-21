# PrivateIndexer Client

This is the client container for the HumeHouse PrivateIndexer.
It scans your local media library, creates torrents, and communicates with the PrivateIndexer server.
Optionally, you can also run a dedicated qBittorrent client alongside it if you don't already have one for seeding.

---

## Building

Simply run the following command in the directory with the `Dockerfile` and your image will be built

```bash
docker compose build
```

---

## Quick Start

### 1. Modify the `docker-compose.yml`

Use the provided `docker-compose.yml` and adjust paths and environment variables to match your setup.

### 2. Configure Environment Variables

| Variable           | Default Value     | Description                                                                     | Example                           |
|--------------------|-------------------|---------------------------------------------------------------------------------|-----------------------------------|
| `MOVIE_DIR`        | *None (required)* | Directory on host/container for the movie media library                         | `/data/media/movies`              |
| `MOVIE_EXTENSIONS` | `mp4,mkv,m4v,avi` | File extensions to whitelist for torrent creation during scans. Comma-separated |                                   |
| `SCANNER_THREADS`  | `8`               | Number of async threads for scanning media. Recommend matching CPU cores.       |                                   |
| `SCAN_INTERVAL`    | `15`              | Minutes between media library scans.                                            |                                   |
| `API_KEY`          | *None (required)* | Your assigned API key (contact David if you don’t have one).                    | `abcdef123456`                    |
| `QBIT_HOST`        | *None (required)* | Host and port of qBittorrent instance                                           | `privateindexer-qbittorrent:8080` |
| `QBIT_USERNAME`    | *None (required)* | Username to log into qBittorrent with                                           | `admin`                           |
| `QBIT_PASSWORD`    | *None (required)* | Password for the qBittorrent username                                           | `password`                        |

---

### 3. Configure Volumes

| Volume             | Description                                                                                        | Example                                           |
|--------------------|:---------------------------------------------------------------------------------------------------|---------------------------------------------------|
| `/app/data`        | Local persistent storage for client metadata and state.                                            | `/humehouse/privateindexer/client/data:/app/data` |
| *Depends on setup* | Movie library location. Mount directory inside container to same location on host to reduce issues | `/data/media/movies:/data/media/movies`           |

---

### 4. Configure Networking

Both the `client` and `qbittorrent` services run on the `privateindexer-net` bridge network to communication with each
other.

If you aren't using the optional qBittorrent client, you'll need to add your existing qBittorrent client to the
`privateindexer-net` bridge network.

---

### 5. Optional: Built-in qBittorrent

If you don’t already run qBittorrent, you can configure and enable the provided container config.

- Uses ports `6881/tcp` and `6881/udp` for torrenting, change the `ports` directive and the environment variable
  `TORRENTING_PORT` if you need to use a different incoming port
- Mount the qBittorrent `/config` directory somewhere on your host to persist data
- Mount the same media directory inside the qBittorrent container that you used on the PrivateIndexer client volume

---

### 6. Start Client

Start containers:

```bash
docker compose up -d
```

Check logs of client:

```bash
docker logs -f privateindexer-client
```

---

## Tips

- Make sure media paths match between PrivateIndexer client and qBittorrent client.
- Ask David for your API key before starting.
- Logs will show when the scanner finds and registers torrents with the PrivateIndexer server.
- You can run the client without the bundled qBittorrent client by pointing `QBIT_HOST` to your existing setup,
  just be sure the path matches the one you mounted to the PrivateIndexer client container (view example below)
- If you are using the bundled qBittorrent client, make sure to run the container first to get the admin password, then
  access the web interface and update the username/password

---

## Example

Here’s an example setup using the optional qBittorrent container:

- My movies are stored in `/data/media/movies` on the host
- My persistent data is stored in `/humehouse/privateindexer` on the host

```yaml
networks:
  privateindexer-net:
    name: privateindexer-net
    driver: bridge
services:
  client:
    image: privateindexer-client:latest
    container_name: privateindexer-client
    restart: unless-stopped
    environment:
      MOVIE_DIR: /data/media/movies
      MOVIE_EXTENSIONS: mp4,mkv,m4v,avi
      SCANNER_THREADS: 16 # 16 threads
      SCAN_INTERVAL: 30 # 30 minutes
      API_KEY: keyhere
      QBIT_HOST: privateindexer-qbittorrent:8080 # connect to dedicated client
      QBIT_USERNAME: admin
      QBIT_PASSWORD: password
    volumes:
      - /humehouse/privateindexer/client_data:/app/data # mount the persistent data storage location to the host somewhere
      - /data/media/movies:/data/media/movies # mount the movies directory on the host to the MOVIE_DIR in the container
    networks:
      - privateindexer-net
    logging:
      options:
        max-size: 10m
        max-file: 5
    build:
      context: .
      network: host
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: privateindexer-qbittorrent
    restart: unless-stopped
    environment:
      PUID: 1000
      PGID: 1000
      TZ: America/Chicago
      WEBUI_PORT: 8080
      TORRENTING_PORT: 6881
    volumes:
      - /humehouse/privateindexer/qbittorrent_config:/config
      - /data/media/movies:/data/media/movies # qBittorrent container should have the same MOVIE_DIR volume mounted
    networks:
      - privateindexer-net
    ports:
      - "8080:8080"
      - "6881:6881"
      - "6881:6881/udp"
```
