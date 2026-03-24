import json
import mimetypes
import os
import random
import re
import string
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

META_BASE = "https://meta.ai"
GRAPHQL_URL = f"{META_BASE}/api/graphql"
GENERATE_DOC_ID = "ac0bad4b9787a393e160fb39f43404c1"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT_LANGUAGE = "vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5"
FBCDN_URL_RE = re.compile(r"https://scontent[^\"'\\\s<>]+")
META_CREATE_URL_RE = re.compile(r"https://meta\.ai/create/[^\"'\\\s<>]+")
META_PROMPT_URL_RE = re.compile(r"https://meta\.ai/prompt/[^\"'\\\s<>]+")
DOWNLOAD_DIR = Path(os.getenv("META_DOWNLOAD_DIR", "/app/downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Meta AI Bridge", version="1.1.0")


class ImageRequest(BaseModel):
    prompt: str
    orientation: str = Field(default="VERTICAL")
    timeout_seconds: int = Field(default=90, ge=15, le=300)


class VideoRequest(BaseModel):
    prompt: str
    timeout_seconds: int = Field(default=180, ge=15, le=300)
    poll_attempts: int = Field(default=12, ge=1, le=30)
    poll_interval_seconds: int = Field(default=5, ge=1, le=30)


class ImageToVideoRequest(BaseModel):
    source_media_ent_id: str
    prompt: str
    source_media_url: Optional[str] = None
    conversation_id: Optional[str] = None
    is_new_conversation: bool = True
    entry_point: str = Field(default="KADABRA__UNKNOWN")
    current_branch_path: Optional[str] = "0"
    timeout_seconds: int = Field(default=180, ge=15, le=300)
    poll_attempts: int = Field(default=12, ge=1, le=30)
    poll_interval_seconds: int = Field(default=5, ge=1, le=30)


class DownloadRequest(BaseModel):
    url: str
    filename: Optional[str] = None
    subdir: str = Field(default="default")


class BatchDownloadRequest(BaseModel):
    urls: List[str]
    subdir: str = Field(default="batch")
    prefix: str = Field(default="media")


class UploadResponse(BaseModel):
    success: bool
    source_media_ent_id: Optional[str] = None
    upload_session_id: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    raw_response: Optional[dict] = None
    error: Optional[str] = None


class ImageToVideoDownloadRequest(ImageToVideoRequest):
    subdir: str = Field(default="image-to-video")
    filename_prefix: str = Field(default="image-to-video")


class MetaBridge:
    def __init__(self) -> None:
        self.cookie_string = self._build_cookie_string()

    def _build_cookie_string(self) -> str:
        raw = os.getenv("META_COOKIE_STRING", "").strip()
        if raw:
            return raw

        pairs = []
        env_map = {
            "datr": os.getenv("META_AI_DATR", "").strip(),
            "ecto_1_sess": os.getenv("META_AI_ECTO_1_SESS", "").strip(),
            "wd": os.getenv("META_AI_WD", "").strip(),
            "dpr": os.getenv("META_AI_DPR", "").strip(),
            "rd_challenge": os.getenv("META_AI_RD_CHALLENGE", "").strip(),
        }
        for key, value in env_map.items():
            if value:
                pairs.append(f"{key}={value}")
        return "; ".join(pairs)

    def validate(self) -> Optional[str]:
        if not self.cookie_string:
            return "Missing cookies. Set META_COOKIE_STRING or META_AI_DATR + META_AI_ECTO_1_SESS."
        missing = []
        if "datr=" not in self.cookie_string:
            missing.append("datr")
        if "ecto_1_sess=" not in self.cookie_string:
            missing.append("ecto_1_sess")
        if missing:
            return f"Missing required cookies: {', '.join(missing)}"
        return None

    def _common_headers(self) -> Dict[str, str]:
        return {
            "origin": META_BASE,
            "referer": f"{META_BASE}/create",
            "user-agent": os.getenv("META_USER_AGENT", DEFAULT_UA),
            "accept-language": os.getenv("META_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
            "cookie": self.cookie_string,
        }

    def _generate_headers(self) -> Dict[str, str]:
        headers = self._common_headers()
        headers.update(
            {
                "accept": "text/event-stream",
                "content-type": "application/json",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "sec-fetch-dest": "empty",
            }
        )
        return headers

    def _prompt_headers(self, conversation_id: str, *, prefetch: bool = False, full_state: bool = False) -> Dict[str, str]:
        headers = self._common_headers()
        headers.update(
            {
                "accept": "*/*",
                "rsc": "1",
                "next-url": "/create",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "sec-fetch-dest": "empty",
            }
        )
        if prefetch:
            headers["next-router-prefetch"] = "1"
            headers["next-router-segment-prefetch"] = "/_tree"
        if full_state:
            headers["next-router-state-tree"] = self._build_router_state_tree(conversation_id)
        return headers

    def _random_id(self) -> str:
        return str(uuid.uuid4())

    def _random_large_int_str(self) -> str:
        return str(random.randint(10**18, 10**19 - 1))

    def _build_payload(
        self,
        prompt: str,
        operation: str,
        orientation: Optional[str] = None,
        *,
        source_media_ent_id: Optional[str] = None,
        source_media_url: Optional[str] = None,
        conversation_id: Optional[str] = None,
        is_new_conversation: bool = True,
        entry_point: str = "KADABRA__UNKNOWN",
        current_branch_path: Optional[str] = "0",
    ) -> Dict:
        conversation_id = conversation_id or self._random_id()
        user_message_id = self._random_id()
        assistant_message_id = self._random_id()
        turn_id = self._random_id()
        request_id = self._random_id() if operation == "TEXT_TO_IMAGE" else None
        prompt_session_id = self._random_id()
        user_unique_message_id = self._random_large_int_str()

        imagine_request = {"operation": operation, "requestId": request_id}
        if operation == "TEXT_TO_IMAGE":
            content_value = prompt
            imagine_request["textToImageParams"] = {
                "prompt": prompt,
                "orientation": orientation or "VERTICAL",
            }
        elif operation == "TEXT_TO_VIDEO":
            content_value = f"Tạo hoạt ảnh cho {prompt}"
            imagine_request["textToImageParams"] = {"prompt": prompt}
        elif operation == "IMAGE_TO_VIDEO":
            content_value = prompt
            imagine_request["imageToVideoParams"] = {
                "sourceMediaEntId": source_media_ent_id,
                "sourceMediaUrl": source_media_url,
                "prompt": prompt,
                "numMedia": 1,
            }
        else:
            raise ValueError(f"Unsupported operation: {operation}")

        return {
            "doc_id": GENERATE_DOC_ID,
            "variables": {
                "conversationId": conversation_id,
                "content": content_value,
                "userMessageId": user_message_id,
                "assistantMessageId": assistant_message_id,
                "userUniqueMessageId": user_unique_message_id,
                "turnId": turn_id,
                "mode": "create",
                "rewriteOptions": None,
                "attachments": None,
                "mentions": None,
                "clippyIp": None,
                "isNewConversation": is_new_conversation,
                "imagineOperationRequest": imagine_request,
                "qplJoinId": None,
                "clientTimezone": os.getenv("TZ", "Asia/Bangkok"),
                "developerOverridesForMessage": None,
                "clientLatitude": None,
                "clientLongitude": None,
                "devicePixelRatio": None,
                "entryPoint": entry_point,
                "promptSessionId": prompt_session_id,
                "promptType": None,
                "conversationStarterId": None,
                "userAgent": os.getenv("META_USER_AGENT", DEFAULT_UA),
                "currentBranchPath": current_branch_path,
                "promptEditType": "new_message",
                "userLocale": "vi-VN",
                "userEventId": None,
                "requestedToolCall": None,
            },
        }

    def _build_router_state_tree(self, conversation_id: str) -> str:
        return json.dumps(
            [
                "",
                {
                    "children": [
                        "(home)",
                        {
                            "children": [
                                "prompt",
                                {
                                    "children": [
                                        ["id", conversation_id, "d"],
                                        {
                                            "children": [
                                                "__PAGE__",
                                                {},
                                                None,
                                                None,
                                                False,
                                            ]
                                        },
                                        None,
                                        None,
                                        False,
                                    ]
                                },
                                None,
                                None,
                                False,
                            ],
                            "connectors": ["__DEFAULT__", {}, None, None, False],
                            "starters": ["__DEFAULT__", {}, None, None, False],
                            "welcome": ["__DEFAULT__", {}, None, None, False],
                        },
                        None,
                        "refetch",
                        False,
                    ],
                    "modal": ["__DEFAULT__", {}, None, None, False],
                    "sidebar": ["__DEFAULT__", {}, None, None, False],
                },
                None,
                None,
                True,
            ],
            separators=(",", ":"),
        )

    def _stream_generate(self, payload: Dict, timeout_seconds: int) -> Dict:
        response = requests.post(
            GRAPHQL_URL,
            headers=self._generate_headers(),
            json=payload,
            stream=True,
            timeout=(20, timeout_seconds),
        )
        response.raise_for_status()

        events: List[Dict] = []
        raw_lines: List[str] = []
        conversation_id = payload["variables"]["conversationId"]
        complete_seen = False
        start = time.time()

        for line in response.iter_lines(decode_unicode=True):
            if time.time() - start > timeout_seconds:
                break
            if line is None:
                continue
            line = line.strip()
            if not line:
                continue
            raw_lines.append(line)
            if line.startswith("data:"):
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                events.append(obj)
                cid = (
                    obj.get("data", {})
                    .get("sendMessageStream", {})
                    .get("conversationId")
                )
                if cid:
                    conversation_id = cid
            elif line.startswith("event:") and "complete" in line.lower():
                complete_seen = True

        response.close()
        return {
            "conversation_id": conversation_id,
            "events": events,
            "raw_lines": raw_lines,
            "complete_seen": complete_seen,
        }

    def _fetch_prompt_page(self, conversation_id: str, timeout_seconds: int) -> str:
        token_chars = string.ascii_lowercase + string.digits
        responses = []
        for mode in ("prefetch", "full"):
            rsc_token = "".join(random.choice(token_chars) for _ in range(5))
            url = f"{META_BASE}/prompt/{conversation_id}?_rsc={rsc_token}"
            headers = self._prompt_headers(
                conversation_id,
                prefetch=(mode == "prefetch"),
                full_state=(mode == "full"),
            )
            response = requests.get(
                url,
                headers=headers,
                timeout=(20, timeout_seconds),
            )
            response.raise_for_status()
            responses.append(response.text)
        return "\n".join(responses)

    def _unique(self, values: List[str]) -> List[str]:
        seen = set()
        out = []
        for value in values:
            if value not in seen:
                seen.add(value)
                out.append(value)
        return out

    def _extract_image_urls(self, text: str) -> List[str]:
        urls = [match.replace("\\u0026", "&") for match in FBCDN_URL_RE.findall(text)]
        image_like = [u for u in urls if any(ext in u.lower() for ext in [".jpeg", ".jpg", ".png", ".webp"])]
        return self._unique(image_like)

    def _extract_video_urls(self, text: str) -> List[str]:
        urls = [match.replace("\\u0026", "&") for match in FBCDN_URL_RE.findall(text)]
        video_like = [u for u in urls if any(ext in u.lower() for ext in [".mp4", ".mov", ".webm"])]
        create_urls = [m.replace("\\u0026", "&") for m in META_CREATE_URL_RE.findall(text)]
        prompt_urls = [m.replace("\\u0026", "&") for m in META_PROMPT_URL_RE.findall(text)]
        return self._unique(video_like + create_urls + prompt_urls)

    def _extract_access_token(self) -> str:
        env_token = os.getenv("META_AI_ACCESS_TOKEN", "").strip()
        if env_token:
            return env_token
        page_headers = self._common_headers().copy()
        response = requests.get(META_BASE, headers=page_headers, timeout=(20, 60))
        response.raise_for_status()
        match = re.search(r'accessToken\\":\\"(ecto1:[^"\\]+)', response.text)
        if not match:
            raise RuntimeError("Could not extract Meta access token from page HTML")
        return match.group(1)

    def upload_image_file(self, file_bytes: bytes, filename: str, mime_type: str) -> Dict:
        validation_error = self.validate()
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)

        access_token = self._extract_access_token()
        upload_session_id = str(uuid.uuid4())
        safe_name = Path(filename).name or f"upload-{uuid.uuid4().hex[:8]}.jpg"
        headers = {
            "accept": "*/*",
            "accept-language": os.getenv("META_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
            "authorization": f"OAuth {access_token}",
            "desired_upload_handler": "genai_document",
            "ecto_auth_token": "true",
            "is_abra_user": "true",
            "offset": "0",
            "origin": META_BASE,
            "referer": META_BASE + "/",
            "user-agent": os.getenv("META_USER_AGENT", DEFAULT_UA),
            "x-entity-length": str(len(file_bytes)),
            "x-entity-name": safe_name,
            "x-entity-type": mime_type,
        }
        response = requests.post(
            f"https://rupload.meta.ai/gen_ai_document_gen_ai_tenant/{upload_session_id}",
            headers=headers,
            data=file_bytes,
            timeout=(20, 180),
        )
        response.raise_for_status()
        data = response.json()
        media_id = data.get("media_id") or data.get("mediaId") or data.get("id")
        return {
            "success": bool(media_id),
            "source_media_ent_id": str(media_id) if media_id else None,
            "upload_session_id": upload_session_id,
            "file_name": safe_name,
            "file_size": len(file_bytes),
            "mime_type": mime_type,
            "raw_response": data,
            "error": None if media_id else f"Upload succeeded but media_id not found: {data}",
        }

    def _download_file(self, url: str, filename: Optional[str], subdir: str) -> Dict:
        safe_subdir = re.sub(r"[^a-zA-Z0-9_.-]", "_", subdir).strip("._") or "default"
        target_dir = DOWNLOAD_DIR / safe_subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        response = requests.get(
            url,
            stream=True,
            timeout=(20, 180),
            headers={"user-agent": os.getenv("META_USER_AGENT", DEFAULT_UA), "referer": META_BASE + "/"},
        )
        response.raise_for_status()

        derived_name = filename
        if not derived_name:
            path_name = Path(urlparse(url).path).name
            derived_name = path_name or f"media-{uuid.uuid4().hex[:8]}"

        if "." not in derived_name:
            content_type = response.headers.get("content-type", "")
            ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
            derived_name = derived_name + ext

        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", derived_name)
        file_path = target_dir / safe_name

        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    f.write(chunk)

        return {
            "success": True,
            "url": url,
            "file_path": str(file_path),
            "file_name": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "content_type": response.headers.get("content-type", "application/octet-stream"),
        }

    def generate_image(self, prompt: str, orientation: str, timeout_seconds: int) -> Dict:
        validation_error = self.validate()
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)

        payload = self._build_payload(prompt, "TEXT_TO_IMAGE", orientation)
        stream_result = self._stream_generate(payload, timeout_seconds)
        prompt_body = self._fetch_prompt_page(stream_result["conversation_id"], timeout_seconds)
        image_urls = self._extract_image_urls(prompt_body)

        if not image_urls:
            for line in stream_result["raw_lines"]:
                image_urls.extend(self._extract_image_urls(line))
            image_urls = self._unique(image_urls)

        cid = stream_result["conversation_id"]
        try:
            (DEBUG_DIR / f"{cid}-prompt-body.txt").write_text(prompt_body)
            (DEBUG_DIR / f"{cid}-raw-lines.txt").write_text("\n".join(stream_result["raw_lines"]))
            (DEBUG_DIR / f"{cid}-events.json").write_text(json.dumps(stream_result["events"], ensure_ascii=False, indent=2))
        except Exception:
            pass

        return {
            "success": len(image_urls) > 0,
            "conversation_id": cid,
            "image_urls": image_urls,
            "complete_seen": stream_result["complete_seen"],
            "event_count": len(stream_result["events"]),
        }

    def _resolve_video_result(self, payload: Dict, timeout_seconds: int, poll_attempts: int, poll_interval_seconds: int, *, note: str) -> Dict:
        stream_result = self._stream_generate(payload, timeout_seconds)

        video_urls: List[str] = []
        for attempt in range(1, poll_attempts + 1):
            prompt_body = self._fetch_prompt_page(stream_result["conversation_id"], timeout_seconds)
            video_urls = self._extract_video_urls(prompt_body)
            if video_urls:
                break
            if attempt < poll_attempts:
                time.sleep(poll_interval_seconds)

        if not video_urls:
            for line in stream_result["raw_lines"]:
                video_urls.extend(self._extract_video_urls(line))
            video_urls = self._unique(video_urls)

        return {
            "success": len(video_urls) > 0,
            "conversation_id": stream_result["conversation_id"],
            "video_urls": video_urls,
            "complete_seen": stream_result["complete_seen"],
            "event_count": len(stream_result["events"]),
            "poll_attempts_used": poll_attempts,
            "note": note,
        }

    def generate_video(self, prompt: str, timeout_seconds: int, poll_attempts: int, poll_interval_seconds: int) -> Dict:
        validation_error = self.validate()
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)

        payload = self._build_payload(prompt, "TEXT_TO_VIDEO")
        return self._resolve_video_result(
            payload,
            timeout_seconds,
            poll_attempts,
            poll_interval_seconds,
            note="Video flow auto-polls prompt state until mp4 links appear or attempts are exhausted.",
        )

    def generate_image_to_video(
        self,
        source_media_ent_id: str,
        prompt: str,
        source_media_url: Optional[str],
        conversation_id: Optional[str],
        is_new_conversation: bool,
        entry_point: str,
        current_branch_path: Optional[str],
        timeout_seconds: int,
        poll_attempts: int,
        poll_interval_seconds: int,
    ) -> Dict:
        validation_error = self.validate()
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)

        payload = self._build_payload(
            prompt,
            "IMAGE_TO_VIDEO",
            source_media_ent_id=source_media_ent_id,
            source_media_url=source_media_url,
            conversation_id=conversation_id,
            is_new_conversation=is_new_conversation,
            entry_point=entry_point,
            current_branch_path=current_branch_path,
        )
        return self._resolve_video_result(
            payload,
            timeout_seconds,
            poll_attempts,
            poll_interval_seconds,
            note="Image-to-video flow auto-polls prompt state until mp4 links appear or attempts are exhausted.",
        )

    def download_media(self, url: str, filename: Optional[str], subdir: str) -> Dict:
        return self._download_file(url, filename, subdir)

    def batch_download_media(self, urls: List[str], subdir: str, prefix: str) -> Dict:
        results = []
        for idx, url in enumerate(urls, start=1):
            ext = Path(urlparse(url).path).suffix
            filename = f"{prefix}-{idx}{ext}" if ext else f"{prefix}-{idx}"
            try:
                results.append(self._download_file(url, filename, subdir))
            except Exception as exc:
                results.append({"success": False, "url": url, "error": str(exc)})
        return {"success": any(item.get("success") for item in results), "results": results}


