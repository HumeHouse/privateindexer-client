# PrivateIndexer Client

This is the client container for the HumeHouse PrivateIndexer.
It scans your local media library, creates torrents, and communicates with the PrivateIndexer server.
There is also a built-in torrent seeder that will automatically start seeding all your media for you.

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

| Variable           | Default Value     | Description                                                                                                      | Example              |
|--------------------|-------------------|------------------------------------------------------------------------------------------------------------------|----------------------|
| `MOVIE_DIR`        | *None (required)* | Path inside the container to your movie media library. (Make sure to mount it to the host somewhere - step 3.)   | `/data/media/movies` |
| `MOVIE_EXTENSIONS` | `mp4,mkv,m4v,avi` | File extensions (comma-separated) to whitelist for torrent creation during scans.                                |                      |
| `SCANNER_THREADS`  | `8`               | Number of async threads for scanning media. Recommend matching CPU cores.                                        |                      |
| `SCAN_INTERVAL`    | `15`              | Minutes between media library scans.                                                                             |                      |
| `API_KEY`          | *None (required)* | Your assigned API key (contact David if you don’t have one).                                                     | `abcdef123456`       |
| `TORRENTING_PORT`  | `6881`            | Port accepting connections from other torrent clients. (Make sure to bind this to host and forward in router)    |                      |

---

### 3. Configure Volumes

| Volume             | Description                                                                                            | Example                                           |
|--------------------|:-------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| `/app/data`        | Persistent storage inside the app for storing torrent files (.torrent) and torrent metadata for cache. | `/humehouse/privateindexer/client/data:/app/data` |
| *Depends on setup* | Movie library location. Path **inside** container MUST match `MOVIE_DIR` environment variable.         | `/data/media/movies:/data/media/movies`           |

---

### 4. Port forwarding

---

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
      - /data/media/movies:/data/media/movies # mount the movies directory on the host to the MOVIE_DIR in the container
    networks:
      - privateindexer-net
    ports:
      - "6881:6881"
    logging:
      options:
        max-size: 10m
        max-file: 5
```
