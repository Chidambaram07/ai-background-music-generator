import numpy as np
import pytest
import soundfile as sf

from app.services.audio_processing import AudioProcessingError
from app.services.storage import ArtifactStorage


def test_storage_isolation_and_conflicts(tmp_path):
    storage = ArtifactStorage(tmp_path)
    first = storage.create_job("job-one")
    second = storage.create_job("job-two")
    (first / "mix.wav").write_bytes(b"first")
    (second / "mix.wav").write_bytes(b"second")
    with pytest.raises(AudioProcessingError, match="already exists"):
        storage.create_job("job-one")
    assert (first / "mix.wav").read_bytes() == b"first"


@pytest.mark.parametrize("job_id", ["..", "../other", "a/b", "C:\\outside", ""])
def test_storage_rejects_unsafe_ids(tmp_path, job_id):
    with pytest.raises(AudioProcessingError):
        ArtifactStorage(tmp_path).job_dir(job_id)


def test_storage_measures_and_rejects_bad_artifacts(tmp_path):
    storage = ArtifactStorage(tmp_path)
    directory = storage.create_job("measured")
    sf.write(directory / "mix.wav", np.zeros(48000), 48000)
    assert storage.wav_info("measured", "mix.wav")["duration_s"] == 1
    assert storage.wav_info("measured", "mix.wav")["sample_rate"] == 48000
    with pytest.raises(AudioProcessingError) as missing:
        storage.wav_info("measured", "missing.wav")
    assert missing.value.code == "ARTIFACT_MISSING"
    (directory / "broken.wav").write_bytes(b"not audio")
    with pytest.raises(AudioProcessingError) as corrupt:
        storage.wav_info("measured", "broken.wav")
    assert corrupt.value.code == "ARTIFACT_CORRUPT"
    with pytest.raises(AudioProcessingError):
        storage.artifact("measured", "../other.wav")