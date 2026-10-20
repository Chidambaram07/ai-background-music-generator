import ipaddress
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
import soundfile as sf

from app.services.audio_processing import AudioProcessingError


class NarrationDownloader:
    def __init__(self, settings, storage):
        self.settings = settings
        self.storage = storage

    def resolve(self, locator: str) -> Path:
        parsed = urlsplit(locator)
        if parsed.path.startswith("/api/v1/music/files/"):
            path = (self.storage.uploads_dir / Path(parsed.path).name).resolve()
            if path.is_relative_to(self.storage.uploads_dir.resolve()) and path.is_file():
                return path
            if not parsed.scheme:
                raise AudioProcessingError("NARRATION_NOT_FOUND", "Uploaded narration was not found.")
        return self.download(locator)

    def pinned_url(self, url: str):
        try:
            parsed = urlsplit(url)
            allowed = {host.strip().lower() for host in self.settings.narration_allowed_private_hosts.split(",") if host.strip()}
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Unsupported narration URL")
            if parsed.scheme != "https" and not self.settings.narration_allow_http:
                raise ValueError("HTTPS narration is required")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = {entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
            if not addresses or (parsed.hostname.lower() not in allowed and any(not ipaddress.ip_address(address).is_global for address in addresses)):
                raise ValueError("Private, loopback, link-local or reserved destinations are not allowed")
            address = sorted(addresses)[0]
            pinned = httpx.URL(url).copy_with(host=address)
            return pinned, parsed.netloc, parsed.hostname
        except (ValueError, OSError) as error:
            raise AudioProcessingError("NARRATION_URL_REJECTED", "Narration URL failed scheme, address or DNS safety checks.", {"reason":type(error).__name__}) from None

    def download(self, locator: str) -> Path:
        self.storage.uploads_dir.mkdir(parents=True, exist_ok=True)
        path = self.storage.uploads_dir / f"{uuid4().hex}.wav"
        started = time.monotonic()
        current = locator
        try:
            with httpx.Client(timeout=self.settings.narration_timeout_seconds, trust_env=False, follow_redirects=False) as client:
                for redirect in range(self.settings.narration_max_redirects + 1):
                    pinned, host, tls_name = self.pinned_url(current)
                    with client.stream("GET", pinned, headers={"Host":host,"Connection":"close","Accept-Encoding":"identity"}, extensions={"sni_hostname":tls_name}) as response:
                        if response.status_code in {301,302,303,307,308}:
                            if redirect == self.settings.narration_max_redirects or not response.headers.get("location"):
                                raise AudioProcessingError("NARRATION_DOWNLOAD_FAILED", "Narration redirect limit was exceeded.")
                            current = urljoin(current, response.headers["location"])
                            continue
                        if response.status_code != 200:
                            raise AudioProcessingError("NARRATION_DOWNLOAD_FAILED", "Narration server did not return audio.", {"status":response.status_code})
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise AudioProcessingError("NARRATION_DOWNLOAD_FAILED", "Compressed narration responses are not accepted.")
                        size = 0
                        with path.open("xb") as handle:
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > self.settings.narration_max_bytes:
                                    raise AudioProcessingError("NARRATION_TOO_LARGE", "Narration exceeds the configured byte limit.")
                                if time.monotonic() - started > self.settings.narration_timeout_seconds:
                                    raise AudioProcessingError("NARRATION_TIMEOUT", "Narration download exceeded its time limit.")
                                handle.write(chunk)
                    info = sf.info(path)
                    if info.format != "WAV" or info.frames <= 0 or info.channels not in (1,2):
                        raise AudioProcessingError("INVALID_NARRATION", "Downloaded narration must be a nonempty mono/stereo WAV.")
                    return path
        except AudioProcessingError:
            path.unlink(missing_ok=True)
            raise
        except httpx.TimeoutException:
            path.unlink(missing_ok=True)
            raise AudioProcessingError("NARRATION_TIMEOUT", "Narration request timed out.") from None
        except (httpx.HTTPError, OSError, RuntimeError, ValueError):
            path.unlink(missing_ok=True)
            raise AudioProcessingError("NARRATION_DOWNLOAD_FAILED", "Narration could not be downloaded or decoded.") from None
        raise AudioProcessingError("NARRATION_DOWNLOAD_FAILED", "No narration was downloaded.")