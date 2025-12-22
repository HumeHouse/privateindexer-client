from typing import Set, Callable, Any

from pydantic import BaseModel, Field

from privateindexer_client.core.logger import log

ExtractorMap = dict[str, Callable[[dict], Any]]

VIDEO_EXTRACTORS = {"qualities": lambda m: m.get("quality", {}).get("quality", {}).get("name"),
                    "video_codecs": lambda m: normalize_video_codec(m.get("mediaInfo", {}).get("videoCodec")),
                    "audio_codecs": lambda m: normalize_audio_codec(m.get("mediaInfo", {}).get("audioCodec")),
                    "audio_channels": lambda m: m.get("mediaInfo", {}).get("audioChannels"), "bit_depths": lambda m: m.get("mediaInfo", {}).get("videoBitDepth"),
                    "hdr_types": lambda m: m.get("mediaInfo", {}).get("videoDynamicRangeType"), "release_groups": lambda m: m.get("releaseGroup"), }

AUDIO_EXTRACTORS = {"qualities": lambda m: m.get("quality", {}).get("quality", {}).get("name"), "audio_codecs": lambda m: m.get("mediaInfo", {}).get("audioCodec"),
                    "audio_channels": lambda m: m.get("mediaInfo", {}).get("audioChannels"), "bit_depths": lambda m: m.get("mediaInfo", {}).get("audioBits"),
                    "sample_rates": lambda m: m.get("mediaInfo", {}).get("audioSampleRate"), "release_groups": lambda m: m.get("releaseGroup"), }

NORMALIZERS = {"audio_channels": lambda m: float(m) if m else None, "bit_depths": lambda m: f"{m}bit" if isinstance(m, int) else m,
               "release_groups": lambda m: m.strip() if isinstance(m, str) and m.strip() else None, }


class AggregatedMetadata(BaseModel):
    qualities: Set[str] = Field(default_factory=set)
    video_codecs: Set[str] = Field(default_factory=set)
    audio_codecs: Set[str] = Field(default_factory=set)
    audio_channels: Set[float] = Field(default_factory=set)
    bit_depths: Set[str] = Field(default_factory=set)
    hdr_types: Set[str] = Field(default_factory=set)
    sample_rates: Set[str] = Field(default_factory=set)
    release_groups: Set[str] = Field(default_factory=set)


def format_tags(aggregated: AggregatedMetadata) -> str:
    """
    Helper function to standardize and generate the tags for an aggregated set of metadata from a media app
    """

    # internal helper to generate a encapsulating text containing the tag
    def tag(values, wrap="[]"):
        clean = sorted(str(v) for v in values if v)
        if not clean:
            return ""
        left, right = wrap
        return f"{left}{"+".join(clean[:3])}{"++" if len(clean) > 3 else ""}{right}"

    tags = [tag(aggregated.qualities)]

    if aggregated.video_codecs:
        tags.append(tag(aggregated.video_codecs))

    if aggregated.bit_depths:
        tags.append(tag(aggregated.bit_depths))

    if aggregated.hdr_types and any(aggregated.hdr_types):
        tags.append(tag(aggregated.hdr_types))

    if aggregated.sample_rates:
        tags.append(tag(aggregated.sample_rates))

    # audio codecs + channels
    audio_entries = {f"{codec} {ch:.1f}" for codec in aggregated.audio_codecs if codec for ch in aggregated.audio_channels if ch}

    if audio_entries:
        tags.append(tag(audio_entries, wrap="()"))

    # release groups at end
    if aggregated.release_groups:
        groups = sorted(g for g in aggregated.release_groups if g)
        tags.append(f"-{"+".join(groups[:3])}")
        if len(groups) > 3:
            tags.append("++")

    return "".join(tags)


def aggregate_metadata(items: list[dict], app_name: str, extractors: ExtractorMap) -> AggregatedMetadata:
    aggregated = AggregatedMetadata()

    for item in items:
        for field, extractor in extractors.items():
            try:
                value = extractor(item)

                # first normalize the value
                if field in NORMALIZERS:
                    value = NORMALIZERS[field](value)
                    if value is None:
                        continue

                # add the value to the set in the aggregated metadata
                getattr(aggregated, field).add(value)
            except Exception as e:
                log.error(f"[{app_name}] Exception while extracting field {field}: {e}")

    return aggregated


def normalize_video_codec(codec: str | None) -> str | None:
    """
    Helper function to standardize the video codec
    """
    if not codec:
        return None

    codec = codec.strip().lower()

    if codec in {"x265", "h265", "hevc", "h.265"}:
        return "H265"

    if codec in {"x264", "h264", "avc", "h.264"}:
        return "H264"

    # fallback to just uppercase codec if no matches
    return codec.upper()


def normalize_audio_codec(codec: str | None) -> str | None:
    """
    Helper function to standardize the audio codec
    """
    if not codec:
        return None

    codec = codec.strip().lower()

    if codec in {"eac3 atmos", "eac3", "ddp", "dd+", "dolby digital plus"}:
        return "DDP"
    if codec in {"dts", "dtshd"}:
        return "DTS"
    if codec in {"truehd"}:
        return "TrueHD"

    # fallback to just uppercase codec if no matches
    return codec.upper()
