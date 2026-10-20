import base64
import hashlib
import json
import mimetypes
from uuid import uuid4

from app.services.audio_processing import AudioProcessingError


class OutputStore:
    def __init__(self, settings, storage):
        self.settings = settings
        self.storage = storage
        self.client = None
        self.signer = None
        if settings.storage_backend == "s3":
            import boto3
            from botocore.config import Config
            options = {"aws_access_key_id":settings.s3_access_key.get_secret_value(),
                       "aws_secret_access_key":settings.s3_secret_key.get_secret_value(),
                       "region_name":settings.s3_region,
                       "config":Config(signature_version="s3v4",s3={"addressing_style":"path"},
                                       connect_timeout=settings.s3_timeout_seconds, read_timeout=settings.s3_timeout_seconds,
                                       retries={"max_attempts":1})}
            if not options["aws_access_key_id"] or not options["aws_secret_access_key"]:
                raise AudioProcessingError("STORAGE_CONFIGURATION_ERROR", "S3 credentials must be supplied through configuration.")
            self.client = boto3.client("s3",endpoint_url=settings.s3_endpoint_url,**options)
            self.signer = boto3.client("s3",endpoint_url=settings.s3_public_endpoint_url,**options)

    def initialize(self):
        if self.client is None:
            return
        from botocore.exceptions import ClientError
        try:
            try:
                self.client.head_bucket(Bucket=self.settings.s3_bucket)
            except ClientError as error:
                if str(error.response["Error"]["Code"]) not in {"404","NoSuchBucket","NotFound"}:
                    raise
                options = {"Bucket":self.settings.s3_bucket}
                if self.settings.s3_region != "us-east-1":
                    options["CreateBucketConfiguration"] = {"LocationConstraint":self.settings.s3_region}
                self.client.create_bucket(**options)
            self.client.put_bucket_versioning(Bucket=self.settings.s3_bucket,VersioningConfiguration={"Status":"Enabled"})
            self.client.put_bucket_cors(Bucket=self.settings.s3_bucket,CORSConfiguration={"CORSRules":[{
                "AllowedOrigins":self.settings.cors_origins,"AllowedMethods":["GET","HEAD"],
                "AllowedHeaders":["*"],"ExposeHeaders":["ETag","x-amz-version-id"],"MaxAgeSeconds":300}]})
        except Exception:
            raise AudioProcessingError("STORAGE_UNAVAILABLE", "S3 bucket could not be initialized.") from None

    def ready(self):
        if self.client is not None:
            try:
                self.client.head_bucket(Bucket=self.settings.s3_bucket)
            except Exception:
                raise AudioProcessingError("STORAGE_UNAVAILABLE", "S3 output storage is unavailable.") from None

    def publish(self, job_id, manifest):
        if self.client is None:
            return {"backend":"local"}
        self.storage.verify_manifest(job_id,manifest)
        filenames = {"mix.wav", *(stem["file"] for stem in manifest["stems"]), *(f"{stem['name']}.wav" for stem in manifest["stems"]), *manifest["mix"].get("formats",{}).values()}
        for optional in ("mix_ducked.wav","mix_looped.wav"):
            if self.storage.artifact(job_id,optional,required=False).is_file():
                filenames.add(optional)
        records = {}
        try:
            def put(relative, data, content_type):
                digest = hashlib.sha256(data).hexdigest()
                key = f"{job_id}/{relative}"
                response = self.client.put_object(Bucket=self.settings.s3_bucket,Key=key,Body=data,
                    ContentType=content_type,Metadata={"sha256":digest},
                    ContentMD5=base64.b64encode(hashlib.md5(data).digest()).decode())
                version_id = response.get("VersionId")
                if not version_id:
                    raise ValueError("S3 versioning is required")
                remote = self.client.head_object(Bucket=self.settings.s3_bucket,Key=key,VersionId=version_id)
                if remote["ContentLength"] != len(data) or remote["Metadata"].get("sha256") != digest:
                    raise ValueError("Uploaded S3 artifact did not verify")
                return {"key":key,"version_id":version_id,"sha256":digest,"bytes":len(data)}
            for relative in sorted(filenames):
                path = self.storage.artifact(job_id,relative)
                records[relative] = put(relative,path.read_bytes(),mimetypes.guess_type(relative)[0] or "application/octet-stream")
            manifest["storage"] = {"backend":"s3","bucket":self.settings.s3_bucket,"objects":dict(records)}
            data = json.dumps(manifest,indent=2,allow_nan=False).encode()
            for relative in ("music_manifest.json","manifest.json"):
                records[relative] = put(relative,data,"application/json")
            self.storage.write_json(job_id,"music_manifest.json",manifest)
            self.storage.write_json(job_id,"manifest.json",manifest)
            return {"backend":"s3","bucket":self.settings.s3_bucket,"objects":records}
        except Exception:
            raise AudioProcessingError("STORAGE_PUBLICATION_FAILED", "Output publication failed; the job cannot be marked successful.") from None

    def download_url(self, relative, record):
        item = record.get("objects",{}).get(relative)
        if self.signer is None or not item or record.get("bucket") != self.settings.s3_bucket:
            raise AudioProcessingError("ARTIFACT_MISSING", "Artifact is not present in the published output index.")
        return self.signer.generate_presigned_url("get_object",Params={"Bucket":record["bucket"],"Key":item["key"],"VersionId":item["version_id"]},ExpiresIn=self.settings.s3_url_ttl_seconds)

    def restore(self, job_id, record):
        if self.client is None:
            return
        for relative, item in record.get("objects",{}).items():
            path = self.storage.artifact(job_id,relative,required=False)
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]:
                continue
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.download")
            try:
                response = self.client.get_object(Bucket=record["bucket"],Key=item["key"],VersionId=item["version_id"])
                with temporary.open("xb") as handle:
                    body = response["Body"]
                    try:
                        for chunk in body.iter_chunks():
                            handle.write(chunk)
                    finally:
                        body.close()
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("Stored artifact hash mismatch")
                temporary.replace(path)
            except Exception:
                raise AudioProcessingError("STORAGE_DOWNLOAD_FAILED", "Published artifact could not be restored.") from None
            finally:
                temporary.unlink(missing_ok=True)