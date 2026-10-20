import io
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
import soundfile as sf

from app.services.audio_processing import AudioProcessingError
from app.services.narration import NarrationDownloader
from scripts.batch_runner import generate, narration_fixture


@pytest.fixture
def narration_server():
    wav = narration_fixture("normal")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location","/speech.wav")
                self.end_headers()
                return
            if self.path == "/private":
                self.send_response(302)
                self.send_header("Location","http://169.254.169.254/metadata")
                self.end_headers()
                return
            data = b"not wav" if self.path == "/invalid" else wav
            self.send_response(200)
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def downloader(client,**options):
    worker = client.app.state.job_worker
    settings = worker.settings.model_copy(update={"narration_allow_http":True,"narration_allowed_private_hosts":"127.0.0.1",**options})
    return NarrationDownloader(settings,worker.storage)


def test_remote_wav_redirect_download_is_real(assessment_client,narration_server):
    service = downloader(assessment_client)
    path = service.download(narration_server+"/redirect")
    assert sf.info(path).samplerate == 48000
    assert sf.info(path).frames == 288000


def test_private_redirect_and_byte_limit_are_rejected(assessment_client,narration_server):
    service = downloader(assessment_client)
    with pytest.raises(AudioProcessingError) as rejected:
        service.download(narration_server+"/private")
    assert rejected.value.code == "NARRATION_URL_REJECTED"
    with pytest.raises(AudioProcessingError) as too_large:
        downloader(assessment_client,narration_max_bytes=100).download(narration_server+"/speech.wav")
    assert too_large.value.code == "NARRATION_TOO_LARGE"
    with pytest.raises(AudioProcessingError):
        service.download(narration_server+"/invalid")
    assert not list(service.storage.uploads_dir.glob("*.wav"))


def test_dns_rebinding_and_mixed_private_addresses_are_blocked(assessment_client,monkeypatch):
    service = downloader(assessment_client)
    monkeypatch.setattr(socket,"getaddrinfo",lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443)),(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))])
    with pytest.raises(AudioProcessingError):
        service.pinned_url("https://example.com/speech.wav")


def test_narration_timeout_is_structured_and_cleans_partial_file(assessment_client,monkeypatch):
    import httpx
    from app.services import narration
    original = httpx.Client
    def timed_out(request):
        raise httpx.ReadTimeout("Injected read timeout")
    monkeypatch.setattr(narration.httpx,"Client",lambda **kwargs: original(transport=httpx.MockTransport(timed_out),**kwargs))
    service = downloader(assessment_client)
    with pytest.raises(AudioProcessingError) as error:
        service.download("http://127.0.0.1/speech.wav")
    assert error.value.code == "NARRATION_TIMEOUT"
    assert not list(service.storage.uploads_dir.glob("*.wav"))


def test_remote_narration_pipeline_and_manifest_curve(assessment_client,narration_server):
    client = assessment_client
    worker = client.app.state.job_worker
    worker.settings = worker.settings.model_copy(update={"narration_allow_http":True,"narration_allowed_private_hosts":"127.0.0.1"})
    job = generate(client,{"prompt":"Calm narration music","duration_s":6,"narration_url":narration_server+"/speech.wav"})
    assert job["status"] == "succeeded",job
    manifest = job["result"]["manifest"]
    curve = manifest["ducking"]["volume_curve"]
    assert len(curve) >= 4
    root = worker.storage.job_dir(job["job_id"])
    mix,rate = sf.read(root/"mix.wav",dtype="float32")
    ducked,_ = sf.read(root/"mix_ducked.wav",dtype="float32")
    gains = np.interp(np.arange(len(mix))/rate,[point["time_s"] for point in curve],[point["gain"] for point in curve])
    np.testing.assert_allclose(ducked,mix*gains,atol=2/32768)
    checks = job["result"]["validation_result"]["checks"]["ducking"]
    assert 8 <= checks["ducking_db"] <= 15
    assert checks["recovers_after_speech"]