bridge = MetaBridge()


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    validation_error = bridge.validate()
    return {
        "status": "ok",
        "cookies": "ready" if not validation_error else f"incomplete: {validation_error}",
        "download_dir": str(DOWNLOAD_DIR),
    }


@app.post("/image")
def image(body: ImageRequest) -> Dict:
    return bridge.generate_image(body.prompt, body.orientation, body.timeout_seconds)


@app.post("/video")
def video(body: VideoRequest) -> Dict:
    return bridge.generate_video(
        body.prompt,
        body.timeout_seconds,
        body.poll_attempts,
        body.poll_interval_seconds,
    )


@app.post("/image-to-video")
def image_to_video(body: ImageToVideoRequest) -> Dict:
    return bridge.generate_image_to_video(
        body.source_media_ent_id,
        body.prompt,
        body.source_media_url,
        body.conversation_id,
        body.is_new_conversation,
        body.entry_point,
        body.current_branch_path,
        body.timeout_seconds,
        body.poll_attempts,
        body.poll_interval_seconds,
    )


@app.post("/upload")
async def upload(file: UploadFile = File(...)) -> Dict:
    content = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    try:
        return bridge.upload_image_file(content, file.filename or "upload.bin", mime_type)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/download")
def download(body: DownloadRequest) -> Dict:
    try:
        return bridge.download_media(body.url, body.filename, body.subdir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/download/batch")
def download_batch(body: BatchDownloadRequest) -> Dict:
    return bridge.batch_download_media(body.urls, body.subdir, body.prefix)


@app.post("/image-to-video/download")
def image_to_video_download(body: ImageToVideoDownloadRequest) -> Dict:
    result = bridge.generate_image_to_video(
        body.source_media_ent_id,
        body.prompt,
        body.source_media_url,
        body.conversation_id,
        body.is_new_conversation,
        body.entry_point,
        body.current_branch_path,
        body.timeout_seconds,
        body.poll_attempts,
        body.poll_interval_seconds,
    )
    urls = result.get("video_urls", [])
    download_result = bridge.batch_download_media(urls, body.subdir, body.filename_prefix)
    return {
        **result,
        "download": download_result,
    }
